"""Claude Code runs: hand a task to the user's own, unmodified Claude Code.

This is the orchestrator shape, not a model provider. Odysseus does not use
Claude Code as a completion endpoint for its own agent loop. It starts the
``claude`` binary the operator installed, gives it a task, a working directory
and (optionally) a scoped Odysseus API token, and streams back what Claude Code
does. Claude Code runs its own loop with its own tools and its own system
prompt, exactly as published.

Accounts: every Odysseus user gets a private ``CLAUDE_CONFIG_DIR`` under
``CLAUDE_CODE_DIR``. They sign in there with Claude Code's own
``claude auth login`` (see ``scripts/claude_code_login.py``), so each user runs
on their own Claude subscription or their own Console API key, whichever they
pick in Anthropic's flow. Odysseus never reads, stores or forwards that
credential, and it does not restrict which sign-in method the binary offers.

Environment: the child gets a minimal allowlisted environment, never the
server's own. In particular the server's ``ANTHROPIC_API_KEY`` (used by the
Anthropic model provider) is not passed through, so one account's key is never
billed for another user's run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from src import process_lifecycle
from src.constants import CLAUDE_CODE_DIR, CLAUDE_CODE_SKILL_DIR

logger = logging.getLogger(__name__)

CLI_PATH_ENV = "ODYSSEUS_CLAUDE_CODE_PATH"
PERMISSION_MODE_ENV = "ODYSSEUS_CLAUDE_CODE_PERMISSION_MODE"
MAX_RUNS_ENV = "ODYSSEUS_CLAUDE_CODE_MAX_RUNS"
TIMEOUT_ENV = "ODYSSEUS_CLAUDE_CODE_TIMEOUT"

DEFAULT_PERMISSION_MODE = "acceptEdits"
# bypassPermissions is refused on purpose: the run executes on the Odysseus
# server, not on the user's own laptop, so an unattended "yes to everything"
# would hand the server's shell to whoever can start a run.
PERMISSION_MODES = frozenset({"acceptEdits", "auto", "dontAsk", "plan"})
EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
DEFAULT_TIMEOUT_S = 1800.0
DEFAULT_MAX_RUNS = 2
TERM_GRACE_S = 5.0
STATUS_TIMEOUT_S = 15.0

# Only these variables reach the child. Everything else in the server's
# environment (database URLs, app keys, the server's own provider API keys)
# stays out.
_PASSTHROUGH_ENV = (
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", "USER", "LOGNAME", "SHELL",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
)

NOT_INSTALLED = (
    "Claude Code is not installed on the Odysseus server. Install it "
    "(https://code.claude.com/docs/en/setup) or set ODYSSEUS_CLAUDE_CODE_PATH."
)


class ClaudeCodeRunError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ── Per-user layout ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UserHome:
    root: Path
    config_dir: Path
    workspace: Path
    tmp: Path
    sessions_file: Path


def _owner_slug(owner: Optional[str]) -> str:
    name = (owner or "").strip() or "local"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:40].strip("._") or "user"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return f"{safe}-{digest}"


def user_home(owner: Optional[str], *, base: Optional[str] = None, create: bool = True) -> UserHome:
    root = Path(base or CLAUDE_CODE_DIR) / _owner_slug(owner)
    home = UserHome(
        root=root,
        config_dir=root / ".claude",  # HOME/.claude, so the skill's ~/.claude paths resolve
        workspace=root / "workspace",
        tmp=root / "tmp",
        sessions_file=root / "sessions.json",
    )
    if create:
        for path in (root, home.config_dir, home.workspace, home.tmp):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)
    return home


def _load_sessions(home: UserHome) -> Dict[str, str]:
    try:
        data = json.loads(home.sessions_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def _save_session(home: UserHome, key: str, session_id: Optional[str]) -> None:
    sessions = _load_sessions(home)
    if session_id:
        sessions[key] = session_id
    else:
        sessions.pop(key, None)
    tmp = home.sessions_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, home.sessions_file)


def _sync_skill(home: UserHome, skill_src: Optional[str] = None) -> None:
    """Install the bundled ``odysseus`` skill as a user-level Claude Code skill."""
    src = Path(skill_src or CLAUDE_CODE_SKILL_DIR)
    if not src.is_dir():
        return
    dest = home.config_dir / "skills" / src.name
    shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


# ── CLI ─────────────────────────────────────────────────────────────────────


def find_cli() -> Optional[str]:
    configured = (os.getenv(CLI_PATH_ENV) or "").strip()
    if configured:
        return configured if os.path.isfile(configured) and os.access(configured, os.X_OK) else None
    return shutil.which("claude")


def permission_mode(requested: Optional[str] = None) -> str:
    mode = (requested or os.getenv(PERMISSION_MODE_ENV) or DEFAULT_PERMISSION_MODE).strip()
    if mode not in PERMISSION_MODES:
        raise ClaudeCodeRunError(400, f"Unsupported permission mode {mode!r}; use one of {sorted(PERMISSION_MODES)}.")
    return mode


def build_args(
    *,
    mode: str,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    resume: Optional[str] = None,
    allowed_tools: Optional[List[str]] = None,
) -> List[str]:
    args = [
        "-p",
        "--output-format", "stream-json",
        "--input-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--permission-mode", mode,
        # Interactive questions have nowhere to render in a headless run.
        "--disallowedTools", "AskUserQuestion",
    ]
    if allowed_tools:
        args += ["--allowedTools", ",".join(allowed_tools)]
    if model:
        args += ["--model", model]
    if effort in EFFORT_LEVELS:
        args += ["--effort", effort]
    if resume:
        args += ["--resume", resume]
    return args


def build_env(home: UserHome, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = {key: os.environ[key] for key in _PASSTHROUGH_ENV if key in os.environ}
    env.update({
        "HOME": str(home.root),
        "CLAUDE_CONFIG_DIR": str(home.config_dir),
        "TMPDIR": str(home.tmp),
        "CLAUDE_CODE_TMPDIR": str(home.tmp),
        "CLAUDE_AGENT_SDK_CLIENT_APP": "odysseus",
    })
    env.update(extra or {})
    return env


def build_input(prompt: str) -> bytes:
    message = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
    }
    return (json.dumps(message) + "\n").encode("utf-8")


def _summarize_tool_input(name: str, raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return ""


def parse_line(line: str) -> List[Dict[str, Any]]:
    """One stream-json line from the CLI -> zero or more normalized events."""
    try:
        msg = json.loads(line)
    except ValueError:
        return []
    if not isinstance(msg, dict):
        return []
    kind = msg.get("type")
    if kind == "system" and msg.get("subtype") == "init":
        return [{
            "type": "init",
            "session_id": msg.get("session_id"),
            "model": msg.get("model"),
            "permission_mode": msg.get("permissionMode"),
            "api_key_source": msg.get("apiKeySource"),
            "tools": len(msg.get("tools") or []),
        }]
    if kind == "stream_event":
        event = msg.get("event") or {}
        delta = event.get("delta") or {}
        if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta" and delta.get("text"):
            return [{"type": "text", "text": delta["text"]}]
        return []
    if kind == "assistant":
        out = []
        for block in (msg.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                out.append({"type": "tool", "name": name, "summary": _summarize_tool_input(name, block.get("input"))})
        return out
    if kind == "result":
        usage = msg.get("usage") or {}
        return [{
            "type": "result",
            "is_error": bool(msg.get("is_error")),
            "subtype": msg.get("subtype"),
            "result": msg.get("result") if isinstance(msg.get("result"), str) else "",
            "session_id": msg.get("session_id"),
            "num_turns": msg.get("num_turns"),
            "duration_ms": msg.get("duration_ms"),
            "usage": {
                "input_tokens": int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
            },
        }]
    return []


async def auth_status(owner: Optional[str], *, cli: Optional[str] = None) -> Dict[str, Any]:
    """``claude auth status --json`` for this user's own config dir. No API request."""
    cli = cli or find_cli()
    if not cli:
        return {"installed": False, "logged_in": False, "error": NOT_INSTALLED}
    home = user_home(owner)
    proc = await asyncio.create_subprocess_exec(
        cli, "auth", "status", "--json",
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=build_env(home), cwd=str(home.workspace), start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), STATUS_TIMEOUT_S)
    except asyncio.TimeoutError:
        await _terminate(proc)
        return {"installed": True, "logged_in": False, "error": "claude auth status timed out"}
    try:
        info = json.loads(out.decode("utf-8", "replace") or "{}")
    except ValueError:
        info = {}
    return {
        "installed": True,
        "logged_in": bool(info.get("loggedIn")),
        "auth_method": info.get("authMethod"),
        "api_provider": info.get("apiProvider"),
        "config_dir": str(home.config_dir),
    }


