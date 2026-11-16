# Notes rework: preview branch

This branch is a **snapshot to read and discuss**, not a merge candidate. Everything here will land as a series of small, focused PRs against `dev`, and this branch will be deleted after that. It sits on `dev @ 0096533c`.

I'm sharing it for two reasons: to show where Notes is going, and to find the pieces that Notes and the document editor could share instead of each growing its own copy.

## What's done

The goal was an Apple Notes-style experience: one place to edit a note, everything editable in place, nothing silently lost.

- **One content model.** Checklists and images live as markdown inside the note's `content`, with no parallel `items` / `image_url` state. Existing notes are migrated on startup, and the old row is kept as a backup. Server side: `core/notes_markdown.py`, `core/note_writes.py`, `docs/notes-markdown-migration.md`.
- **One editor.** The note window is the only editor. It renders the markdown and saves only the blocks you touched, so untouched text comes back byte for byte. Links, tables, code, math, mermaid and raw HTML are editable in place. What has no rich form (math, diagrams, unknown HTML) sits in the text as an "island" you can select, move, delete or edit as source. Frontend: `static/js/features/notes/` (`domain/markdownSerializer.js`, `domain/sourceFidelity.js`, `ui/richBody.js`, `ui/noteWindow.js`).
- **Safe saves.** Every write carries the version it was based on. A stale write gets a 409 and the window shows a "changed elsewhere" banner (Keep mine / Reload). A local draft covers a failed or offline save and is offered back on reopen. Edits are retried when the connection returns.
- **Sharing.** Per-note sharing with View / Edit, revoke, leave, a "Shared with me" list, and fail-closed access checks on every route, including the media route for images in shared notes (`core/notes_sharing.py`).
- **Images.** Paste, drop or upload, inline resize (S/M/L), sketch insert, card thumbnails. External images are shown as a link by default. An opt-in setting renders them with no referrer and no cookies, and a collaborator who has it off never fetches the owner's images.
- **Version history.** Every note keeps its versions, with a diff "against now", a one-line summary per change, restore of any version, and versions split per author.
- **Agent actions from a note.** "Ask about this note", whole-note runs and per-item runs. A run may edit **only the note it started from**. Other notes stay read-only, and create/delete are refused. Every agent edit has an Undo that restores the exact bytes. On a shared note, every write asks for approval first (`core/note_agent_scope.py`).
- **Chat integration.** Pinned notes on the empty chat screen, the notes a chat used shown on its messages, and a chat's own note at the top.
- **Limits.** Title and text have size limits (413 with a clear message), and the list's derived-view cache is bounded.
- **Tests.** Unit tests (Python and the Notes JS tests), route tests, and browser flows on desktop and phone. `docs/notes-test-matrix.md` maps each behaviour to the tests that pin it. A regression and edge-case browser suite with run instructions is in `tests/e2e/notes-qa/`.
- **Docs.** `website/notes.md` (user guide) and `specs/calendar-tasks-notes.md` are updated.

## What's still missing (I'm on it)

**Bugs being fixed right now:**
- A table written directly under a list item or a heading (no blank line) can still make an edit in that block get dropped without a warning.
- Editing a quote that contains a code block rewrites the code on every save.
- Text typed while a checklist tick is still on its way to the server can be wiped.
- A collaborator's change moves your caret to the start of the note.
- A delete or leave that fails offline keeps every card on screen. Its test is red on purpose, which also makes `tests/test_notes_reference_contracts_js.py` fail on this branch.
- The new external-images module isn't in the service worker precache, so `node scripts/check_frontend.mjs` fails on this branch.

**Smaller things after that:**
- Size limits only cover title and text (labels and other fields are unbounded).
- A chat-error change touches a `dev` chat test.
- A few edge cases from the exploratory browser suite (multi-line checklist conversion, phone landscape, Back with a note open).

**Open decisions:**
- Whether a run started from a private note should ask before editing it after reading web content (my preference: yes).
- A total size budget for each note's history.
- What `null` should mean for `content` / `items` / `image_url` now that they aren't columns.
- Sharing by exact username rather than showing every account.
- An Undo (or a "Recently deleted" area) for delete.

**Not tested:** real iOS Safari (WebKit with an iPhone profile only), Windows, reminder delivery, and real models beyond a small sample (the browser flows use a stub model).

## What Notes and the document editor could share

From a first read of `static/js/document.js` and `routes/document/`. These are proposals to discuss, not decisions.

| Area | Notes (this branch) | Document editor (`dev`) | Idea |
|---|---|---|---|
| Markdown ⇄ editable DOM | Block-level serializer that writes back only edited blocks and keeps untouched source byte for byte (`markdownSerializer.js`, `sourceFidelity.js`) | Its own markdown handling inside `document.js` | One shared round-trip module, with the same fidelity tests for both |
| Rich blocks | Islands for math, mermaid, code, raw HTML; table and link popovers | KaTeX, tables, code runner, rich-text toolbar | Shared island and popover components, both rendered through `static/js/markdown.js` |
| Saving and conflicts | Base-version check, 409, "changed elsewhere" banner, local draft, retry on reconnect (`application/noteSession.js`, `core/note_writes.py`) | `PUT /api/document/{id}` with no base-version check I could find; local drafts in `localStorage` | One save session with conflict detection. Today two tabs, or a tool edit and a user edit, can overwrite each other |
| Version history | Versions per author, diff against now, restore, summary text (`core/note_versions.py`, `ui/historyPanel.js`, `domain/historyText.js`) | `document_versions` (immutable snapshots, `source` ai/user), list / get / restore routes | One versions service and one history panel. The doc model already tags the source, and Notes adds author-aware merging and the diff view |
| Tool edits with Undo | Edits scoped to one target, Undo restoring exact bytes, approval on shared content (`core/note_agent_scope.py`) | Document tools edit docs (`src/agent_tools/document_tools.py`) | Same scope + Undo + approval pattern for documents |
| Images | Upload, paste, resize, sketch, external-image setting, media access checks | Uploads and import (PDF/DOCX) | Shared image insertion and external-image policy |
| Sharing | View/Edit grants, fail-closed checks | Owner only | If documents need sharing, reuse the same grant model and checks |
| Limits and caching | Size limits as constants, bounded view cache | — | Same constants and guards |

The part I'd tackle first is **save + conflicts + versions**: it's the most valuable to both, the easiest to cut cleanly, and it doesn't touch rendering. The markdown round-trip comes next, but it's the riskiest to share because both editors depend on its exact output.

## Running it

Same as `dev`: `python -m uvicorn app:app`. The migration runs on startup and keeps backups, but use a copy of your data dir anyway. For the browser suite, read `tests/e2e/notes-qa/README.md`: it starts its own throwaway instance with a stub model.
