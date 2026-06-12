"""Scoped todo bridge: `share`/`unshare` are WRITE actions.

The /api/codex/todos POST route picks its required scope by checking the
normalized action against WRITE_ACTIONS. `share`/`unshare` mutate the note's
ACL, so they must demand `todos:write` — before this gate a token holding only
`todos:read` could share a private note to another account or revoke existing
collaborators (PR #2940 review, P1).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.codex_routes as cr


def _client(monkeypatch, scopes, calls):
    """App with the codex router, a fake token-auth middleware, and
    do_manage_notes stubbed out (we only test the scope gate)."""

    async def _fake_manage_notes(content, owner=None):
        calls.append(owner)
        return {"ok": True}

    monkeypatch.setattr(cr, "do_manage_notes", _fake_manage_notes)

    app = FastAPI()

    @app.middleware("http")
    async def _token_state(request, call_next):
        request.state.current_user = "api"
        request.state.api_token = True
        request.state.api_token_scopes = list(scopes)
        request.state.api_token_owner = "alice"
        return await call_next(request)

    app.include_router(cr.setup_codex_routes())
    return TestClient(app)


@pytest.mark.parametrize("action", ["share", "unshare"])
def test_read_scope_cannot_share_or_unshare(monkeypatch, action):
    calls = []
    client = _client(monkeypatch, ["todos:read"], calls)
    r = client.post("/api/codex/todos", json={"action": action, "id": "n1", "username": "bob"})
    assert r.status_code == 403
    assert calls == [], "a todos:read token must never reach do_manage_notes for ACL actions"


@pytest.mark.parametrize("action", ["share", "unshare"])
def test_write_scope_can_share_and_unshare(monkeypatch, action):
    calls = []
    client = _client(monkeypatch, ["todos:write"], calls)
    r = client.post("/api/codex/todos", json={"action": action, "id": "n1", "username": "bob"})
    assert r.status_code == 200
    assert calls == ["alice"], "write-scoped calls run as the token owner"


def test_read_scope_still_reads(monkeypatch):
    calls = []
    client = _client(monkeypatch, ["todos:read"], calls)
    r = client.post("/api/codex/todos", json={"action": "get", "id": "n1"})
    assert r.status_code == 200
    assert calls == ["alice"]


def test_write_actions_cover_acl_mutations():
    # Belt-and-braces: if someone prunes WRITE_ACTIONS the parametrized route
    # tests above fail too, but this names the contract directly.
    assert {"share", "unshare"} <= cr.WRITE_ACTIONS
