"""Toast history must be stored for the operator in auth-disabled mode.

With AUTH_ENABLED=false the auth middleware is not installed, so no request
carries ``current_user``. ``POST /api/tasks/notification-logs`` used to answer
401 in that mode, and the front-end fetch wrapper turned that 401 into a
navigation to /login, so every toast reloaded the page.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
import routes.task_routes as task_routes
from src.owner_identity import DEFAULT_LOCAL_OWNER


def _endpoint(router, method):
    return next(
        route.endpoint
        for route in router.routes
        if route.path.endswith("/notification-logs") and method in route.methods
    )


@pytest.fixture
def logs_env(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tasks.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(task_routes, "SessionLocal", session_factory)

    scheduler = MagicMock()
    scheduler.ensure_defaults = AsyncMock()
    router = task_routes.setup_task_routes(scheduler)
    return SimpleNamespace(
        post=_endpoint(router, "POST"),
        get=_endpoint(router, "GET"),
        db=session_factory,
    )


def _req(user=None, body=None):
    state = SimpleNamespace()
    if user is not None:
        state.current_user = user
    return SimpleNamespace(
        state=state,
        headers={},
        json=AsyncMock(return_value=body or {}),
    )


def _owners(env):
    db = env.db()
    try:
        return [row.owner for row in db.query(cdb.NotificationLog).all()]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_auth_disabled_toast_is_logged_for_local_owner(logs_env, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")

    result = await logs_env.post(_req(body={"body": "Saved", "status": "success"}))

    assert result == {"success": True}
    assert _owners(logs_env) == [DEFAULT_LOCAL_OWNER]
    listed = await logs_env.get(_req(), limit=10)
    assert [n["body"] for n in listed["notifications"]] == ["Saved"]


@pytest.mark.asyncio
async def test_auth_enabled_anonymous_toast_is_still_rejected(logs_env, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")

    with pytest.raises(HTTPException) as exc:
        await logs_env.post(_req(body={"body": "Saved"}))

    assert exc.value.status_code == 401
    assert _owners(logs_env) == []
    assert await logs_env.get(_req(), limit=10) == {"notifications": []}


@pytest.mark.asyncio
async def test_auth_enabled_toast_is_logged_for_logged_in_user_only(logs_env, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")

    await logs_env.post(_req("alice", {"body": "Alice saved", "status": "error"}))

    assert _owners(logs_env) == ["alice"]
    alice = await logs_env.get(_req("alice"), limit=10)
    assert [(n["body"], n["status"]) for n in alice["notifications"]] == [("Alice saved", "error")]
    assert (await logs_env.get(_req("bob"), limit=10)) == {"notifications": []}
    assert (await logs_env.get(_req(), limit=10)) == {"notifications": []}
