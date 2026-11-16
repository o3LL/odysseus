"""A note's title and text have a size limit, and the list's view cache a memory budget.

Without limits a 3 MB body and a 50,000-character title were stored (200),
and the list's derived-view cache held every distinct content it had seen
up to 4096 rows, however long each one was. A write past a limit is refused
with 413 and a message naming the field and the limit; a note already past
it (stored before the limit) can still be edited down.
"""
import routes.note_routes as nr
from core import note_writes
from src.constants import (
    NOTE_CONTENT_MAX_CHARS,
    NOTE_TITLE_MAX_CHARS,
    NOTE_VIEW_CACHE_MAX_CHARS,
    NOTE_VIEW_CACHE_SIZE,
)
from tests.test_notes_sharing import _PEER, _Identity, env  # noqa: F401  (fixture)


def _refused(response, field):
    assert response.status_code == 413, response.text
    detail = response.json()["detail"]
    assert detail["field"] == field
    limit = note_writes.NOTE_TITLE_MAX_CHARS if field == "title" else note_writes.NOTE_CONTENT_MAX_CHARS
    assert detail["limit"] == limit
    assert f"{detail['limit']:,}" in detail["message"]
    return detail


async def test_a_body_or_title_past_the_limit_is_refused_on_create(env):
    body = "x" * 3_000_000
    detail = _refused(await env.call("POST", "/api/notes", json={"title": "big", "content": body}), "content")
    assert detail["length"] == 3_000_000
    _refused(await env.call("POST", "/api/notes", json={"title": "t" * 50_000, "content": "ok"}), "title")
    assert (await env.call("GET", "/api/notes")).json()["notes"] == []
    at_limit = await env.call("POST", "/api/notes", json={"title": "t" * NOTE_TITLE_MAX_CHARS,
                                                          "content": "x" * NOTE_CONTENT_MAX_CHARS})
    assert at_limit.status_code == 200


async def test_an_update_or_an_added_task_that_grows_past_the_limit_changes_nothing(env, monkeypatch):
    monkeypatch.setattr(note_writes, "NOTE_CONTENT_MAX_CHARS", 40)
    monkeypatch.setattr(note_writes, "NOTE_TITLE_MAX_CHARS", 10)
    env.seed("n1", title="Trip", content="- [ ] passport")
    before = env.row("n1").updated_at
    _refused(await env.call("PUT", "/api/notes/n1", json={"content": "y" * 41}), "content")
    _refused(await env.call("PUT", "/api/notes/n1", json={"title": "z" * 11}), "title")
    _refused(await env.call("PUT", "/api/notes/n1", json={"items": [{"text": "w" * 41}]}), "content")
    _refused(await env.call("POST", "/api/notes/n1/items", json={"text": "v" * 30}), "content")
    row = env.row("n1")
    assert (row.title, row.content, row.updated_at) == ("Trip", "- [ ] passport", before)
    ok = await env.call("PUT", "/api/notes/n1", json={"content": "y" * 40, "items": [], "title": "z" * 10})
    assert ok.status_code == 200


async def test_a_note_stored_past_the_limit_can_still_be_ticked_and_edited_down(env, monkeypatch):
    monkeypatch.setattr(note_writes, "NOTE_CONTENT_MAX_CHARS", 20)
    long_text = "- [ ] a task that is far past the limit"
    env.seed("old", content=long_text)
    assert (await env.call("POST", "/api/notes/old/items/0/toggle", json={"done": True})).status_code == 200
    shorter = "- [x] a task past the limit"
    assert (await env.call("PUT", "/api/notes/old", json={"content": shorter})).status_code == 200
    assert env.row("old").content == shorter
    _refused(await env.call("PUT", "/api/notes/old", json={"content": shorter + "!"}), "content")


def test_the_agent_tool_refuses_a_note_past_the_limit(env, monkeypatch):
    monkeypatch.setattr(note_writes, "NOTE_CONTENT_MAX_CHARS", 20)
    added = env.tool("alice", action="add", title="Big", content="q" * 21)
    assert added["exit_code"] == 1 and "too long" in added["error"]
    assert env.tool("alice", action="list").get("response", "").count("Big") == 0
    env.seed("n1", content="short")
    updated = env.tool("alice", action="update", note_id="n1", content="q" * 21)
    assert updated["exit_code"] == 1 and "Nothing was saved" in updated["error"]
    assert env.row("n1").content == "short"


def test_the_view_cache_stays_within_its_character_budget():
    nr._derived_view.cache_clear()
    try:
        block = 1_000_000
        for i in range(200):
            content = f"{i}\n" + "x" * block
            assert nr._derived_view(content, None, None, "note")[0] == content
        info = nr._derived_view.cache_info()
        assert info["chars"] <= NOTE_VIEW_CACHE_MAX_CHARS
        assert 0 < info["entries"] < 200
        # The most recently read row is still cached; the first one is gone.
        assert (f"199\n" + "x" * block, None, None, "note") in nr._derived_view._entries
        assert (f"0\n" + "x" * block, None, None, "note") not in nr._derived_view._entries
        # A row too large for the whole budget is derived but never kept.
        huge = "h" * (NOTE_VIEW_CACHE_MAX_CHARS + 1)
        assert nr._derived_view(huge, None, None, "note")[0] == huge
        assert (huge, None, None, "note") not in nr._derived_view._entries
        # The row cap still applies to small rows, least recently read first.
        nr._derived_view.cache_clear()
        for i in range(NOTE_VIEW_CACHE_SIZE + 5):
            nr._derived_view(f"- [ ] {i}", None, None, "note")
        nr._derived_view("- [ ] 5", None, None, "note")
        for i in range(3):
            nr._derived_view(f"new {i}", None, None, "note")
        assert nr._derived_view.cache_info()["entries"] == NOTE_VIEW_CACHE_SIZE
        assert ("- [ ] 5", None, None, "note") in nr._derived_view._entries
        assert ("- [ ] 6", None, None, "note") not in nr._derived_view._entries
    finally:
        nr._derived_view.cache_clear()
