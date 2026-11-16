"""A chat started from a note may change that note, and only that note.

The scope lives on the session row (`sessions.note_scope_id`), set by the
server when the chat is created from the note; see core.note_agent_scope.
These tests drive manage_notes, the tool gate, the chat route and the notes
API with a real database.
"""
import asyncio
import datetime
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import core.database as cdb
import core.note_agent_scope as scope_mod
import routes.chat_routes as chat_routes
import routes.note_routes as nr
from core.database import Note, NoteShare, Session as DbSession
from core.note_agent_scope import (
    NoteEditScope,
    bind_note_edit_scope,
    current_note_edit_scope,
    load_note_edit_scope,
    unbind_note_edit_scope,
)
from src.tool_capabilities import ToolRunSecurityContext
from src.tools import notes as notes_tool
from tests.helpers.database import disposable_database

MINE = "mine0001-note-aaaa"
OTHER = "othr0002-note-bbbb"
SID = "chat-from-note"


@pytest.fixture
def db(monkeypatch, tmp_path):
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(cdb, "SessionLocal", factory)
        monkeypatch.setattr(nr, "SessionLocal", factory)
        monkeypatch.setattr(notes_tool, "_SEEN_VERSIONS", type(notes_tool._SEEN_VERSIONS)())
        monkeypatch.setattr(scope_mod, "_SEEN_BY_SESSION", type(scope_mod._SEEN_BY_SESSION)())
        with factory() as s:
            s.add(Note(id=MINE, owner="alice", title="Trip", content="Pack light\n- [ ] book the cabin"))
            s.add(Note(id=OTHER, owner="alice", title="Groceries", content="- [ ] milk"))
            s.add(DbSession(id=SID, name="Agent: Trip", endpoint_url="", model="m", owner="alice", note_scope_id=MINE))
            s.commit()
        yield factory


def _row(factory, note_id):
    with factory() as s:
        return s.get(Note, note_id)


def _scope(factory, session_id=SID):
    with factory() as s:
        return load_note_edit_scope(s, session_id, "alice")


async def _arun(args, scope):
    token = bind_note_edit_scope(scope)
    try:
        return await notes_tool.do_manage_notes(json.dumps(args), owner="alice")
    finally:
        unbind_note_edit_scope(token)


def _run(args, scope):
    return asyncio.run(_arun(args, scope))


def _user_edits(factory, note_id, change):
    """The user saves something in the note window."""
    with factory() as s:
        row = s.get(Note, note_id)
        row.content = change(row.content)
        row.updated_at = row.updated_at + datetime.timedelta(seconds=1)
        s.commit()


def _read(factory, scope):
    """The turn shows the run its note, as chat_stream does."""
    scope.saw(_row(factory, scope.note_id))


def test_the_scope_comes_from_the_session_row(db):
    scope = _scope(db)
    assert scope == NoteEditScope(session_id=SID, note_id=MINE, shared=False)
    assert _scope(db, "an-ordinary-chat") is None


def test_a_write_on_the_attached_note_goes_through(db):
    scope = _scope(db)
    _read(db, scope)
    result = _run({"action": "update", "id": MINE, "content": "Pack light, bring boots"}, scope)
    assert result["exit_code"] == 0, result
    assert result["agent_edit"] is True
    assert _row(db, MINE).content == "Pack light, bring boots\n\n- [ ] book the cabin"
    # The run's own write is the version it has seen: a second edit needs no re-read.
    result = _run({"action": "toggle_item", "id": MINE, "index": 0}, scope)
    assert result["exit_code"] == 0, result
    assert _row(db, MINE).content.endswith("- [x] book the cabin")


def test_an_update_without_an_id_means_the_attached_note(db):
    scope = _scope(db)
    _read(db, scope)
    result = _run({"action": "update", "title": "Cabin trip"}, scope)
    assert result["exit_code"] == 0, result
    assert _row(db, MINE).title == "Cabin trip"
    assert _row(db, OTHER).title == "Groceries"