# ── Runs ────────────────────────────────────────────────────────────────────

_RUN_SLOTS: Dict[int, asyncio.Semaphore] = {}
_OWNER_LOCKS: Dict[str, asyncio.Lock] = {}


def _slots() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    if loop_id not in _RUN_SLOTS:
        _RUN_SLOTS[loop_id] = asyncio.Semaphore(max(1, int(os.getenv(MAX_RUNS_ENV) or DEFAULT_MAX_RUNS)))
    return _RUN_SLOTS[loop_id]


def _owner_lock(owner: Optional[str]) -> asyncio.Lock:
    key = _owner_slug(owner)
    if key not in _OWNER_LOCKS:
        _OWNER_LOCKS[key] = asyncio.Lock()
    return _OWNER_LOCKS[key]


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """TERM the whole process group, then KILL, and reap. Never raises."""
    if proc.returncode is not None:
        return
    pid = proc.pid
    pgid = process_lifecycle.pgid_of(pid)
    try:
        await process_lifecycle.escalate_async(
            lambda: process_lifecycle.tree_gone(pid, pgid) or proc.returncode is not None,
            lambda sig: process_lifecycle.signal_group(pid, pgid, sig),
            steps=process_lifecycle.term_kill_steps(TERM_GRACE_S),
            wait=proc.wait,
        )
    except Exception:  # pragma: no cover - teardown must not mask the real error
        logger.warning("Claude Code run teardown failed", exc_info=True)


