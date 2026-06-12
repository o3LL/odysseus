"""Notes sharing — per-note collaborators (view + edit).

Two layers:
  * `_note_access` (routes/note_routes.py) is the single authorization helper;
    we test its boundary directly against a temp DB.
  * `do_manage_notes` share/unshare exercised end-to-end through the real MCP
    tool with two simulated users (different `owner=` args).

Same throwaway-DB + monkeypatched-SessionLocal pattern as
tests/test_mcp_notes_features.py (a file-backed SQLite so init's tables are
visible to the code under test; `:memory:` would give each connection its own
empty DB).
"""
import os
import json
import uuid
import asyncio
import tempfile


def _make_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import core.database as db

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    engine = create_engine(f"sqlite:///{tmp.name}", connect_args={"check_same_thread": False})
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    return tmp.name, db


def test_note_access_is_strict(monkeypatch):
    """The authorization helper: owner full, stranger nothing, collaborators
    exactly what their grant says."""
    db_path, db = _make_db(monkeypatch)
    try:
        import routes.note_routes as nr
        from core.database import Note, NoteShare
        from types import SimpleNamespace

        def _req(configured):
            # Minimal Request stand-in: _note_access only reads
            # request.app.state.auth_manager.is_configured.
            am = SimpleNamespace(is_configured=configured)
            return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(auth_manager=am)))

        cfg = _req(True)    # configured multi-user deployment
        uncfg = _req(False)  # single-user / auth disabled

        s = db.SessionLocal()
        note = Note(id=str(uuid.uuid4()), owner="alice", title="x", content="- [ ] a")
        s.add(note)
        s.commit()

        # Owner gets everything.
        assert nr._note_access(cfg, s, note, "alice") == {"read", "write", "delete", "share"}
        # An unrelated user gets nothing (the security boundary).
        assert nr._note_access(cfg, s, note, "carol") == set()
        # user is None FAILS CLOSED when auth is configured (Vuln 2 fix)…
        assert nr._note_access(cfg, s, note, None) == set()
        # …but keeps full access in single-user / unconfigured mode.
        assert nr._note_access(uncfg, s, note, None) == {"read", "write", "delete", "share"}

        # Edit collaborator → read + write, but not delete/share.
        s.add(NoteShare(id=str(uuid.uuid4()), note_id=note.id, shared_with="bob", permission="edit"))
        s.commit()
        assert nr._note_access(cfg, s, note, "bob") == {"read", "write"}

        # View collaborator → read only.
        s.add(NoteShare(id=str(uuid.uuid4()), note_id=note.id, shared_with="dave", permission="view"))
        s.commit()
        assert nr._note_access(cfg, s, note, "dave") == {"read"}
        s.close()
    finally:
        os.unlink(db_path)


def test_manage_notes_share_flow(monkeypatch):
    """Share / unshare through the MCP tool, with two simulated users."""
    db_path, db = _make_db(monkeypatch)
    try:
        # Stub AuthManager so share() accepts our test usernames (it validates
        # targets against real accounts otherwise).
        import core.auth as auth_mod

        class _FakeAuth:
            users = {"alice": {}, "bob": {}, "carol": {}}

            def get_privileges(self, u):
                return {"can_share_notes": True}

        monkeypatch.setattr(auth_mod, "AuthManager", _FakeAuth)
        from src.tool_implementations import do_manage_notes

        def call(owner, **args):
            return asyncio.run(do_manage_notes(json.dumps(args), owner=owner))

        # alice creates a note with one task
        r = call("alice", action="add", title="Trip", content="- [ ] Pack")
        nid = r["response"].split("id: ")[1].rstrip(")")

        # Before sharing: bob sees nothing and cannot edit.
        assert call("bob", action="list").get("response") == "No notes found."
        assert call("bob", action="update", id=nid, title="hax").get("error")

        # alice shares with bob (edit).
        assert "bob" in call("alice", action="share", id=nid, users=["bob"]).get("response", "")

        # bob now sees it (tagged with the owner) and can check the item off.
        out = call("bob", action="list").get("results", "")
        assert "shared by alice" in out
        assert "marked done" in call("bob", action="toggle_item", id=nid, index=0).get("response", "")

        # bob cannot delete (owner-only); carol (not shared) is still blocked.
        assert call("bob", action="delete", id=nid).get("error")
        assert call("carol", action="update", id=nid, title="no").get("error")

        # alice's own list shows the collaborator.
        assert "shared with bob" in call("alice", action="list").get("results", "")

        # Revoke: bob loses access.
        call("alice", action="unshare", id=nid, users=["bob"])
        assert call("bob", action="update", id=nid, title="no").get("error")
        assert call("bob", action="list").get("response") == "No notes found."

        # Sharing with a non-existent account is silently ignored (validated).
        call("alice", action="share", id=nid, users=["ghost"])
        assert call("alice", action="list").get("results", "").count("shared with") == 0
    finally:
        os.unlink(db_path)