@pytest.mark.parametrize("args", [
    {"action": "update", "id": OTHER, "content": "x"},
    {"action": "update", "id": OTHER[:8], "content": "x"},
    {"action": "toggle_item", "id": OTHER, "index": 0},
])
def test_a_write_on_another_note_is_refused(db, args):
    scope = _scope(db)
    _read(db, scope)
    before = _row(db, OTHER)
    result = _run(args, scope)
    assert result["exit_code"] == 1
    assert "may change only that note" in result["error"] and MINE in result["error"]
    after = _row(db, OTHER)
    assert (after.content, after.updated_at) == (before.content, before.updated_at)


@pytest.mark.parametrize("args, phrase", [
    ({"action": "add", "title": "New"}, "add is not available"),
    ({"action": "create", "title": "New"}, "add is not available"),
    ({"action": "delete", "id": MINE}, "delete is not available"),
    ({"action": "update", "id": MINE, "archived": True}, "cannot change: archived"),
    ({"action": "update", "id": MINE, "archived": True, "content": "x"}, "cannot change: archived"),
    ({"action": "share", "id": MINE}, "Sharing is changed from the Notes window"),
])
def test_creating_deleting_and_archiving_are_refused(db, args, phrase):
    scope = _scope(db)
    _read(db, scope)
    with db() as s:
        count = s.query(Note).count()
    result = _run(args, scope)
    assert result["exit_code"] == 1
    assert phrase in result["error"]
    with db() as s:
        assert s.query(Note).count() == count
    assert _row(db, MINE).content == "Pack light\n- [ ] book the cabin"
    assert _row(db, MINE).archived in (False, None)


def test_archived_false_a_model_fills_in_is_ignored(db):
    scope = _scope(db)
    _read(db, scope)
    result = _run({"action": "update", "id": MINE, "content": "Pack", "archived": False}, scope)
    assert result["exit_code"] == 0, result


def test_colour_tag_pin_and_reminder_of_the_attached_note_can_change(db):
    # Leo's 020acb54 todo: "Update the note to demonstrate update action (change color, label, etc.)".
    scope = _scope(db)
    _read(db, scope)
    result = _run({"action": "update", "id": MINE, "color": "blue", "label": "demo", "pinned": True,
                   "due_date": "2030-01-02T09:00:00+00:00"}, scope)
    assert result["exit_code"] == 0, result
    row = _row(db, MINE)
    assert (row.color, row.label, row.pinned, row.due_date) == ("blue", "demo", True, "2030-01-02T09:00:00+00:00")


@pytest.mark.parametrize("args", [
    {"action": "list"},
    {"action": "search", "query": "milk"},
    {"action": "view", "id": OTHER},
    {"action": "get", "id": MINE},
])
def test_reads_reach_every_note(db, args):
    result = _run(args, _scope(db))
    assert result["exit_code"] == 0, result


def test_the_scope_survives_a_follow_up_turn(db):
    first = _scope(db)
    _read(db, first)
    assert _run({"action": "update", "id": MINE, "content": "one"}, first)["exit_code"] == 0
    # Next turn, typed in the normal chat: no note attached, no client flag.
    follow_up = _scope(db)
    assert follow_up is not first and follow_up.note_id == MINE
    assert _run({"action": "update", "id": OTHER, "content": "x"}, follow_up)["exit_code"] == 1
    assert _run({"action": "add", "title": "x"}, follow_up)["exit_code"] == 1
    # What the chat last read or wrote is still known.
    assert _run({"action": "update", "id": MINE, "content": "two"}, follow_up)["exit_code"] == 0


def test_a_write_from_a_stale_version_is_refused_until_the_note_is_read_again(db):
    scope = _scope(db)
    _read(db, scope)
    _user_edits(db, MINE, lambda c: c + "\n- [ ] typed by the user")
    result = _run({"action": "update", "id": MINE, "content": "agent text"}, scope)
    assert result["exit_code"] == 1
    assert "changed since you last read it" in result["error"]
    assert _row(db, MINE).content.endswith("typed by the user")
    result = _run({"action": "toggle_item", "id": MINE, "index": 0}, scope)
    assert result["exit_code"] == 1 and "changed since you last read it" in result["error"]
    assert _run({"action": "view", "id": MINE}, scope)["exit_code"] == 0
    assert _run({"action": "update", "id": MINE, "content": "agent text"}, scope)["exit_code"] == 0
    # Prose sent alone keeps the task lines, the user's included.
    assert _row(db, MINE).content == "agent text\n\n- [ ] book the cabin\n- [ ] typed by the user"


