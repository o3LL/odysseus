"""Claude Code runs: start a task in the calling user's own Claude Code.

Odysseus never offers a Claude sign-in. Each user signs in once, with Claude
Code's own ``claude auth login``, inside their private config dir
(``scripts/claude_code_login.py`` runs it there). These routes only report that
login, start runs, and stream what Claude Code does back as server-sent events.

A run can be given a short-lived Odysseus API token so Claude Code can reach
the user's Odysseus data through the bundled ``odysseus`` skill and the
scope-gated ``/api/codex/*`` API. The token carries only the scopes the caller
asked for, belongs to the caller, and is deleted when the run ends.
"""

import json
import logging
import os
import secrets
import uuid
from typing import Any, Dict, List, Optional

import bcrypt
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

from core.database import ApiToken, get_db_session
from routes.api_token_routes import _normalize_scopes, _validate_self_scopes
from src import claude_code_runner as runner
from src.auth_helpers import is_delegated_credential, require_user
from src.constants import internal_api_base
from src.owner_identity import INTERNAL_TOOL_USER, auth_disabled

logger = logging.getLogger(__name__)

USERS_ENV = "ODYSSEUS_CLAUDE_CODE_USERS"
SKILL_COMMAND = "python3 ~/.claude/skills/odysseus/scripts/odysseus_api.py"
MAX_PROMPT_CHARS = 100_000


def _caller(request: Request) -> Optional[str]:
    """The human starting a run. Bearer-token callers (including a Claude Code
    run's own token) and loopback tools cannot start runs."""
    if is_delegated_credential(request) or getattr(request.state, "current_user", None) == INTERNAL_TOOL_USER:
        raise HTTPException(403, "Claude Code runs need a signed-in Odysseus user.")
    if auth_disabled():
        return None
    user = require_user(request)
    if not user:
        raise HTTPException(401, "Sign in to Odysseus first.")
    if (os.getenv(USERS_ENV) or "admins").strip().lower() != "all":
        auth_mgr = getattr(request.app.state, "auth_manager", None)
        if not auth_mgr or not auth_mgr.is_admin(user):
            raise HTTPException(403, f"Claude Code runs are limited to admins. Set {USERS_ENV}=all to open them to every user.")
    return user


def _login_hint(owner: Optional[str]) -> str:
    return "python scripts/claude_code_login.py" + (f" --user {owner}" if owner else "")


def _mint_run_token(request: Request, owner: str, scopes: List[str]) -> tuple[str, str]:
    raw = "ody_" + secrets.token_urlsafe(32)
    token_id = str(uuid.uuid4())[:8]
    with get_db_session() as db:
        db.add(ApiToken(
            id=token_id,
            owner=owner,
            name=f"Claude Code run {token_id}",
            token_hash=bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode(),
            token_prefix=raw[:8],
            scopes=",".join(scopes),
            is_active=True,
        ))
    _invalidate_tokens(request)
    return token_id, raw


def _revoke_run_token(request: Request, token_id: str) -> None:
    try:
        with get_db_session() as db:
            db.query(ApiToken).filter(ApiToken.id == token_id).delete()
    except Exception:
        logger.warning("Could not delete Claude Code run token %s", token_id, exc_info=True)
    _invalidate_tokens(request)


def _invalidate_tokens(request: Request) -> None:
    invalidator = getattr(request.app.state, "invalidate_token_cache", None)
    if invalidator:
        try:
            invalidator()
        except Exception:
            pass


def _sse(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def setup_claude_code_routes() -> APIRouter:
    router = APIRouter(prefix="/api/claude-code", tags=["claude-code"])

    @router.get("/status")
    async def status(request: Request):
        owner = _caller(request)
        info = await runner.auth_status(owner)
        info["login_command"] = _login_hint(owner)
        info["permission_mode"] = os.getenv(runner.PERMISSION_MODE_ENV) or runner.DEFAULT_PERMISSION_MODE
        return info

    @router.get("/sessions")
    def sessions(request: Request):
        owner = _caller(request)
        home = runner.user_home(owner)
        return {"sessions": sorted(runner._load_sessions(home))}

    @router.delete("/sessions/{key}")
    def forget_session(request: Request, key: str):
        owner = _caller(request)
        runner._save_session(runner.user_home(owner), key, None)
        return {"status": "forgotten"}

    @router.post("/runs")
    async def start_run(request: Request, payload: Dict[str, Any] = Body(...)):
        owner = _caller(request)
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "A task is required.")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise HTTPException(413, "The task is too long.")
        scopes_in = payload.get("scopes") or []
        if not isinstance(scopes_in, list):
            raise HTTPException(400, "scopes must be a list.")
        scopes = _normalize_scopes(scopes_in) if scopes_in else []
        if scopes and owner is None:
            raise HTTPException(400, "Odysseus data access needs AUTH_ENABLED=true (tokens belong to a user).")
        if scopes:
            _validate_self_scopes(request, owner, scopes)
        try:
            mode = runner.permission_mode(payload.get("permission_mode"))
        except runner.ClaudeCodeRunError as exc:
            raise HTTPException(exc.status, exc.message)
        if not runner.find_cli():
            raise HTTPException(503, runner.NOT_INSTALLED)

        token_id, extra_env, allowed = None, {"ODYSSEUS_URL": internal_api_base()}, []
        if scopes:
            token_id, raw = _mint_run_token(request, owner, scopes)
            extra_env["ODYSSEUS_API_TOKEN"] = raw
            allowed = [f"Bash({SKILL_COMMAND} *)"]

        run_req = runner.RunRequest(
            owner=owner,
            prompt=prompt,
            session_key=str(payload.get("session_key") or "") or None,
            model=str(payload.get("model") or "") or None,
            effort=str(payload.get("effort") or "") or None,
            permission_mode=mode,
            extra_env=extra_env,
            allowed_tools=allowed,
        )

        async def events():
            try:
                async for event in runner.run(run_req):
                    yield _sse(event)
            except runner.ClaudeCodeRunError as exc:
                yield _sse({"type": "error", "status": exc.status, "message": exc.message})
            finally:
                if token_id:
                    _revoke_run_token(request, token_id)
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return router