def test_manage_notes_share_requires_privilege(monkeypatch):
    """A user without the can_share_notes privilege cannot share via the tool."""
    db_path, db = _make_db(monkeypatch)
    try:
        import core.auth as auth_mod

        class _NoShareAuth:
            users = {"alice": {}, "bob": {}}

            def get_privileges(self, u):
                return {"can_share_notes": False}

        monkeypatch.setattr(auth_mod, "AuthManager", _NoShareAuth)
        from src.tool_implementations import do_manage_notes

        def call(owner, **args):
            return asyncio.run(do_manage_notes(json.dumps(args), owner=owner))

        nid = call("alice", action="add", title="T", content="x")["response"].split("id: ")[1].rstrip(")")
        r = call("alice", action="share", id=nid, users=["bob"])
        assert r.get("error") and "not allowed to share" in r["error"]
        # bob still can't see it.
        assert call("bob", action="list").get("response") == "No notes found."
    finally:
        os.unlink(db_path)


def test_manage_notes_view_permission_and_get(monkeypatch):
    """A 'view' collaborator can read (get) but not edit; owner `get` returns
    full content."""
    db_path, db = _make_db(monkeypatch)
    try:
        import core.auth as auth_mod

        class _FakeAuth:
            users = {"alice": {}, "bob": {}}

            def get_privileges(self, u):
                return {"can_share_notes": True}

        monkeypatch.setattr(auth_mod, "AuthManager", _FakeAuth)
        from src.tool_implementations import do_manage_notes

        def call(owner, **args):
            return asyncio.run(do_manage_notes(json.dumps(args), owner=owner))

        nid = call("alice", action="add", title="Trip", content="- [ ] Pack")["response"].split("id: ")[1].rstrip(")")
        # Owner get returns full content + checklist indices.
        og = call("alice", action="get", id=nid).get("results", "")
        assert "Trip" in og and "0: Pack" in og

        # Share view-only with bob.
        call("alice", action="share", id=nid, users=["bob"], permission="view")
        # bob can read…
        assert "Trip" in call("bob", action="get", id=nid).get("results", "")
        assert "shared by alice" in call("bob", action="get", id=nid).get("results", "")
        # …but cannot edit or toggle (view permission).
        assert call("bob", action="update", id=nid, title="hax").get("error")
        assert call("bob", action="toggle_item", id=nid, index=0).get("error")
    finally:
        os.unlink(db_path)


def test_rename_user_repoints_note_shares(monkeypatch):
    """Renaming a user must move their NoteShare grants to the new username so
    the share doesn't orphan (and re-sharing later doesn't create a phantom
    second collaborator)."""
    db_path, db = _make_db(monkeypatch)
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import routes.auth_routes as ar
        from core.database import Note, NoteShare

        # Seed: admin owns a note shared with bob.
        s = db.SessionLocal()
        n = Note(id=str(uuid.uuid4()), owner="admin", title="t", content="x")
        s.add(n)
        s.add(NoteShare(id=str(uuid.uuid4()), note_id=n.id, shared_with="bob", permission="edit"))
        s.commit()
        nid = n.id
        s.close()

        class _FakeAuth:
            def __init__(self):
                self.users = {"admin": {}, "bob": {}}

            def is_admin(self, u):
                return u == "admin"

            def get_username_for_token(self, token):
                return "admin"

            def rename_user(self, old, new, by):
                self.users[new] = self.users.pop(old, {})
                return True

        am = _FakeAuth()
        app = FastAPI()
        app.state.auth_manager = am
        app.include_router(ar.setup_auth_routes(am))
        c = TestClient(app, raise_server_exceptions=False)

        r = c.put("/api/auth/users/bob/rename", json={"username": "bobby"})
        assert r.status_code == 200, r.text

        s = db.SessionLocal()
        shares = s.query(NoteShare).filter(NoteShare.note_id == nid).all()
        names = sorted(x.shared_with for x in shares)
        s.close()
        # Exactly one share, now pointing at the new name (no orphan/duplicate).
        assert names == ["bobby"], names
    finally:
        os.unlink(db_path)


def _notes_client(monkeypatch, db):
    """TestClient over the real note routes; the acting user comes from the
    x-test-user header (mirrors what the auth middleware would set)."""
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.note_routes as nr

    # note_routes binds SessionLocal at import time; point it at the temp DB.
    monkeypatch.setattr(nr, "SessionLocal", db.SessionLocal)

    app = FastAPI()

    @app.middleware("http")
    async def _user(request, call_next):
        request.state.current_user = request.headers.get("x-test-user")
        return await call_next(request)

    app.state.auth_manager = SimpleNamespace(
        is_configured=True, users={"alice": {}, "bob": {}, "carol": {}}
    )
    app.include_router(nr.setup_note_routes())
    return TestClient(app)