def test_a_write_before_any_read_is_refused(db):
    result = _run({"action": "update", "id": MINE, "content": "blind"}, _scope(db))
    assert result["exit_code"] == 1
    assert "Read the note first" in result["error"]


def test_an_ordinary_chat_is_not_scoped(db):
    assert current_note_edit_scope() is None
    result = asyncio.run(notes_tool.do_manage_notes(json.dumps({"action": "add", "title": "Free"}), owner="alice"))
    assert result["exit_code"] == 0


# --- the approval gate ---

def _gate(scope, args, *, tainted):
    gate = ToolRunSecurityContext(external_untrusted_context_seen=tainted)
    token = bind_note_edit_scope(scope)
    try:
        return gate.decision_for("manage_notes", json.dumps(args))
    finally:
        unbind_note_edit_scope(token)


def test_a_shared_note_asks_before_every_change(db):
    with db() as s:
        s.add(NoteShare(id="g1", note_id=MINE, shared_with="bob", permission="edit"))
        s.commit()
    scope = _scope(db)
    assert scope.shared
    for tainted in (True, False):
        for args in ({"action": "update", "id": MINE, "content": "x"}, {"action": "update", "content": "x"},
                     {"action": "toggle_item", "id": MINE[:8], "index": 0}):
            decision = _gate(scope, args, tainted=tainted)
            assert decision.allowed is False and "needs your approval" in decision.reason
        # A change the tool will refuse is not asked about.
        assert _gate(scope, {"action": "update", "id": OTHER, "content": "x"}, tainted=tainted).allowed
        assert _gate(scope, {"action": "add", "title": "x"}, tainted=tainted).allowed
    # Reads keep the usual rule: free on an untainted run.
    assert _gate(scope, {"action": "view", "id": MINE}, tainted=False).allowed


def test_a_private_note_gets_no_prompt(db):
    scope = _scope(db)
    assert not scope.shared
    # The note context arms the gate on every note-started run.
    assert _gate(scope, {"action": "update", "id": MINE, "content": "x"}, tainted=True).allowed
    assert _gate(scope, {"action": "toggle_item", "id": MINE, "index": 0}, tainted=True).allowed


def test_the_run_reads_its_own_note_back_without_a_prompt(db):
    scope = _scope(db)
    for ref in (MINE, MINE[:8]):
        assert _gate(scope, {"action": "view", "id": ref}, tainted=True).allowed
    # Other notes keep the usual rule for reads after untrusted context.
    plain = ToolRunSecurityContext(external_untrusted_context_seen=True)
    for args in ({"action": "view", "id": OTHER}, {"action": "list"}):
        expected = plain.decision_for("manage_notes", json.dumps(args)).allowed
        assert _gate(scope, args, tainted=True).allowed is expected


def test_a_body_envelope_gets_the_same_decision_as_the_bare_call(db):
    # manage_notes unwraps {"body": {...}} before acting (_parse_tool_args),
    # so the gate must judge the call the tool will actually run.
    private = _scope(db)
    with db() as s:
        s.add(NoteShare(id="g1", note_id=MINE, shared_with="bob", permission="edit"))
        s.commit()
    shared = _scope(db)
    assert shared.shared and not private.shared
    calls = (
        {"action": "update", "id": MINE, "content": "x"},
        {"action": "update", "content": "x"},
        {"action": "toggle_item", "id": MINE[:8], "index": 0},
        {"action": "update", "id": OTHER, "content": "x"},
        {"action": "view", "id": MINE},
        {"action": "view", "id": OTHER},
        {"action": "add", "title": "x"},
    )
    for scope in (shared, private):
        for tainted in (True, False):
            for args in calls:
                bare = _gate(scope, args, tainted=tainted)
                wrapped = _gate(scope, {"body": args}, tainted=tainted)
                assert (wrapped.allowed, wrapped.reason) == (bare.allowed, bare.reason), (scope.shared, tainted, args)
    wrapped = _gate(shared, {"body": {"action": "update", "id": MINE, "content": "x"}}, tainted=False)
    assert wrapped.allowed is False and "needs your approval" in wrapped.reason


