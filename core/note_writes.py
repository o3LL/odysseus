"""Compare-and-swap writes to one note row.

A route that reads a note, changes it and commits races every other writer
to the same row: two requests that read the same version both commit, and
the first one's change is lost. These helpers turn the commit into
`UPDATE notes SET … WHERE id = ? AND updated_at = ?`, so a write only lands
on the version it was computed from. The caller decides what to do when it
does not: answer 409, or read the row again and re-apply.
"""

from typing import Any, Callable, Optional

from sqlalchemy import DateTime, String, bindparam, inspect, text, update

from core.database import Note, utcnow_naive
from src.constants import NOTE_CONTENT_MAX_CHARS, NOTE_TITLE_MAX_CHARS

# How many times a write that is a pure function of the current row (a task
# toggle, an append, a write with no base) is re-applied after losing a race.
NOTE_WRITE_ATTEMPTS = 5


def size_refusal(title: Optional[str], content: Optional[str], *,
                 before_title: Optional[str] = None, before_content: Optional[str] = None) -> Optional[dict]:
    """Why a note with this title and content can't be stored, or None.

    A field is refused when it is past its limit (`NOTE_TITLE_MAX_CHARS`,
    `NOTE_CONTENT_MAX_CHARS`) and longer than it was before the write, so a
    note stored before the limits existed can still be edited down.
    """
    for field, value, before, limit, name in (
        ("title", title, before_title, NOTE_TITLE_MAX_CHARS, "title"),
        ("content", content, before_content, NOTE_CONTENT_MAX_CHARS, "text"),
    ):
        length = len(value or "")
        if length > limit and length > len(before or ""):
            return {
                "message": f"The note's {name} is too long to save: {length:,} characters, "
                           f"the limit is {limit:,}.",
                "field": field,
                "length": length,
                "limit": limit,
            }
    return None


class NoteChanged(Exception):
    """Another write landed between this request's read and its commit."""


def stored_stamp(db, note_id: str) -> Any:
    """The row's `updated_at` exactly as the database holds it.

    Not the ORM value: SQLite keeps whatever text the writer stored (raw SQL
    migrations and older code may use another format than SQLAlchemy binds),
    and comparing the parsed value back would never match such a row.
    """
    return db.execute(text("SELECT updated_at FROM notes WHERE id = :id"), {"id": note_id}).scalar()


def commit_if_unchanged(db, note: Note, stamp: Any) -> None:
    """Write `note`'s pending column changes only if its row still carries `stamp`.

    Raises NoteChanged (after a rollback) when the row moved on. Without
    pending changes this is a plain commit.
    """
    state = inspect(note)
    changes = {}
    for attr in state.mapper.column_attrs:
        if state.attrs[attr.key].history.has_changes():
            changes[attr.columns[0].key] = getattr(note, attr.key)
    if not changes:
        db.commit()
        return
    table = Note.__table__
    note_id = note.id
    changes["updated_at"] = utcnow_naive()
    if stamp is None:
        # A row a raw SQL writer left without a timestamp.
        same_version = table.c.updated_at.is_(None)
    else:
        stamp_type = String() if isinstance(stamp, str) else DateTime()
        same_version = table.c.updated_at == bindparam("expected_stamp", stamp, type_=stamp_type)
    stmt = update(table).where(table.c.id == note_id).where(same_version).values(**changes)
    # Drop the ORM's own pending UPDATE, which would write unconditionally.
    db.expire(note)
    if db.execute(stmt).rowcount != 1:
        db.rollback()
        raise NoteChanged(note_id)
    db.commit()


def write_with_retry(db, note_id: str, load: Callable[[], Note], apply: Callable[[Note], Optional[Any]],
                     attempts: int = NOTE_WRITE_ATTEMPTS):
    """Load, apply and commit-if-unchanged, re-reading and re-applying on a lost race.

    `apply(note)` changes the loaded note and returns a value for the caller;
    it must be safe to run again on a fresher row. Raises NoteChanged when
    every attempt lost.
    """
    for _ in range(attempts):
        # The stamp before the row: a write landing in between fails the
        # compare below instead of being silently overwritten.
        stamp = stored_stamp(db, note_id)
        note = load()
        result = apply(note)
        try:
            commit_if_unchanged(db, note, stamp)
        except NoteChanged:
            db.expire_all()
            continue
        db.refresh(note)
        return note, result
    raise NoteChanged()