@dataclass
class RunRequest:
    owner: Optional[str]
    prompt: str
    session_key: Optional[str] = None
    model: Optional[str] = None
    effort: Optional[str] = None
    permission_mode: Optional[str] = None
    extra_env: Dict[str, str] = field(default_factory=dict)
    allowed_tools: List[str] = field(default_factory=list)
    timeout: Optional[float] = None


def _run_timeout(requested: Optional[float]) -> float:
    if requested and requested > 0:
        return float(requested)
    try:
        return float(os.getenv(TIMEOUT_ENV) or DEFAULT_TIMEOUT_S)
    except ValueError:
        return DEFAULT_TIMEOUT_S


async def run(req: RunRequest, *, cli: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
    """Run one task through the user's Claude Code and yield normalized events.

    Yields ``init``, ``text``, ``tool`` and finally ``result`` (or ``error``).
    Closing the generator early (client disconnect, Stop) ends the CLI's whole
    process group.
    """
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise ClaudeCodeRunError(400, "A task is required.")
    cli = cli or find_cli()
    if not cli:
        raise ClaudeCodeRunError(503, NOT_INSTALLED)
    mode = permission_mode(req.permission_mode)
    home = user_home(req.owner)
    _sync_skill(home)
    key = (req.session_key or "").strip()[:200]
    resume = _load_sessions(home).get(key) if key else None
    args = build_args(mode=mode, model=req.model, effort=req.effort, resume=resume, allowed_tools=req.allowed_tools)
    limit = _run_timeout(req.timeout)

    async with _owner_lock(req.owner), _slots():
        proc = await asyncio.create_subprocess_exec(
            cli, *args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=build_env(home, req.extra_env), cwd=str(home.workspace),
            start_new_session=True, limit=16 * 1024 * 1024,
        )
        stderr_tail: List[bytes] = []

        async def _drain_stderr() -> None:
            assert proc.stderr is not None
            async for chunk in proc.stderr:
                stderr_tail.append(chunk)
                del stderr_tail[:-20]

        stderr_task = asyncio.create_task(_drain_stderr())
        got_result = False
        try:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(build_input(prompt))
            await proc.stdin.drain()
            proc.stdin.close()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + limit
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    yield {"type": "error", "status": 504, "message": f"Claude Code run exceeded {limit:g} s."}
                    return
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), remaining)
                except asyncio.TimeoutError:
                    continue
                if not line:
                    break
                for event in parse_line(line.decode("utf-8", "replace")):
                    if event["type"] == "result":
                        got_result = True
                        if key and not event["is_error"] and event.get("session_id"):
                            _save_session(home, key, event["session_id"])
                        elif key and event["is_error"] and resume:
                            # Drop a session the CLI could not continue so the
                            # next run starts fresh instead of failing forever.
                            _save_session(home, key, None)
                    yield event
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), TERM_GRACE_S)
            if not got_result:
                tail = b"".join(stderr_tail).decode("utf-8", "replace").strip()[-500:]
                yield {"type": "error", "status": 502, "message": tail or f"Claude Code exited with {proc.returncode}."}
        finally:
            await _terminate(proc)
            stderr_task.cancel()