@pytest.mark.parametrize("args", [
    {"action": "update", "id": MINE, "content": "changed"},
    {"action": "update", "id": MINE[:8], "content": "changed"},
    {"action": "update", "id": MINE.upper(), "content": "changed"},
    {"action": "update", "id": MINE[:8].upper(), "content": "changed"},
    {"action": "update", "id": "Mine0001", "content": "changed"},
    {"action": "update", "note_id": MINE.upper(), "content": "changed"},
    {"action": "toggle_item", "id": MINE.upper(), "index": 0},
    {"body": {"action": "update", "id": MINE.upper(), "content": "changed"}},
], ids=["exact", "prefix", "upper", "upper-prefix", "one-capital", "note_id-upper", "toggle-upper", "body-upper"])
def test_a_shared_note_asks_whatever_the_case_of_the_id(monkeypatch, db, args):
    # The tool resolves a prefix with SQL LIKE, which ignores ASCII case in
    # SQLite, so any spelling that reaches the note must reach the prompt too.
    # Driven through execute_tool_block, the path a real run takes.
    from src import tool_execution as execution
    from src.agent_runtime.authority import OperationGrant, RequestAuthority
    from src.tool_types import ToolBlock

    monkeypatch.setattr(execution, "_owner_is_admin", lambda owner: False)
    with db() as s:
        s.add(NoteShare(id="g1", note_id=MINE, shared_with="bob", permission="edit"))
        s.commit()
    scope = _scope(db)
    assert scope.shared
    _read(db, scope)
    before = _row(db, MINE).content

    async def go():
        token = bind_note_edit_scope(scope)
        try:
            authority = RequestAuthority("req", "alice", SID, "", (OperationGrant("manage_notes"),))
            return await execution.execute_tool_block(
                ToolBlock("manage_notes", json.dumps(args)), owner="alice", session_id=SID,
                request_authority=authority, security_context=ToolRunSecurityContext())
        finally:
            unbind_note_edit_scope(token)

    _, result = asyncio.run(go())
    assert result.get("blocked") is True, result
    assert _row(db, MINE).content == before


def test_outside_a_note_chat_the_gate_is_unchanged():
    gate = ToolRunSecurityContext(external_untrusted_context_seen=True)
    assert gate.decision_for("manage_notes", json.dumps({"action": "update", "id": MINE, "content": "x"})).allowed is False


# --- the chat route ---

@pytest.mark.asyncio
async def test_chat_stream_binds_the_session_scope_for_the_run(monkeypatch, db):
    from tests.test_foreground_model_routing import _RouteRequest, _chat_stream_endpoint
    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, "agent", captured)
    seen = {}

    async def agent_stream(*args, **kwargs):
        seen["scope"] = current_note_edit_scope()
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", agent_stream)
    monkeypatch.setattr(chat_routes, "SessionLocal", db)
    monkeypatch.setattr(chat_routes, "coerce_message_and_session", lambda *a, **kw: ("hi", SID))
    request = _RouteRequest("agent")
    request._form.update({"compare_mode": "false", "session": SID, "selected_note_ids": json.dumps([MINE])})
    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass
    assert seen["scope"] is not None and seen["scope"].note_id == MINE
    # The note shown with the turn is the version the run has read.
    assert seen["scope"].seen_version == _row(db, MINE).updated_at.isoformat()
    assert current_note_edit_scope() is None


def test_the_note_context_names_the_note_id():
    text = chat_routes._format_selected_notes_context([Note(id=MINE, owner="alice", title="Trip", content="x")])
    assert f"Id: {MINE}" in text


# --- Undo, through the notes API ---

class _Identity:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            user = dict(scope.get("headers") or []).get(b"x-test-user")
            if user:
                scope.setdefault("state", {})["current_user"] = user.decode()
        await self.app(scope, receive, send)


@pytest.fixture
def api(monkeypatch, db):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    app.include_router(nr.setup_note_routes())

    async def call(method, url, user="alice", **kw):
        transport = httpx.ASGITransport(app=_Identity(app), client=("203.0.113.7", 54321))
        async with httpx.AsyncClient(transport=transport, base_url="http://notes.test") as c:
            return await c.request(method, url, headers={"x-test-user": user}, **kw)

    return call