def test_update_route_management_fields_are_owner_only(monkeypatch):
    """PR #2940 review (P2): the generic PUT must not let an 'edit'
    collaborator flip pinned/archived/sort_order — that's the same owner-only
    state the dedicated /pin and /archive routes protect."""
    db_path, db = _make_db(monkeypatch)
    try:
        from core.database import Note, NoteShare

        client = _notes_client(monkeypatch, db)
        s = db.SessionLocal()
        n = Note(id=str(uuid.uuid4()), owner="alice", title="t", content="- [ ] a")
        s.add(n)
        s.add(NoteShare(id=str(uuid.uuid4()), note_id=n.id, shared_with="bob", permission="edit"))
        s.add(NoteShare(id=str(uuid.uuid4()), note_id=n.id, shared_with="carol", permission="view"))
        s.commit()
        nid = n.id
        s.close()

        alice = {"x-test-user": "alice"}
        bob = {"x-test-user": "bob"}
        carol = {"x-test-user": "carol"}

        # Content collaboration still works for the edit collaborator.
        assert client.put(f"/api/notes/{nid}", json={"content": "- [x] a"}, headers=bob).status_code == 200
        # A view collaborator can't write at all (404: don't reveal existence).
        assert client.put(f"/api/notes/{nid}", json={"content": "x"}, headers=carol).status_code == 404

        # Management state is rejected for the edit collaborator…
        for payload in (
            {"archived": True},
            {"pinned": True},
            {"sort_order": 0},
            {"archived": True, "pinned": True, "sort_order": 0},
        ):
            r = client.put(f"/api/notes/{nid}", json=payload, headers=bob)
            assert r.status_code == 403, (payload, r.text)

        # …and none of it leaked into the row.
        s = db.SessionLocal()
        row = s.query(Note).filter(Note.id == nid).first()
        assert not row.archived and not row.pinned
        s.close()

        # The owner keeps full control through the same route.
        r = client.put(
            f"/api/notes/{nid}",
            json={"archived": True, "pinned": True, "sort_order": 5},
            headers=alice,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["archived"] is True and body["pinned"] is True and body["sort_order"] == 5
    finally:
        os.unlink(db_path)


def test_collaborators_do_not_see_the_roster(monkeypatch):
    """PR #2940 review (privacy): `shared_with` is owner-only. A collaborator
    gets their own grant (my_permission/can_edit) and the owner (shared_by),
    never the other collaborators' usernames."""
    db_path, db = _make_db(monkeypatch)
    try:
        from core.database import Note, NoteShare

        client = _notes_client(monkeypatch, db)
        s = db.SessionLocal()
        n = Note(id=str(uuid.uuid4()), owner="alice", title="t", content="x")
        s.add(n)
        s.add(NoteShare(id=str(uuid.uuid4()), note_id=n.id, shared_with="bob", permission="edit"))
        s.add(NoteShare(id=str(uuid.uuid4()), note_id=n.id, shared_with="carol", permission="view"))
        s.commit()
        nid = n.id
        s.close()

        # Owner sees the full roster.
        owner_view = client.get(f"/api/notes/{nid}", headers={"x-test-user": "alice"}).json()
        assert {(x["username"], x["permission"]) for x in owner_view["shared_with"]} == {
            ("bob", "edit"),
            ("carol", "view"),
        }

        # Edit collaborator: own grant only, no roster.
        bob_view = client.get(f"/api/notes/{nid}", headers={"x-test-user": "bob"}).json()
        assert bob_view["shared_with"] == []
        assert bob_view["my_permission"] == "edit"
        assert bob_view["can_edit"] is True
        assert bob_view["shared_by"] == "alice"
        assert bob_view["is_shared"] is True

        # View collaborator, via GET and via the list route.
        carol_view = client.get(f"/api/notes/{nid}", headers={"x-test-user": "carol"}).json()
        assert carol_view["shared_with"] == []
        assert carol_view["my_permission"] == "view"
        assert carol_view["can_edit"] is False

        listed = client.get("/api/notes", headers={"x-test-user": "carol"}).json()["notes"]
        entry = next(x for x in listed if x["id"] == nid)
        assert entry["shared_with"] == []
        assert entry["my_permission"] == "view"
    finally:
        os.unlink(db_path)


def test_manage_notes_update_management_fields_are_owner_only(monkeypatch):
    """The MCP `update` action mirrors the REST split: an edit collaborator
    can change content but not pinned/archived."""
    db_path, db = _make_db(monkeypatch)
    try:
        import core.auth as auth_mod

        class _FakeAuth:
            users = {"alice": {}, "bob": {}}

            def get_privileges(self, u):
                return {"can_share_notes": True}

        monkeypatch.setattr(auth_mod, "AuthManager", _FakeAuth)
        from src.tool_implementations import do_manage_notes

        def call(owner, **args):
            return asyncio.run(do_manage_notes(json.dumps(args), owner=owner))

        nid = call("alice", action="add", title="T", content="x")["response"].split("id: ")[1].rstrip(")")
        call("alice", action="share", id=nid, users=["bob"])

        # Content edit is fine; management state is owner-only.
        assert "updated" in call("bob", action="update", id=nid, content="y").get("response", "")
        assert call("bob", action="update", id=nid, pinned=True).get("error")
        assert call("bob", action="update", id=nid, archived=True).get("error")
        # The owner can still do both at once.
        assert "updated" in call("alice", action="update", id=nid, pinned=True, archived=True).get("response", "")
    finally:
        os.unlink(db_path)
