"""Concurrent writes to one note never lose each other's changes.

Every write route reads the note, changes it and commits. Two requests that
read the same version must not both commit blindly: a write with a base gets
a 409, and a toggle or an append is re-applied to the newer row. The races
are injected deterministically (a competing commit right after the route
loaded the note), plus one run where the toggle requests really overlap.
"""
import asyncio

import pytest

from tests.helpers.note_routes_client import notes_routes_harness


@pytest.fixture
def notes(monkeypatch, tmp_path):
    with notes_routes_harness(monkeypatch, tmp_path) as harness:
        yield harness


async def test_put_with_a_base_is_refused_when_another_write_lands_after_its_check(notes):
    base = notes.seed("n1", "v0")
    fired = notes.write_between("n1", lambda _c: "bob's text")
    r = await notes.call("PUT", "/api/notes/n1", json={"content": "alice's text", "base_updated_at": base})
    assert fired
    assert r.status_code == 409
    assert r.json()["detail"]["note"]["content"] == "bob's text"
    assert notes.content("n1") == "bob's text"


async def test_concurrent_toggles_both_count(notes):
    notes.seed("n1", "- [ ] a\n- [ ] b")
    notes.write_between("n1", lambda c: c.replace("- [ ] b", "- [x] b"))
    r = await notes.call("POST", "/api/notes/n1/items/0/toggle")
    assert r.status_code == 200
    assert notes.content("n1") == "- [x] a\n- [x] b"


async def test_concurrent_appends_both_land(notes):
    notes.seed("n1", "- [ ] a")
    notes.write_between("n1", lambda c: c + "\n- [ ] from bob")
    r = await notes.call("POST", "/api/notes/n1/items", json={"text": "from alice"})
    assert r.status_code == 200
    texts = [t["text"] for t in r.json()["note"]["items"]]
    assert "from alice" in texts and "from bob" in texts


async def test_toggles_racing_for_real_all_count(notes):
    for trial in range(8):
        note_id = f"n{trial}"
        notes.seed(note_id, "- [ ] a\n- [ ] b\n- [ ] c")
        results = await asyncio.gather(
            notes.call("POST", f"/api/notes/{note_id}/items/0/toggle"),
            notes.call("POST", f"/api/notes/{note_id}/items/1/toggle", user="bob"),
            notes.call("POST", f"/api/notes/{note_id}/items/2/toggle"),
        )
        assert [r.status_code for r in results] == [200, 200, 200]
        assert notes.content(note_id) == "- [x] a\n- [x] b\n- [x] c", f"trial {trial}"


async def test_card_item_write_with_a_stale_base_is_a_409(notes):
    base = notes.seed("n1", "- [ ] a\n- [ ] b")
    await notes.call("POST", "/api/notes/n1/items/0/toggle", user="bob")
    r = await notes.call("PUT", "/api/notes/n1", json={
        "items": [{"text": "a", "done": False}, {"text": "b edited", "done": False}],
        "base_updated_at": base,
    })
    assert r.status_code == 409
    assert notes.content("n1") == "- [x] a\n- [ ] b"