async def test_undo_puts_the_exact_bytes_back(db, api):
    # A legacy row: odd spacing, CRLF, and an `items` column not yet folded.
    original = {"title": "  Trip\t", "content": "Pack  light\r\n\r\n  - [ ] cabin  ", "items": '[{"text": "old", "done": false}]'}
    with db() as s:
        row = s.get(Note, MINE)
        row.title, row.content, row.items = original["title"], original["content"], original["items"]
        s.commit()
    scope = _scope(db)
    _read(db, scope)
    assert (await _arun({"action": "update", "id": MINE, "content": "rewritten", "title": "New"}, scope))["exit_code"] == 0
    assert (await _arun({"action": "toggle_item", "id": MINE, "index": 0}, scope))["exit_code"] == 0
    listed = (await api("GET", f"/api/notes/{MINE}")).json()
    assert listed["agent_edit"]["undoable"] is True and listed["agent_edit"]["session_id"] == SID

    r = await api("POST", f"/api/notes/{MINE}/agent-undo")
    assert r.status_code == 200, r.text
    row = _row(db, MINE)
    # Both edits of the chat are undone at once, to the bytes it started from.
    assert {"title": row.title, "content": row.content, "items": row.items} == original
    assert row.agent_undo is None and r.json()["agent_edit"] is None
    assert (await api("POST", f"/api/notes/{MINE}/agent-undo")).status_code == 404


async def test_undo_never_overwrites_a_later_change(db, api):
    scope = _scope(db)
    _read(db, scope)
    assert (await _arun({"action": "update", "id": MINE, "content": "agent"}, scope))["exit_code"] == 0
    _user_edits(db, MINE, lambda c: c + "\nmine")
    assert (await api("GET", f"/api/notes/{MINE}")).json()["agent_edit"]["undoable"] is False
    r = await api("POST", f"/api/notes/{MINE}/agent-undo")
    assert r.status_code == 409
    assert r.json()["detail"]["note"]["content"].endswith("mine")
    assert _row(db, MINE).content.endswith("mine")


async def test_undo_puts_colour_tag_pin_and_reminder_back(db, api):
    with db() as s:
        row = s.get(Note, MINE)
        row.color, row.label, row.pinned, row.due_date = "#ffcc00", "trip", False, None
        s.commit()
    scope = _scope(db)
    _read(db, scope)
    assert (await _arun({"action": "update", "id": MINE, "color": "blue", "label": "demo", "pinned": True,
                         "due_date": "2030-01-02T09:00:00+00:00", "title": "Demo"}, scope))["exit_code"] == 0
    r = await api("POST", f"/api/notes/{MINE}/agent-undo")
    assert r.status_code == 200, r.text
    row = _row(db, MINE)
    assert (row.title, row.color, row.label, row.pinned, row.due_date) == ("Trip", "#ffcc00", "trip", False, None)


async def test_undo_is_the_owners(db, api):
    with db() as s:
        s.add(NoteShare(id="g1", note_id=MINE, shared_with="bob", permission="edit"))
        s.commit()
    scope = _scope(db)
    _read(db, scope)
    assert (await _arun({"action": "update", "id": MINE, "content": "agent"}, scope))["exit_code"] == 0
    bob = (await api("GET", f"/api/notes/{MINE}", user="bob")).json()
    assert bob["agent_edit"] is None
    assert (await api("POST", f"/api/notes/{MINE}/agent-undo", user="bob")).status_code == 403
    assert _row(db, MINE).content.startswith("agent")


# --- the session route: where the scope is set ---

def _create_session_endpoint(monkeypatch, factory):
    import core.session_manager as sm
    import routes.session_routes as sessions
    for module in (sm, sessions):
        monkeypatch.setattr(module, "SessionLocal", factory)
    with factory() as s:
        s.add(cdb.ModelEndpoint(id="ep", owner="alice", name="ep", base_url="http://llm.test/v1", is_enabled=True))
        s.add(Note(id="bobs-note", owner="bob", title="Bob's", content="x"))
        s.commit()
    manager = sm.SessionManager.__new__(sm.SessionManager)
    manager.sessions = {}
    manager.upload_handler = None
    router = sessions.setup_session_routes(manager, {})
    endpoint = next(r.endpoint for r in router.routes if r.path == "/api/session" and "POST" in r.methods)
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice"), headers={})

    def create(**form):
        fields = dict(name="Agent: Trip", endpoint_url="", model="m", rag=None, skip_validation="true",
                      api_key="", endpoint_id="ep", cwd=None, note_id="")
        fields.update(form)
        return endpoint(request=request, **fields)

    return create, manager


