"""UploadHandler.rename_owner: uploads.json must follow a user rename.

Every uploads.json row carries an `owner` field that resolve_upload() enforces,
and dedupe keys are owner-prefixed ("{owner}:{hash}"). The rename-user route
re-points both via rename_owner(); left stale, the renamed user's existing
chat/document uploads and upload-backed note images stop resolving
(PR #2940 review).
"""
import json
import os

from src.upload_handler import UploadHandler


def _handler(tmp_path):
    base = tmp_path / "base"
    up = base / "uploads"
    up.mkdir(parents=True)
    return UploadHandler(str(base), str(up))


def _write_index(handler, data):
    with open(os.path.join(handler.upload_dir, "uploads.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)


def _read_index(handler):
    with open(os.path.join(handler.upload_dir, "uploads.json"), encoding="utf-8") as f:
        return json.load(f)


def test_rename_moves_owner_field_and_prefixed_key(tmp_path):
    h = _handler(tmp_path)
    _write_index(h, {
        "alice:h1": {"id": "u1.png", "owner": "alice", "hash": "h1"},
        "bob:h2": {"id": "u2.png", "owner": "bob", "hash": "h2"},
        # Legacy row keyed by bare hash: owner moves, key stays.
        "h3": {"id": "u3.png", "owner": "alice", "hash": "h3"},
        # Ownerless row: untouched.
        "h4": {"id": "u4.png", "owner": None, "hash": "h4"},
    })

    assert h.rename_owner("alice", "alice2") == 2

    idx = _read_index(h)
    assert "alice:h1" not in idx
    assert idx["alice2:h1"]["owner"] == "alice2"
    assert idx["h3"]["owner"] == "alice2"
    assert idx["bob:h2"]["owner"] == "bob"
    assert idx["h4"]["owner"] is None


def test_rename_collision_keeps_the_new_owners_existing_row(tmp_path):
    # Both accounts uploaded the same bytes, so the moved key would land on a
    # row the new owner already has — theirs wins, the stale row is dropped.
    h = _handler(tmp_path)
    _write_index(h, {
        "alice:h1": {"id": "old.png", "owner": "alice", "hash": "h1"},
        "alice2:h1": {"id": "new.png", "owner": "alice2", "hash": "h1"},
    })

    assert h.rename_owner("alice", "alice2") == 0

    idx = _read_index(h)
    assert set(idx) == {"alice2:h1"}
    assert idx["alice2:h1"]["id"] == "new.png"


def test_rename_matches_stored_owner_case_insensitively(tmp_path):
    # Auth usernames are lowercase by contract, but old rows may carry mixed
    # case — they must still match (same contract as the SQL owner migration).
    h = _handler(tmp_path)
    _write_index(h, {"Alice:h1": {"id": "u1.png", "owner": "Alice", "hash": "h1"}})

    assert h.rename_owner("alice", "bob") == 1

    idx = _read_index(h)
    assert set(idx) == {"bob:h1"}
    assert idx["bob:h1"]["owner"] == "bob"


def test_rename_is_a_noop_without_matches_or_index(tmp_path):
    h = _handler(tmp_path)
    # No uploads.json at all.
    assert h.rename_owner("alice", "bob") == 0
    assert not os.path.exists(os.path.join(h.upload_dir, "uploads.json"))
    # Index exists but nothing owned by alice.
    _write_index(h, {"bob:h1": {"id": "u1.png", "owner": "bob", "hash": "h1"}})
    assert h.rename_owner("alice", "carol") == 0
    assert _read_index(h) == {"bob:h1": {"id": "u1.png", "owner": "bob", "hash": "h1"}}
