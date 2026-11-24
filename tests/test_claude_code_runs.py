"""Claude Code runs: Odysseus hands a task to the user's own Claude Code CLI.

A fake ``claude`` script stands in for the real binary. It records its argv,
environment and stdin, then replays stream-json lines, so these tests check
the process contract (flags, isolation, resume, teardown) without a Claude
account or network.
"""

import asyncio
import contextlib
import json
import os
import signal
import stat
import sys
import textwrap
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import claude_code_runner as runner

FAKE_CLI = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys, time
    record = os.environ["FAKE_RECORD"]
    data = sys.stdin.readline()
    with open(record, "a") as fh:
        fh.write(json.dumps({{"argv": sys.argv[1:], "env": dict(os.environ), "stdin": data, "cwd": os.getcwd()}}) + "\\n")
    mode = os.environ.get("FAKE_MODE", "ok")
    if sys.argv[1:3] == ["auth", "status"]:
        print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}}))
        sys.exit(0)
    if mode == "hang":
        child = os.fork()
        if child == 0:
            time.sleep(60)
            os._exit(0)
        with open(record + ".pids", "w") as fh:
            fh.write(f"{{os.getpid()}} {{child}}")
        time.sleep(60)
    if mode == "crash":
        sys.stderr.write("boom: not logged in\\n")
        sys.exit(3)
    resumed = "--resume" in sys.argv
    sid = "sess-2" if resumed else "sess-1"
    lines = [
        {{"type": "system", "subtype": "init", "session_id": sid, "model": "claude-sonnet-5-5",
          "permissionMode": "acceptEdits", "apiKeySource": "none", "tools": ["Read", "Edit", "Bash"]}},
        {{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": "Hel"}}}}}},
        {{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": "lo"}}}}}},
        {{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "name": "Bash", "input": {{"command": "ls -la"}}}}]}}}},
        {{"type": "result", "subtype": "error_during_execution" if mode == "resume_error" else "success",
          "is_error": mode == "resume_error", "result": "done", "session_id": sid, "num_turns": 2,
          "usage": {{"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7}}}},
    ]
    for line in lines:
        print(json.dumps(line), flush=True)
    """
)


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    path = tmp_path / "claude"
    path.write_text(FAKE_CLI.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("ODYSSEUS_CLAUDE_CODE_PATH", str(path))
    monkeypatch.setattr(runner, "CLAUDE_CODE_DIR", str(tmp_path / "claude_code"))
    monkeypatch.setattr(runner, "CLAUDE_CODE_SKILL_DIR", str(tmp_path / "no-skill"))
    monkeypatch.setattr(runner, "_PASSTHROUGH_ENV", runner._PASSTHROUGH_ENV + ("FAKE_RECORD", "FAKE_MODE"))
    monkeypatch.setenv("FAKE_RECORD", str(record))
    monkeypatch.setattr(runner, "TERM_GRACE_S", 0.5)
    runner._RUN_SLOTS.clear()
    runner._OWNER_LOCKS.clear()

    def calls():
        if not record.exists():
            return []
        return [json.loads(line) for line in record.read_text().splitlines()]

    return {"path": path, "calls": calls, "record": record}


def _collect(req):
    async def go():
        return [event async for event in runner.run(req)]

    return asyncio.run(go())


# ── Process contract ────────────────────────────────────────────────────────


def test_args_match_headless_stream_json_and_refuse_bypass():
    args = runner.build_args(mode="acceptEdits", model="claude-sonnet-5-5", effort="high", resume="abc")
    assert args[:2] == ["-p", "--output-format"]
    assert ["--input-format", "stream-json"] == args[3:5]
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert args[args.index("--resume") + 1] == "abc"
    assert args[args.index("--effort") + 1] == "high"
    assert "--disallowedTools" in args
    assert "--effort" not in runner.build_args(mode="plan", effort="turbo")
    with pytest.raises(runner.ClaudeCodeRunError):
        runner.permission_mode("bypassPermissions")


def test_env_is_allowlisted_and_per_user(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-key")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///secret.db")
    monkeypatch.setenv("CLAUDECODE", "1")
    alice = runner.user_home("alice", base=str(tmp_path))
    bob = runner.user_home("bob", base=str(tmp_path))
    env = runner.build_env(alice, {"ODYSSEUS_URL": "http://127.0.0.1:1"})
    assert "ANTHROPIC_API_KEY" not in env
    assert "DATABASE_URL" not in env
    assert "CLAUDECODE" not in env
    assert env["CLAUDE_CONFIG_DIR"] == str(alice.config_dir)
    assert env["HOME"] == str(alice.root)
    assert env["ODYSSEUS_URL"] == "http://127.0.0.1:1"
    assert alice.config_dir != bob.config_dir
    assert stat.S_IMODE(os.stat(alice.config_dir).st_mode) == 0o700
    # Path-like usernames cannot escape the per-user root.
    evil = runner.user_home("../../etc", base=str(tmp_path))
    assert evil.root.parent == tmp_path


def test_parse_line_normalizes_stream_json():
    assert runner.parse_line("not json") == []
    init = runner.parse_line(json.dumps({"type": "system", "subtype": "init", "session_id": "s", "tools": ["a", "b"]}))
    assert init == [{"type": "init", "session_id": "s", "model": None, "permission_mode": None,
                     "api_key_source": None, "tools": 2}]
    tool = runner.parse_line(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "x"}, {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}]}}))
    assert tool == [{"type": "tool", "name": "Edit", "summary": "a.py"}]


# ── Runs ────────────────────────────────────────────────────────────────────


def test_run_streams_events_in_user_workspace(fake_cli):
    events = _collect(runner.RunRequest(owner="alice", prompt="say hello", extra_env={"ODYSSEUS_URL": "http://x"}))
    kinds = [e["type"] for e in events]
    assert kinds == ["init", "text", "text", "tool", "result"]
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Hello"
    assert events[-1]["usage"] == {"input_tokens": 15, "output_tokens": 7}
    call = fake_cli["calls"]()[0]
    assert json.loads(call["stdin"])["message"]["content"][0]["text"] == "say hello"
    home = runner.user_home("alice")
    assert call["cwd"] == str(home.workspace)
    assert call["env"]["CLAUDE_CONFIG_DIR"] == str(home.config_dir)
    assert call["env"]["ODYSSEUS_URL"] == "http://x"


def test_session_key_resumes_and_a_failed_resume_is_forgotten(fake_cli, monkeypatch):
    _collect(runner.RunRequest(owner="alice", prompt="one", session_key="chat-1"))
    assert runner._load_sessions(runner.user_home("alice")) == {"chat-1": "sess-1"}
    _collect(runner.RunRequest(owner="alice", prompt="two", session_key="chat-1"))
    second = fake_cli["calls"]()[1]["argv"]
    assert second[second.index("--resume") + 1] == "sess-1"
    assert runner._load_sessions(runner.user_home("alice")) == {"chat-1": "sess-2"}

    monkeypatch.setenv("FAKE_MODE", "resume_error")
    _collect(runner.RunRequest(owner="alice", prompt="three", session_key="chat-1"))
    assert runner._load_sessions(runner.user_home("alice")) == {}
    # Another user's run never sees alice's sessions.
    assert runner._load_sessions(runner.user_home("bob")) == {}


def test_crash_without_result_reports_stderr(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "crash")
    events = _collect(runner.RunRequest(owner=None, prompt="x"))
    assert events == [{"type": "error", "status": 502, "message": "boom: not logged in"}]


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_closing_the_stream_kills_the_whole_process_group(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    pids_file = fake_cli["record"].with_name("record.jsonl.pids")

    async def go():
        gen = runner.run(runner.RunRequest(owner="alice", prompt="x"))
        task = asyncio.ensure_future(gen.__anext__())
        for _ in range(100):
            if pids_file.exists() and pids_file.read_text().strip():
                break
            await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
            await task
        await gen.aclose()

    asyncio.run(go())
    leader, child = (int(p) for p in pids_file.read_text().split())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and (_alive(leader) or _alive(child)):
        time.sleep(0.05)
    assert not _alive(child), "grandchild survived the run"
    # Leader is our child; reaped by the runner, so it is gone entirely.
    assert not _alive(leader)


def test_timeout_is_a_504_and_ends_the_cli(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    events = _collect(runner.RunRequest(owner="alice", prompt="x", timeout=0.5))
    assert events == [{"type": "error", "status": 504, "message": "Claude Code run exceeded 0.5 s."}]
    leader, child = (int(p) for p in fake_cli["record"].with_name("record.jsonl.pids").read_text().split())
    time.sleep(0.2)
    assert not _alive(child)


def test_missing_cli_is_a_503(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_CLAUDE_CODE_PATH", str(tmp_path / "nope"))
    with pytest.raises(runner.ClaudeCodeRunError) as exc:
        _collect(runner.RunRequest(owner=None, prompt="x"))
    assert exc.value.status == 503


def test_skill_is_installed_under_the_users_claude_dir(tmp_path):
    skill = tmp_path / "odysseus"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: odysseus\n---\n")
    (skill / "scripts" / "odysseus_api.py").write_text("print(1)\n")
    home = runner.user_home("alice", base=str(tmp_path / "cc"))
    runner._sync_skill(home, str(skill))
    assert (home.root / ".claude" / "skills" / "odysseus" / "scripts" / "odysseus_api.py").exists()


# ── Routes ──────────────────────────────────────────────────────────────────


class _FakeQuery:
    def __init__(self, store):
        self.store = store

    def filter(self, *conds):
        return self

    def delete(self):
        self.store["deleted"] += 1


class _FakeDb:
    def __init__(self, store):
        self.store = store

    def add(self, row):
        self.store["added"].append(row)

    def query(self, _model):
        return _FakeQuery(self.store)


@pytest.fixture
def app_client(fake_cli, monkeypatch):
    from routes import claude_code_routes as routes

    store = {"added": [], "deleted": 0, "invalidated": 0}

    @contextlib.contextmanager
    def fake_session():
        yield _FakeDb(store)

    monkeypatch.setattr(routes, "get_db_session", fake_session)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = FastAPI()
    app.state.invalidate_token_cache = lambda: store.__setitem__("invalidated", store["invalidated"] + 1)
    app.include_router(routes.setup_claude_code_routes())
    return TestClient(app), store, routes


def _sse_events(text):
    out = []
    for chunk in text.split("\n\n"):
        if chunk.startswith("data: ") and chunk != "data: [DONE]":
            out.append(json.loads(chunk[len("data: "):]))
    return out


def test_route_streams_a_run(app_client):
    client, _store, _routes = app_client
    resp = client.post("/api/claude-code/runs", json={"prompt": "hello", "session_key": "s1"})
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    assert [e["type"] for e in events] == ["init", "text", "text", "tool", "result"]
    assert resp.text.rstrip().endswith("data: [DONE]")
    assert client.get("/api/claude-code/sessions").json() == {"sessions": ["s1"]}


def test_route_status_reports_login_without_a_request(app_client):
    client, _store, _routes = app_client
    body = client.get("/api/claude-code/status").json()
    assert body["installed"] is True and body["logged_in"] is True
    assert body["login_command"] == "python scripts/claude_code_login.py"


def test_route_mints_a_scoped_token_and_deletes_it_after_the_run(app_client, monkeypatch, fake_cli):
    client, store, routes = app_client
    monkeypatch.setattr(routes, "_caller", lambda request: "alice")
    monkeypatch.setattr(routes, "_validate_self_scopes", lambda request, user, scopes: None)
    resp = client.post("/api/claude-code/runs", json={"prompt": "list my todos", "scopes": ["todos:write"]})
    assert resp.status_code == 200
    row = store["added"][0]
    assert row.owner == "alice"
    assert row.scopes == "todos:read,todos:write"
    assert store["deleted"] == 1 and store["invalidated"] == 2
    call = fake_cli["calls"]()[-1]
    assert call["env"]["ODYSSEUS_API_TOKEN"].startswith("ody_")
    argv = call["argv"]
    assert argv[argv.index("--allowedTools") + 1] == f"Bash({routes.SKILL_COMMAND} *)"


def test_route_refuses_scopes_without_auth_and_bearer_callers(app_client, monkeypatch):
    client, _store, routes = app_client
    resp = client.post("/api/claude-code/runs", json={"prompt": "x", "scopes": ["todos:read"]})
    assert resp.status_code == 400
    monkeypatch.setattr(routes, "is_delegated_credential", lambda request: True)
    assert client.post("/api/claude-code/runs", json={"prompt": "x"}).status_code == 403
    assert client.get("/api/claude-code/status").status_code == 403


def test_route_refuses_bypass_permissions(app_client):
    client, _store, _routes = app_client
    resp = client.post("/api/claude-code/runs", json={"prompt": "x", "permission_mode": "bypassPermissions"})
    assert resp.status_code == 400