def test_a_session_created_from_my_note_carries_its_scope(monkeypatch, db):
    create, _manager = _create_session_endpoint(monkeypatch, db)
    created = create(note_id=MINE)
    with db() as s:
        assert s.get(DbSession, created.id).note_scope_id == MINE
    assert _scope(db, created.id).note_id == MINE
    plain = create()
    with db() as s:
        assert s.get(DbSession, plain.id).note_scope_id is None


def test_no_session_is_scoped_to_someone_elses_note(monkeypatch, db):
    from fastapi import HTTPException
    create, manager = _create_session_endpoint(monkeypatch, db)
    with db() as s:
        count = s.query(DbSession).count()
    for note_id in ("bobs-note", "no-such-note"):
        with pytest.raises(HTTPException) as exc:
            create(note_id=note_id)
        assert exc.value.status_code == 404
    with db() as s:
        assert s.query(DbSession).count() == count, "nothing was created"


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id, offered", [(SID, True), ("an-ordinary-chat", False)])
async def test_a_note_chat_is_always_offered_manage_notes(monkeypatch, db, session_id, offered):
    """A request about something else keeps manage_notes in a note chat, next to what it asked for."""
    from tests.test_foreground_model_routing import _RouteRequest, _chat_stream_endpoint
    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, "agent", captured)
    seen = {}

    async def agent_stream(*args, **kwargs):
        seen["contract"] = kwargs.get("turn_contract")
        seen["authority"] = kwargs.get("request_authority")
        yield "data: [DONE]\n\n"

    message = "List my research reports."
    monkeypatch.setattr(chat_routes, "stream_agent_loop", agent_stream)
    monkeypatch.setattr(chat_routes, "SessionLocal", db)
    monkeypatch.setattr(chat_routes, "coerce_message_and_session", lambda *a, **kw: (message, session_id))
    request = _RouteRequest("agent")
    request._form.update({"compare_mode": "false", "session": session_id, "message": message})
    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass
    contract = seen["contract"]
    assert contract is not None, "the agent turn runs under a turn contract"
    assert "manage_research" in contract.offered, sorted(contract.offered)
    assert ("manage_notes" in contract.offered) is offered, sorted(contract.offered)
    # The request itself grants no note write; the note chat does.
    from src.agent_runtime.authority import ExactOperation
    update = ExactOperation.normalize("manage_notes", json.dumps({"action": "update", "id": MINE, "content": "x"}))
    assert seen["authority"].permits(update) is offered
    if offered:
        # The note record itself is inside the authority's owned scopes, so
        # admission does not refuse it as beyond the request.
        from src.agent_runtime.owned_resources import admit_owned_operation
        from src.agent_runtime.resources import ResourceIdentityError
        monkeypatch.setattr(cdb, "SessionLocal", db)
        try:
            admit_owned_operation(seen["authority"], update)
        except ResourceIdentityError as error:
            raise AssertionError(f"the note write is outside the chat's owned scope: {error}")


# --- the request itself asks for notes ---

NOTE_REQUESTS = ("Use this note to create a new demo one", "create a new note from this one")


async def _note_chat_turn(monkeypatch, db, message, *, session_id=SID):
    """One chat_stream turn in the note chat; returns (response, what the agent loop got)."""
    from tests.test_foreground_model_routing import _RouteRequest, _chat_stream_endpoint
    endpoint = _chat_stream_endpoint(monkeypatch, "agent", {})
    seen = {}

    async def agent_stream(*args, **kwargs):
        seen["authority"] = kwargs.get("request_authority")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", agent_stream)
    monkeypatch.setattr(chat_routes, "SessionLocal", db)
    monkeypatch.setattr(chat_routes, "coerce_message_and_session", lambda *a, **kw: (message, session_id))
    request = _RouteRequest("agent")
    request._form.update({"compare_mode": "false", "session": session_id, "message": message})
    response = await endpoint(request)
    if hasattr(response, "body_iterator"):
        async for _ in response.body_iterator:
            pass
    return response, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("message", NOTE_REQUESTS)
async def test_a_note_request_in_a_note_chat_holds_one_notes_grant(monkeypatch, db, message):
    """The request grants manage_notes and so does the note chat: one grant, no 500."""
    from src.agent_runtime.authority import ExactOperation, create_request_authority
    from src.agent_runtime.owned_resources import admit_owned_operation
    # Without this the test proves nothing: the request alone must grant notes.
    assert "manage_notes" in {g.tool for g in create_request_authority(message, owner="alice").grants}

    response, seen = await _note_chat_turn(monkeypatch, db, message)
    authority = seen.get("authority")
    assert authority is not None, getattr(response, "body", b"").decode()
    notes = [g for g in authority.grants if g.tool == "manage_notes"]
    assert len(notes) == 1 and notes[0].actions is None and notes[0].inputs is None, notes

    update = ExactOperation.normalize("manage_notes", json.dumps({"action": "update", "id": MINE, "content": "x"}))
    assert authority.permits(update)
    monkeypatch.setattr(cdb, "SessionLocal", db)
    admit_owned_operation(authority, update)
    # The grant lets the call reach manage_notes; the chat's scope still keeps
    # it to the attached note, so a new note is refused there.
    scope = _scope(db)
    _read(db, scope)
    result = await _arun({"action": "add", "title": "New demo"}, scope)
    assert result["exit_code"] == 1 and "add is not available" in result["error"]
    result = await _arun({"action": "update", "id": MINE, "content": "Pack light\n- [x] book the cabin"}, scope)
    assert result["exit_code"] == 0, result


@pytest.mark.asyncio
async def test_an_authority_that_cannot_be_built_is_an_error_in_the_chat(monkeypatch, db):
    """Nothing runs, and the UI gets a message naming the session rather than a bare 500."""
    def broken(*args, **kwargs):
        raise ValueError("Malformed request authority")

    monkeypatch.setattr(chat_routes, "request_authority_for_http", broken)
    response, seen = await _note_chat_turn(monkeypatch, db, "List my research reports.")
    assert "authority" not in seen, "the agent loop must not start"
    assert response.status_code == 500
    message = json.loads(response.body)["error"]["message"]
    assert SID in message and "Nothing was run" in message
    # chat.js switches the chat to Chat mode on these words.
    assert "tool" not in message.lower() and "auto" not in message.lower()


async def test_another_chats_edit_takes_the_undo_slot(db, api):
    # One Undo slot per note: a second chat's edit replaces the first chat's
    # snapshot, so Undo goes back to before the second chat, not further.
    with db() as s:
        s.add(DbSession(id="second-chat", name="Agent: Trip", endpoint_url="", model="m", owner="alice", note_scope_id=MINE))
        s.commit()
    first = _scope(db)
    _read(db, first)
    assert (await _arun({"action": "update", "id": MINE, "content": "first chat"}, first))["exit_code"] == 0
    second = _scope(db, "second-chat")
    _read(db, second)
    assert (await _arun({"action": "update", "id": MINE, "content": "second chat"}, second))["exit_code"] == 0
    assert (await api("GET", f"/api/notes/{MINE}")).json()["agent_edit"]["session_id"] == "second-chat"
    r = await api("POST", f"/api/notes/{MINE}/agent-undo")
    assert r.status_code == 200, r.text
    assert _row(db, MINE).content.startswith("first chat")


async def test_the_run_may_change_its_notes_type_and_undo_puts_it_back(db, api):
    scope = _scope(db)
    _read(db, scope)
    result = await _arun({"action": "update", "id": MINE, "note_type": "goal"}, scope)
    assert result["exit_code"] == 0, result
    assert _row(db, MINE).note_type == "goal"
    assert (await api("POST", f"/api/notes/{MINE}/agent-undo")).status_code == 200
    assert _row(db, MINE).note_type in (None, "note", "checklist")
