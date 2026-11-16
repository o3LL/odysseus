# Notes: which test pins which behaviour

Every behaviour of the Notes rework with the tests that pin it, and how coverage was measured. Sources: `specs/calendar-tasks-notes.md` (Notes sections, "Agent, Codex, And CLI Surfaces", "Notes Frontend State And Lifetimes"), `website/notes.md`, `docs/notes-markdown-migration.md`. When a behaviour changes, change its row in the same commit.

Each row was checked by reading the named test. Rows marked *superseded* describe a behaviour a later change replaced; they stay so the history of a rule is in one place.

## Running it

```bash
# Python: everything Notes touches. The whole suite is the reference run.
CHROMADB_PORT=59999 ./venv/bin/python -m pytest -q tests

# JS unit (node:test, no browser).
node --test tests/notes*.test.mjs tests/selectedNotesContext.test.mjs tests/noteTestOracle.test.mjs

# E2E: a disposable instance, a stub model, desktop and phone sizes.
# NOTES_FLOWS picks flows (default: all). `bugs` runs last and fails the run
# while a known bug is still there (tests/browser/notesBugsFlow.mjs). Each flow
# runs isolated (tests/browser/notesRunner.mjs): a failure is recorded, the
# contexts it opened are closed, the next flow runs, and the run ends on one
# `SUMMARY <browser>: N flows, P passed, F failed; ...` line.
python scripts/verify_notes_browser_runtime.py --browser chromium
python scripts/verify_notes_browser_runtime.py --browser webkit
```

E2E flows (`tests/browser/`): `native` and its steps `checkMarkdownCards`, `checkNoteWindow`, `checkInlineImages`, `storedTypes`, `cardEdges`, `windowEdges` (`notesNative.mjs`); `gaps` (`notesGapFlow.mjs`); `editable` (`notesEditableFlow.mjs`); `ui` (`notesUiFlow.mjs`); `sharing`, `sharedGrid`, `sharedRefresh`, `lonelyPopover`, `renamedPoll` (`notesSharingFlow.mjs`); `agent` (`notesAgentFlow.mjs`); `agentedit` (`notesAgentEditFlow.mjs`); `accounts`, `accountDeletion`, `agentShareRefused` (`notesAccountFlow.mjs`); `context` (`notesContextFlow.mjs`); `focus` (`notesFocusFlow.mjs`); `history` (`notesHistoryFlow.mjs`); `coverage` (`notesCoverageFlow.mjs`); `bugs` (`notesBugsFlow.mjs`). Each flow runs at 1440×1000 and at phone size, in the engine `--browser` names. Phone size is a plain 390×844 touch viewport, or a Playwright device profile with `NOTES_PHONE_DEVICE` (`auto`: iPhone 15 under WebKit, Pixel 7 under Chromium; viewport, pixel ratio, touch and user agent). "WebKit" is Playwright's WebKit build, not Safari on iOS.

### Measuring coverage

```bash
# Python, branch coverage of every Python file the Notes stack changed
# (A to T on top of dev 4d0610a1: 28 files, from core/ routes/ src/).
git diff --name-only 4d0610a1 -- '*.py' ':!tests' ':!scripts' | paste -sd, > /tmp/notes-py
CHROMADB_PORT=59999 python -m coverage run --branch --include="$(cat /tmp/notes-py)" -m pytest -q tests
python -m coverage report -m

# JS: node unit tests and the Chromium e2e write V8 coverage to one directory;
# c8 reports it against frontend/src (static/js is built from it, byte for byte).
NODE_V8_COVERAGE=$PWD/.v8 node --test tests/notes*.test.mjs tests/selectedNotesContext.test.mjs tests/noteTestOracle.test.mjs
NOTES_JS_COVERAGE_DIR=$PWD/.v8 python scripts/verify_notes_browser_runtime.py --browser chromium
npx c8 report --temp-directory .v8 --include 'frontend/src/features/notes/**' --all --reporter text
```

`NOTES_JS_COVERAGE_DIR` is read by `tests/browser/notesCoverage.mjs` (Chromium only; WebKit has no coverage API). On a slow machine, `NOTES_SERVER_START_TIMEOUT` (seconds, default 45) gives the disposable instance longer to boot and `NOTES_RUN_TIMEOUT` (default 900) gives the flows longer to run; a full run took 12 to 23 minutes on a loaded 4-core machine.

## Columns

- **Python unit**: a function or module on its own, including the tool gate with a scope bound by hand.
- **Python route / integration**: through a FastAPI route, `chat_stream`, the agent loop, or `manage_notes` on a real disposable database.
- **JS unit**: `node --test`, no browser.
- **E2E flow**: `e2e:<flow or step>` from the list above. Both sizes unless the row says otherwise.

Short names: `md` = `test_notes_markdown.py`, `mdr` = `test_notes_markdown_routes.py`, `mig` = `test_notes_unify_migration.py`, `casc` = `test_note_shares_cascade_migration.py`, `sh` = `test_notes_sharing.py`, `codex` = `test_codex_todo_scope_gate.py`, `dt` = `test_chat_stream_requested_disabled_tools.py`, `og` = `test_manage_notes_owner_gate.py`, `up` = `test_upload_handler_cleanup.py`, `fire` = `test_note_reminder_fire_scope.py`, `ES` = `test_note_agent_edit_scope.py`, `CN` = `test_chat_context_notes.py`, `RC` = `test_note_agent_run_context.py`, `NV` = `test_note_versions.py`, `HU` = `test_note_history_ux.py`, `CB` = `test_completion_boundary.py`; `vec` = `tests/fixtures/notes_task_vectors.json` (run by `md` and `notesMarkdownTasks.test.mjs`); JS: `rend` = `notesMarkdownRender`, `ser` = `notesMarkdownSerializer`, `auto` = `notesDraftAutosave`, `shjs` = `notesSharing`, `ag` = `notesAgentActions`, `wf` = `notesWorkflows`, `rem` = `notesReminders` (all `tests/*.test.mjs`).

## Slices A to J
### Content model

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Task grammar: `-`/`*`/`+`, space/tab, `[ ]`/`[x]`/`[X]` (spec §Note Content Model) | vec parse "bullets and spacing"; md::test_parse_vectors | — | vec parse "bullets and spacing"; notesMarkdownTasks::parseTaskLines | — | covered by unit |
| Nesting 2 spaces/level, tab = 1 level, cap 3 levels on emit (§Content Model) | vec parse "nested", apply_items "bad indent coerced" | — | vec parse "nested", apply_items "bad indent coerced" | — | covered by unit |
| Split on `\n` only, CRLF one break (§Content Model) | vec parse "crlf" | — | vec parse "crlf" | — | covered |
| Fence rule: same char, ≥ length closes; unclosed runs to end (§Content Model) | vec parse "backtick/tilde/long fence/other char/unclosed fence" | — | vec parse "backtick/tilde/long fence/other char/unclosed fence"; rend::fenced task samples | — | covered |
| Fenced task lines not numbered/toggled/counted/merged (§Content Model) | vec toggle "skips fenced", merge "fenced only tasks"; md::test_derive_note_type_keeps_goal_and_draw; mig::test_items_kept_when_only_fenced_code_has_tasks | — | vec toggle "skips fenced", merge "fenced only tasks" | — | covered |
| Python, JS domain and `markdown.js` renderer number tasks identically (§Content Model) | md::test_parse_vectors | — | notesMarkdownTasks::parseTaskLines; rend::interactive renderer stamps the same task indices | — | covered |

### Derived fields

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| `items` = `[{index,text,done,indent}]` or null (§Derived Fields) | — | mdr::test_read_derives_items_and_type_from_content, mdr::test_create_writes_items_and_image_into_content | — | — | covered |
| `note_type` checklist vs note derived; goal/draw stored (§Derived Fields) | md::test_derive_note_type_keeps_goal_and_draw | mdr::test_read_derives_items_and_type_from_content | — | — | covered |
| Drawing `image_url` = stored column if unmigrated, else last image (§Derived Fields) | — | mdr::test_put_image_url_folds_and_drawings_replace_their_canvas (GET draw); mdr::test_unmigrated_drawing_reads_its_image_url_column | — | — | covered |
| POST/PUT `items` rewrite task lines in place; unchanged bytes kept; extra appended; past-end dropped; prose/fences untouched (§Derived Fields) | vec apply_items (13) | mdr::test_put_items_rewrites_task_lines_in_place | vec apply_items (13) | — | covered |
| `items: null` = not supplied (§Derived Fields) | md::test_apply_items_none_means_not_supplied | — | — | — | covered |
| `image_url` folds to `![](url)` unless referenced; column never written (§Derived Fields) | md::test_fold_image_url_appends_once_and_replaces_drawings | mdr::test_create_writes_items_and_image_into_content | — | — | covered |
| Re-saved drawing replaces its canvas image, `gallery_id` survives (§Derived Fields) | — | mdr::test_put_image_url_folds_and_drawings_replace_their_canvas | — | e2e:storedTypes | covered |
| Editing a goal note keeps `note_type = goal` (§Derived Fields, Current Gaps) | md::test_derive_note_type_keeps_goal_and_draw (pure fn only) | mdr::test_editing_a_goal_keeps_it_a_goal | — | e2e:storedTypes | covered |
| Unmigrated row read via `effective_content()`; first write folds it (§Derived Fields) | md::test_effective_content_folds_unmigrated_items_only_when_readable | mdr::test_read_derives…("legacy"); mdr::test_put_on_unmigrated_row_folds_items_first | — | e2e:checkMarkdownCards (seeded row is migrated at boot, not read unmigrated) | covered by unit |
| Calendar route reminder writer writes task lines (§Derived Fields) | — | mdr::test_calendar_reminder_writer_uses_task_lines | — | — | covered |
| `manage_calendar` tool reminder writer (`src/tools/calendar.py`) writes task lines (§Derived Fields) | — | mdr::test_calendar_tool_reminder_writer_uses_task_lines | — | — | covered |

### Checklist routes

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Toggle flips line `index`, keeps indent/bullet/text; returns `{ok,item,items,note}` (§Checklist Endpoints) | vec toggle | mdr::test_toggle_route_flips_the_indexed_task_line | vec toggle | e2e:checkMarkdownCards (card), checkNoteWindow (window, single POST) | covered |
| Toggle with no tasks / out of range → 400 (§Checklist Endpoints) | vec toggle "out of range/negative/no tasks" | mdr::test_toggle_route_flips_the_indexed_task_line (400 out of range and no tasks) | vec toggle "out of range/negative/no tasks" | — | covered |
| Append `{text,indent}` after last task or new block; empty → 400 (§Checklist Endpoints) | vec append; md::test_append_rejects_blank_text | mdr::test_append_route_adds_one_task_after_the_last | vec append | — | covered (mdr asserts 400 on blank text) |
| Card checkbox → toggle route; card add-item → append route, never whole-list PUT (§Checklist Endpoints) | — | — | wf::checkbox and quick add use the toggle and append endpoints | e2e:checkMarkdownCards (toggle only); e2e:checkMarkdownCards (toggle), e2e:cardEdges (append, desktop) | covered |

### Images

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Toolbar image button uploads via `/api/upload`, block after caret's block, saves at once (§Note Images) | — | — | — | e2e:checkInlineImages | covered by e2e |
| Paste of image files uploads; pasted text stays text (§Note Images) | — | — | — | e2e:checkInlineImages (file paste); e2e:checkInlineImages (file), e2e:windowEdges (rich paste → plain text) | covered |
| Drop of image files; multi-file drop is one request (§Note Images) | — | tests/test_upload_multifile.py (server batch only) | — | e2e:gaps (synthetic DataTransfer drop, one request) | covered; a real OS drag is not automatable |
| Tap image → S/M/L/Remove; L clears title; Remove drops emptied paragraph (§Note Images) | — | — | ser::image sizes | e2e:checkInlineImages | covered |
| Locked block or non-editable body: image tap inert (§Note Images) | — | — | — | e2e:checkInlineImages | covered for a non-editable body; locked blocks are gone since K |
| Cmd/Ctrl+Z right after insert removes the image (§Note Images) | — | — | — | e2e:checkInlineImages | covered |
| 429 after 3 uploads/10 s; window shows "Image upload failed"/"Some images could not be uploaded" (§Note Images) | — | tests/test_upload_multifile.py::test_genuine_recent_volume_still_throttled (server) | — | e2e:gaps (uploadFailures: 4th upload inside 10 s) | covered |
| Sketch sheet replaces body; "Insert sketch" inserts PNG inline (§Note Images) | — | — | — | e2e:checkInlineImages | covered |
| Card leads with first image, others not on card (§Note Images) | — | — | — | e2e:checkInlineImages | covered |
| Removing an image keeps the upload; cleanup counts inline refs, sized or not (§Note Images) | — | up::test_inline_note_images_are_kept_until_their_reference_is_removed | — | — | covered |
| Writers owner-reserve upload refs; missing/wrong-owner upload fails the write (§Agent… Surfaces) | — | up::test_note_content_reservation_covers_inline_images; up::test_note_calendar_and_document_routes_reserve_before_database_writes | — | — | covered |

### Stale writes

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| PUT with stale `base_updated_at` → 409 with `{message, note}` (§Stale Writes) | — | mdr::test_put_with_stale_base_updated_at_is_refused | — | e2e:checkNoteWindow | covered |
| No `base_updated_at` → last write wins (§Stale Writes) | — | mdr::test_put_without_base_updated_at_keeps_last_write_wins | — | — | covered |
| Another owner's stale PUT is 404, not 409 (§Stale Writes) | — | mdr::test_stale_put_from_another_owner_is_still_a_404 | — | — | covered |
| Window sends base with content saves; colour/tag/reminder/archive patches do not (§Stale Writes) | — | — | auto::one debounced PUT carries title, content and the base; auto::meta writes move the base… (base movement only) | — | covered: auto::colour, tag, reminder and archive patches carry no base; content saves do |

### Sharing

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| `note_access()` matrix owner/edit/view/stranger (§Note Sharing) | — | sh::test_note_access_matrix | — | — | covered |
| Usernames trimmed+lowercased both sides; `""`≡`None` (§Note Sharing) | — | sh::test_note_access_lowercases_both_sides_and_unifies_no_user | — | — | covered |
| Collaborator PUT of owner-only field → 403, nothing applied (§Note Sharing) | — | sh::test_edit_collaborator_cannot_set_owner_only_fields (8 fields); sh::…owner_only_fields now incl. note_type, gallery_id | — | — | covered |
| Edit collaborator: `/pin`, `/archive`, `DELETE` need manage → 403 "Only the note's owner can do that" (§Note Sharing) | — | sh::test_edit_collaborator_cannot_pin_archive_or_delete | — | — | covered |
| Stranger gets same 404 as missing on every route (§Note Sharing) | — | sh::test_stranger_gets_404_everywhere | — | e2e:sharing (note + both media) | covered |
| View collaborator write → 403 view-only (§Note Sharing) | — | sh::test_view_collaborator_is_read_only | — | e2e:sharing (direct PUT and toggle) | covered |
| Edit collaborator edits content/title/tasks (§Note Sharing) | — | sh::test_edit_collaborator_changes_content_title_and_tasks | — | e2e:sharing (card tick, typing, pasted image) | covered |
| Collaborator writes go through the 409 check (§Note Sharing) | — | sh::test_collaborator_edit_goes_through_the_409_check | — | — | covered |
| `GET /api/notes`: mine, then active shared-with-me, plus `me` (§Note Sharing) | — | sh::test_list_returns_owned_then_shared_with_me_and_me; sh::test_archived_view_never_lists_notes_shared_with_me | — | — | covered |
| Roster to owner only; collaborator copy nulls session/agent/ai fields (§Note Sharing) | — | sh::test_roster_and_owner_links_go_to_the_owner_only | — | — | covered |
| `share-targets` lists other accounts, needs `can_share_notes` (§Note Sharing) | — | sh::test_share_and_targets_need_the_privilege | — | — | covered |
| POST share upserts, flips permission, skips unknown/owner, 400 on bad perm (§Note Sharing) | — | sh::test_share_upserts_skips_unknown_and_owner; sh::test_rest_share_with_unknown_account_writes_nothing | — | — | covered |
| Unreadable account list → 503, nothing written (§Note Sharing) | — | sh::test_rest_share_writes_nothing_without_readable_accounts | — | — | covered |
| DELETE share: owner removes anyone; collaborator only self (`left: true`) (§Note Sharing) | — | sh::test_owner_removes_anyone_collaborator_only_leaves; sh::test_rest_leave_is_the_collaborators_way_out | — | e2e:sharing (Leave from the window) | covered |
| Removing people and leaving do not need `can_share_notes` (§Note Sharing) | — | sh::test_removing_people_and_leaving_do_not_need_the_share_privilege | — | — | covered |
| Media route serves referenced uploads to readers both ways (§Note Sharing) | — | sh::test_media_serves_referenced_uploads_both_ways | — | e2e:sharing (owner image for collaborator, collaborator image for owner) | covered |
| Media route 404s unreferenced upload, stranger, traversal (§Note Sharing) | — | sh::test_media_404s_unreferenced_upload_and_stranger | — | — | covered |
| Renderer rewrites `/api/upload/<id>` to note-scoped path on shared notes only (§Note Sharing) | — | — | shjs::a shared note loads uploads through the note; shjs::the media rewrite leaves other images and chat alone | e2e:sharing (card img src is /api/notes/{id}/media/…) | covered |
| Serializer writes `/api/upload/<id>` back (§Note Sharing) | — | — | shjs::edits in a shared note store the upload path | — | covered |
| Writes reserve only the uploads they add; collaborator save with owner's image is not 409 (§Note Sharing) | — | sh::test_writes_reserve_only_the_uploads_they_add (fails with the fix reverted) | — | e2e:sharing (collaborator saves with owner image, no 409) | covered |
| Edge: restore someone else's removed image → 409 (§Note Sharing) | — | sh::test_putting_back_someone_elses_removed_image_is_refused | — | — | covered (pins the known edge) |
| Note delete deletes grants explicitly and via cascade (§Note Sharing) | casc::test_calendar_reminder_delete_removes_grants_on_a_legacy_table | sh::test_note_delete_removes_its_grants; sh::test_note_shares_cascade_on_note_delete; sh::test_tool_delete_removes_grants | — | — | covered |
| Single-user mode: everything accessible, targets empty, share 400 (§Note Sharing) | — | sh::test_single_user_mode_lists_everything_and_refuses_to_share | — | — | covered |
| Auth configured, no identity → 401 (§Notes And Reminders) | tests/test_notes_fail_closed_auth.py | sh::test_unconfigured_auth_without_identity_fails_closed | — | — | covered |
| `can_share_notes` default privilege, admins always (§Note Sharing) | — | sh::test_can_share_notes_is_a_default_privilege | — | e2e:sharing (admin Users tab "Share notes", on by default) | covered |

### Rename/delete

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Rename re-points grants, merges keeping edit, drops grant on own note (§Note Sharing) | — | sh::test_rename_repoints_grants_and_merges_collisions; sh::test_rename_route_moves_grants_in_the_same_transaction | — | e2e:sharing (admin rename, roster follows, open session keeps working) | covered |
| Delete account purges owned notes (trim/lowercase), grants, legacy rows, grants naming it (§Note Sharing) | — | sh::test_account_deletion_deletes_owned_notes_shares_and_backups | — | e2e:accountDeletion (DB rows checked) | covered |
| NULL-owner/other notes untouched; empty name no-op; rerun zeros (§Note Sharing) | — | sh::test_purge_is_idempotent_and_never_touches_ownerless_notes | — | — | covered |
| Purge inside caller transaction rolls back with it; no legacy table OK (§Note Sharing) | — | sh::test_purge_inside_a_caller_transaction_rolls_back_with_it; sh::test_purge_without_a_legacy_backup_table | — | — | covered |
| Collaborator gets 404 on GET/PUT/toggle after owner deleted (§Note Sharing) | — | sh::test_collaborator_gets_404_once_the_owner_is_deleted | — | e2e:accountDeletion | covered |
| Purge failure: account stays deleted, `{ok, warnings}` (§Note Sharing) | — | sh::test_account_deletion_reports_a_notes_cleanup_failure | — | e2e:accountDeletion (forced by a trigger in the disposable DB; admin toast) | covered |
| Admin delete dialog says notes are deleted too (spec §Note Sharing "admin UI"; E) | — | — | — | e2e:accountDeletion (exact text) | covered |
| Uploads of deleted notes are not deleted (§Note Sharing) | — | sh::test_purge_leaves_upload_files_to_the_upload_cleanup | — | — | covered |

### Reminders

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Scheduler note scan only fires for the owner (§Note Sharing) | — | mdr::test_ping_scanner_never_pings_a_collaborator | — | — | covered |
| `/fire-reminder` owner only (§Note Sharing) | — | sh::test_fire_reminder_stays_owner_only; fire::test_real_reminder_requires_owned_note | — | — | covered |
| Browser reminder loop skips notes shared with me (§Note Sharing) | — | — | rem::reminders are the owner's… | — | covered |
| Due badges and "Clear past" count only my notes (§Note Sharing) | — | — | rem::reminders are the owner's… (counts); rem::"Clear past" deletes only my past reminders | — | covered at unit level; rendered badge not asserted |
| `reminder_body()`: prose, `Pending (N)`, 8 shown, `...and K more`, `N items`, 400 chars (§Note Rendering, reminder text) | md::test_pending_summary_and_reminder_body | fire::test_checklist_reminder_body_is_built_from_task_lines; fire::test_fully_done_checklist_reminder_counts_items | — | — | covered |
| Scheduler ping uses `reminder_body()` with task lines (§Reminder text) | — | mdr::test_ping_scanner_reminder_lists_pending_task_lines | — | — | covered |
| Unmigrated checklist reminder still lists legacy items | — | fire::test_unmigrated_checklist_reminder_body_still_lists_legacy_items | — | — | covered |
| Daily brief lists open tasks from any note with task lines (§Reminder text; migration "What keeps working") | — | mdr::test_daily_brief_lists_open_tasks_from_any_note_with_task_lines | — | — | covered |

### Rendering

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| `tasks: interactive` → `li.md-task[data-task-index]` + role=checkbox (§Note Rendering) | — | — | rend::interactive renderer stamps…; rend::task text keeps inline markdown | e2e:checkMarkdownCards | covered |
| Without notes options output equals dev (§Note Rendering) | — | — | rend::without notes options task lines render exactly as on dev | — | covered |
| Task line inside inline code / `<details>` keeps text, not clickable (§Note Rendering) | — | — | rend::a task line in inline code or inside <details> keeps its text and gets no checkbox | — | covered |
| Task rows carry `.note-checkbox` classes (§Note Rendering) | — | — | — | e2e:checkMarkdownCards (`li.md-task.note-checkbox`) | covered by e2e |
| `images: notes` same-origin only: upload, data PNG/JPEG/GIF/WebP, same-origin blob; others → link (§Note Rendering) | — | — | rend::markdown images: default rendering is unchanged, notes allow this origin only | e2e:checkMarkdownCards | covered |
| `scrubNoteFetches`: raw `<img src>` third-party removed (§Note Rendering) | none (node harness stubs `<template>`) | — | — | e2e:checkMarkdownCards (raw img, details img, request log) | covered by e2e |
| `scrubNoteFetches`: srcset, poster, background, xlink:href, style url() removed (§Note Rendering) | — | — | — | e2e:cardEdges (card + window; raw SVG confined to the CSP-locked preview frame) | covered |
| `w=N` → data-w + width, clamped 5..100, 100 writes nothing, other titles stay (§Note Rendering) | — | — | rend::note image widths | e2e:checkInlineImages (measured widths) | covered |
| Note images get `md-note-image`, `loading=lazy`, `decoding=async` (§Note Rendering) | rend (class only) | — | — | — | covered: rend asserts loading/decoding |
| Dialect: backslash escapes one markdown char, typed text renders as typed (§Note Rendering) | — | — | ser::typed text that looks like markdown is escaped and renders as typed; ser::ordinary prose is left unescaped | — | covered |
| Dialect: code spans and `\[ \]`/`\( \)` math keep their backslashes (§Note Rendering) | — | — | rend::notes dialect: code spans and \[ \] / \( \) math keep their backslashes | — | covered |
| Dialect: indented bullets nest to 3; ordered list keeps start (§Note Rendering) | — | — | ser::serializer round-trips (nested bullets, "ordered list keeps its start") | — | covered: rend asserts `<ol start>` |

### Editor

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Untouched block written back byte for byte (§Frontend State) | — | — | ser::joining keeps untouched source byte for byte; ser::blocks split… | e2e:gaps (blocksVerbatim) | covered |
| Edited block serialized in dialect, keeps code lang/tables/nested/numbered/quotes (§Frontend State) | — | — | ser::serializer round-trips…; ser::editor-produced markup serializes like the renderer output | e2e:checkNoteWindow (checklist typing) | covered |
| Lossy blocks reported (math, details, mermaid, aligned table, external image) (§Frontend State) | — | — | ser::blocks the dialect cannot hold are reported | — | superseded by K: those constructs are islands, edited in place (see K) |
| Locked block shown dashed + hint, written back verbatim (§Frontend State) | — | — | — | — | superseded by K: nothing is locked any more (see K) |
| Markdown source mode swaps in a textarea with raw content (§Frontend State) | — | — | — | e2e:windowEdges | covered |
| Paste is plain text only (§Frontend State) | — | — | — | e2e:windowEdges | covered |
| Links refuse `javascript:` (§Frontend State) | — | — | wf::render helpers reject script-capable media (`_safeImgSrc`, not the editor link) | e2e:coverage (image link menu refuses `javascript:`) | covered: ser/rend/sanitize tests + e2e |
| Per-task agent button skipped by serializer and block snapshots (§Frontend State) | — | — | ag::the per-task agent button never reaches saved markdown | e2e:checkNoteWindow (open/close writes nothing) | covered |

### Window

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Nothing written on open/blur/close unless edited (§Frontend State) | — | — | auto::opening and closing a note writes nothing; auto::a blur or close without a real edit never saves | e2e:checkNoteWindow | covered |
| Edit backed up to localStorage at once, saved 600 ms later (§Frontend State) | — | — | auto::typing is backed up synchronously; auto::one debounced PUT | e2e:checkNoteWindow (draft cleared after save) | covered |
| One write queue; older save cannot clear newer draft (§Frontend State) | — | — | auto::older save cannot clear newer draft and writes are serialized | — | covered |
| Untouched new note creates nothing; first edit POSTs once (§Frontend State) | — | — | auto::a new note closed untouched creates nothing | e2e:checkNoteWindow | covered |
| 409 keeps local text; Reload adopts server; Keep mine overwrites (§Frontend State) | — | — | auto::a 409 keeps the local text…(keep); auto::reloading after a 409 | e2e:checkNoteWindow (Reload only); e2e:checkNoteWindow (Reload), e2e:windowEdges (Keep mine) | covered |
| Earlier unsaved draft offered with Restore/Discard (§Frontend State) | — | — | auto::an earlier unsaved draft on this device is offered back | e2e:windowEdges (Restore saves, Discard writes nothing) | covered |
| Typing (or a held edit) while a new note's create request is out moves to the created note's draft; no new-note draft is left to Restore as a second note (§Frontend State) | — | — | auto::typing while a new note is being created…; auto::a held edit on a new note moves to the created note | — | covered; QA finding 18 |
| A restored draft is saved against the version it was typed on, so a newer server copy meets Reload/Keep mine (§Frontend State) | — | — | auto::a restored draft is saved against the version it was typed on…; auto::restoring a draft with no recorded base | — | covered; QA finding 18 |
| Checkbox flip saves pending text first, then toggle route (§Frontend State) | — | — | auto::a checkbox flip saves pending text first; auto::a checkbox flip whose pending text failed… | e2e:checkNoteWindow | covered |
| Own-tab write followed, not reported as conflict (§Frontend State) | — | — | wf::same-tab writes are recorded so the note window can tell them from remote ones | e2e:gaps (ownTick, desktop) | covered on desktop; the phone card has no tick behind an open sheet |
| Failed network save keeps edits, "Note not synced" (§Frontend State) | — | — | auto::failed network save keeps recoverable edits | — | covered |
| Back online, a failed save is sent again without another keystroke, from the open window or one closed while offline; "Note synced" replaces "Note not synced" | — | — | — | `tests/e2e/notes-qa/notes-qa-regressions.spec.mjs`: 'MINOR an edit typed offline is saved when the connection comes back, and says so'; `notes-edge-cases.spec.mjs`: 'P2 edits made offline are sent when the connection comes back' (Chromium) | covered; W5, QA "Offline, then back online". A reload while offline still relies on the Restore banner |
| Window is the only editor; card click opens it; one at a time, opening another flushes (§Frontend State) | — | — | — | e2e:gaps (flushOnSwitch) | covered |
| Minimises to `Note: <title>` dock chip (§Frontend State) | — | — | auto::a minimised note window is labelled with its title, 28 characters at most | e2e:windowEdges (desktop: chip title, restore) | covered |
| Legacy facade `openNote(id)` opens it from chat links (§Frontend State) | — | — | — | e2e:gaps (chatLink) | covered |
| Phone: full-height sheet, 16 px text (§Frontend State) | — | — | — | e2e:checkNoteWindow (mobile) | covered for size and font; real iOS focus zoom needs a real iPhone (not available) |
| Phone: the toolbar is one row that scrolls sideways, every button reachable; the footer steps aside while the text has focus; on its side the footer is one row (§Frontend State) | — | — | — | e2e:ui (`checkToolbar`, `checkTouchTargets`, phone); `tests/e2e/notes-qa/notes-qa-regressions.spec.mjs`: 'MINOR on a phone the note window keeps the screen for the text while typing'; `notes-edge-cases.spec.mjs`: 'P2 a phone in landscape shows at least 150 px of the note' (Chromium) | covered; W2, QA "Phone profile". iPhone 15: 252 px of text before, 342 at rest and 507 while typing. The landscape test's later close-button check is W21, still red. Real iOS keyboard not run |
| Phone: the archive view is reachable from the panel header, and a note archived from the window comes back from it (§Frontend State) | — | — | — | `notes-qa-regressions.spec.mjs`: 'MINOR on a phone a note archived from its window can be found again' (Chromium) | covered; W3 |
| Tag field accepts `#tag`; colour ring (§Frontend State) | — | — | — | e2e:checkNoteWindow | covered |
| Archive and delete (with confirm) from the window (§Frontend State) | — | — | wf::note window deletion waits for confirmation | e2e:checkNoteWindow | covered |
| 404 on save/flip marks gone: stops writing, drops draft, removes card, banner + Close (§Frontend State) | — | — | auto::a 404 marks the note gone; auto::a 404 on a checkbox flip; auto::a 404 while creating a note is an ordinary failure | e2e:accountDeletion (banner, one 404, card gone, no draft) | covered; found fix 9ef8ed7d |
| Taps on a checkbox, an image or a row's agent button work on touch in WebKit (new) | — | — | — | e2e:checkNoteWindow, checkInlineImages, agent — WebKit phone | covered; found fixes bf99115c, c107335f |
| A gone note's card goes even after the reminder start-up read replaced the list (new) | — | — | — | e2e:accountDeletion | covered; found fix 9ef8ed7d (was flaky 2 runs in 3) |

### Sharing UI

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Share button with count, owner + signed in only (§Frontend State) | — | — | — | e2e:sharing | covered |
| Popover lists accounts, switch, View/Edit pills, saves at once (§Frontend State) | — | — | — | e2e:sharing (switch → View, pill → Edit, pill → View) | covered |
| Popover states: "Couldn't load users." + retry, "No other users…", 403 "not allowed" (§Frontend State) | — | — | — | e2e:sharing (failed list + Try again, 403 without the privilege); e2e:lonelyPopover (no other users) | covered |
| "Shared with me" header after own notes, filter chip (§Frontend State) | — | — | — | e2e:sharing | covered (chip at unit level: notesQueries) |
| Grid view: shared cards stay below the "Shared with me" header (§Current Gaps: known defect) | — | — | — | e2e:sharedGrid (desktop + phone) | covered; found fix 6f300bd7 |
| Incoming card shows owner name; mine shows `Shared · n` (§Frontend State) | — | — | shjs::sharing rules read the payload (shareBadge) | e2e:sharing | covered |
| Shared card: no pin/done/archive/delete/colour/select/drag; menu Copy + Leave (§Frontend State) | — | — | — | e2e:sharing (controls absent) | covered; its corner menu is unreachable, Leave moved to the window (fix 6944e555) |
| View-only card: no add-item; tapping a task opens the note (§Frontend State) | — | — | — | e2e:sharing (tap on a view-only task opens the note) | covered |
| View-only window: banner, no toolbar, read-only title/body, reminder disabled, empty footer (§Frontend State) | — | — | — | e2e:sharing | covered (footer now holds only Leave) |
| View-only session never saves, backs up or conflicts; paste/drop do nothing (§Frontend State) | — | — | shjs::a view-only session never saves, backs up or conflicts | e2e:sharing (paste and drop change nothing, no write) | covered |
| Edit window: content/title/tasks, empty footer (§Frontend State) | — | — | — | e2e:sharing (footer = Leave only) | covered |
| Shared note never sets my top sort order | — | — | shjs::a shared note never sets my top sort order | — | covered |
| 20 s refresh while open+visible; paused while typing, on popover/select; failed read keeps list (§Frontend State) | — | — | — | e2e:sharedRefresh (Playwright clock: change shows after one re-read; an idle open note takes the change with focus kept, c331dc6a; no re-read while the popover is open or in select mode; a failed read keeps the list) | covered; found fix a214655c. The pause while typing is in the QA regression spec |
| `me` refreshed on every poll so a renamed account keeps working (§Frontend State) | — | — | — | e2e:sharing renamedPoll (Playwright clock) | covered |
| A collaborator can leave from the note window (new) | — | — | — | e2e:sharing (confirm, window closes, card goes, note and media 404) | covered; found fix 6944e555 |

### Agent

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Per-item prompt, Ask prompt, whole-note prompt texts (§Agent… Surfaces) | — | — | ag::prompts: one item with its note title… | — | covered |
| Run creates `Agent: <label>` session on default model, links `agent_session_id`, posts agent mode with `disabled_tools=["manage_notes"]` (§Agent… Surfaces) | — | — | ag::a per-item run creates a session…; ag::the three entry points share one run | e2e:agent | superseded by M: runs get `manage_notes`, scoped to their note (see M) |
| `chat_stream` honours `disabled_tools` (form JSON array or JSON body); malformed changes nothing (§Agent… Surfaces) | — | dt::test_requested_disabled_tools_parsing; dt::test_form_disabled_tools_reach_the_policy_and_the_agent_loop; dt::test_json_body_disabled_tools_are_honoured_too; dt::test_malformed_disabled_tools_change_nothing | — | — | covered |
| Fully withheld family is not required; todo prompt answered without notes family (§Agent… Surfaces) | — | dt::test_a_withheld_family_is_not_required | — | e2e:agent (todo prompt reaches the stub) | covered |
| `manage_notes` never reaches the model on note runs (§Agent… Surfaces) | — | dt::test_form_disabled_tools_reach_the_policy_and_the_agent_loop (loop-level) | — | e2e:agent (stub tool list; fails with c59d55c9 reverted) | superseded by M (see M) |
| Runs owner-only; hidden on notes shared with me (§Agent… Surfaces) | — | — | ag::per-task buttons: my saved notes only…; ag::no run on a note shared with me… | e2e:agent | covered |
| No run on empty prompt or missing default model (§Agent… Surfaces) | — | — | ag::no run on a note shared with me, an empty prompt or a missing default model | — | covered |
| Every run re-points `agent_session_id` (§Agent… Surfaces) | — | — | ag::a per-item run… (patch call) | e2e:agent | covered |
| Task button reads row text at click time (§Frontend State) | — | — | ag::a row resolves its own text at click time | — | covered |
| Task button hidden on done rows (§Frontend State) | — | — | ag::decorateTaskRows: one button per task row… (buttons are added; CSS hides them) | e2e:agent (done rows have no button, card and window) | covered |
| Task button hidden in select mode (§Frontend State) | — | — | ag::per-task buttons… (showsTaskAgent selectMode) | — | covered (rule); DOM: e2e-testable |
| Hover-only on desktop, dimmed always on touch (§Frontend State) | — | — | — | e2e:agent (desktop opacity 0 → >0 on hover; phone: none on cards, 0.4 in the window) | covered (emulated touch); real phone not run |
| Newly typed task gets its button after save (§Frontend State) | — | — | — | e2e:agent | covered |
| Ask popover: four presets + text field, flushes first (§Frontend State) | ag (prompt only) | — | — | e2e:agent (preset + free text) | covered |
| "Agent chat" footer once linked; card "Agent" tag (§Frontend State) | — | — | — | e2e:agent | covered |
| Toast "Agent working in the background" with Open chat (§Frontend State) | — | — | ag::a per-item run… (toast text) | e2e:focus (Open chat from the toast) | covered |
| Open agent chat closes window (flush) and panel, then selects session (§Frontend State) | — | — | ag::open agent chat leaves Notes, then selects the linked session | e2e:agent (tag and footer land on the session) | covered |
| Note content unchanged by a run (§Agent… Surfaces "cannot edit notes") | — | — | — | e2e:agent (byte compare) | covered with a stub; a real model not automatable |

### Tools

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| `manage_notes` has no share/unshare, no users/permission args (§Agent… Surfaces) | — | sh::test_manage_notes_schema_offers_no_sharing_actions | — | — | covered |
| share/unshare/leave any spelling refused before DB, `SHARING_UI_ONLY_ERROR` (§Agent… Surfaces) | — | sh::test_tool_refuses_to_change_sharing | — | e2e:agentShareRefused (stub calls share, approval allowed, refusal shown, note_shares empty) | covered |
| list/get still show sharing (§Agent… Surfaces) | — | sh::test_tool_works_on_a_note_shared_over_rest | — | — | covered |
| Id/prefix resolve only among readable notes, own first; `%`/`_` literal; uniform not-found (§Agent… Surfaces) | — | sh::test_tool_prefix_lookup_cannot_reach_a_strangers_note; sh::test_tool_prefix_prefers_own_note_over_a_shared_one; og::test_other_owner_and_missing_id_get_the_same_error | — | — | covered |
| Collaborator cannot set tool owner-only fields; view → "view only" (§Agent… Surfaces) | — | sh::test_tool_view_permission_and_owner_only_fields | — | — | covered: sh::test_tool_view_permission_and_owner_only_fields (label, note_type included) |
| `get` aliases view/read/show/open print `[x] i: text` (§Agent… Surfaces) | — | mdr::test_manage_notes_get_list_and_toggle_use_content | — | — | covered |
| get/read/show/open count as reads in tool_capabilities and clean_agent_preview (§Agent… Surfaces) | tests/test_clean_agent_preview.py (only `list`, `view`); tests/test_clean_agent_preview.py::test_manage_notes_read_aliases_are_private_reads | — | — | — | covered |
| `list` stays a locator; `toggle_item` needs explicit int index + optional done (§Agent… Surfaces) | tests/test_notes_checklist_state.py::test_toggle_requires_explicit_integer_index | mdr::test_manage_notes_get_list_and_toggle_use_content | — | — | covered |
| `checklist_items` written as task lines (§Agent… Surfaces) | — | mdr::test_manage_notes_add_and_update_write_task_lines | — | — | covered |
| Selected-notes chat context: content verbatim + `Items:` block + derived type (§Agent… Surfaces) | — | mdr::test_selected_notes_context_reads_task_lines | — | e2e (selected-context steps: "1 note selected") | covered |

### Codex

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| share/unshare/leave → 403 for every todo scope, before scope check (§Agent… Surfaces) | — | codex::test_sharing_is_refused_for_every_todo_scope | — | — | covered |
| Not in WRITE_ACTIONS; not in /capabilities; read/write scopes still work (§Agent… Surfaces) | — | codex::test_sharing_is_not_a_bridge_action; codex::test_capabilities_do_not_list_sharing; codex::test_write_scope_still_writes; codex::test_read_scope_cannot_write; codex::test_read_scope_still_reads | — | — | covered |

### CLI

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| `scripts/odysseus-notes` list/show/search derive items and note_type (§Agent… Surfaces) | tests/cli/test_notes_cli_items.py::test_serialize_reads_items_from_content_task_lines | — | — | — | covered |
| `create --item TEXT` (repeatable) writes task lines (§Agent… Surfaces) | tests/cli/test_notes_cli_items.py::test_create_writes_each_item_as_a_task_line | — | — | — | covered |

### Migration

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Items fold: merge after last task / new block; not doubled; fence-only still folded; items NULL (migration §What gets folded) | mig::test_folds_items_into_content_and_clears_items; mig::test_items_already_present_as_task_lines_are_not_doubled; mig::test_items_kept_when_only_fenced_code_has_tasks; vec merge | — | vec merge | e2e:checkMarkdownCards (seeded legacy note) | covered |
| Fold drops empty-text items, collapses newline in text, coerces indent 0..3, `checked` alias (migration §What gets folded) | vec apply_items has these for `apply_items`; mig::test_malformed_row… (indent "oops" only) | — | vec apply_items has these for `apply_items` | — | covered: mig::test_fold_drops_blank_items_turns_newlines_into_spaces_and_coerces_done_and_indent |
| Existing content never rewritten, only gains lines (migration §What gets folded) | vec merge; mig::test_items_already_present… | — | vec merge | — | covered |
| image_url fold, unless referenced; gallery_id kept (migration §What gets folded) | mig::test_image_url_folds_into_content_and_keeps_gallery_link | — | — | — | covered |
| note_type not rewritten (migration §What gets folded) | mig::test_folds_items_into_content_and_clears_items; mig::test_image_url_folds… | — | — | — | covered |
| Backup row before first rewrite, INSERT OR IGNORE keeps the first state (migration §Backup) | mig::test_backup_row_holds_the_pre_migration_values; mig::test_idempotent_on_rerun | — | — | — | covered |
| Documented rollback SQL restores rows byte for byte (migration §Backup) | mig::test_documented_rollback_sql_restores_the_rows | — | — | — | covered |
| Idempotent; malformed row skipped with warning, rows after it continue (migration §Idempotency) | mig::test_idempotent_on_rerun; mig::test_malformed_row_is_skipped_and_keeps_items_next_to_a_valid_row; mig::test_empty_items_rows_are_left_alone | — | — | — | covered: mig::test_malformed_row_is_skipped_with_a_warning_naming_it |
| Missing DB file → no-op (migration §When it runs) | mig::test_missing_database_is_a_no_op | — | — | — | covered |
| Non-SQLite `DATABASE_URL` → all three steps no-op; readers fold on the fly (migration §When it runs) | mig::test_non_sqlite_database_url_is_a_no_op | mdr::test_unmigrated_checklist_reads_as_task_lines_without_a_migration; mdr::test_unmigrated_photo_note_shows_its_image_and_a_save_keeps_one | — | — | covered; found fix 3b011175 (image_url was not folded on read) |
| note_shares rebuild when FK lacks CASCADE, model indexes, orphan rows dropped, idempotent, current/missing table left alone (migration §cascade) | casc::test_legacy_table_blocks_deleting_a_shared_note; casc::test_migration_rebuilds_legacy_table_with_cascade_and_model_indexes; casc::test_migration_matches_a_fresh_create_all; casc::test_migration_drops_orphan_rows_and_is_idempotent; casc::test_current_table_is_left_alone; casc::test_missing_table_is_a_no_op | — | — | — | covered |
| Cascade rebuild failure rolls back and logs, old table kept (migration §cascade) | casc::test_failed_rebuild_rolls_back_and_keeps_the_old_table | — | — | — | covered |
| Deleted account's legacy rows removed so rollback cannot restore them (migration §Backup) | — | sh::test_account_deletion_deletes_owned_notes_shares_and_backups | — | — | covered |

### Layout

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Card checkbox tap does not open the window | — | — | — | e2e:checkMarkdownCards | covered |
| External images never fetched from a card (§Note Rendering) | — | — | — | e2e:checkMarkdownCards (request log) | covered |
| Select mode → send to agent → "1 note selected"; cleared on new chat (§Frontend State, selection) | — | — | wf::selection can exit only after synchronous acceptance | e2e (select-mode steps in main loop) | covered |
| Masonry observer not re-created after close (§Frontend State) | — | — | tests/notesApplication.test.mjs::masonry observer is not re-created once Notes is closed | — | covered |
| Reminder startup idempotent, interval survives panel close (§Frontend State) | — | — | rem::startup and loop are idempotent… | — | covered |
| Card drag reorder (desktop/mobile) (§Frontend State, pre-existing) | — | — | wf::reorder retains desktop persistence-only and mobile post-response state ordering | — | unit only: a real mouse/touch drag is not driven reliably |

### Cross-browser

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Whole e2e on Safari/iOS (all slices) | n/a | — | — | Chromium only; every flow on Chromium and WebKit, desktop and phone | WebKit at phone size (iPhone 15 profile) is the stand-in; real Safari and iOS not available |

### Rows added with this matrix (no earlier row covered them)

| Behaviour (source) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Reminder from the window: presets, weekly repeat on a chosen day, a typed date and time, removal from the tag's x (website "Colours, tags and reminders") | — | — | — | e2e:coverage (`checkRemindersAndFilters`) | covered |
| Panel search matches title, content, tag and task text (§Frontend State) | — | — | `notesQueries`: search matches title, content, tag and task text… | e2e:coverage (search box) | covered |
| Filter chips: Reminders (on, then hidden), a tag, Default, All; Today, Goals, Shared with me (§Frontend State) | — | — | `notesQueries`: each filter chip keeps only its notes | e2e:coverage (Reminders twice, tag, Default, All) | covered; Today/Goals/Shared chips at unit level only |
| Order: pinned, then active reminders, manual order, newest; shared after mine; archived view newest first (§Frontend State) | — | — | `notesQueries` (three tests) | — | covered |
| The owner turns a share off while the collaborator has the note open: next keystroke gets the "no longer shared" banner, text kept, no draft, card gone; note and media 404 for them (§Note Sharing, §Frontend State 404) | — | `sh` (unshare route) | — | e2e:coverage (`checkRevokeAndDelete`) | covered |
| The owner deletes a shared note under the collaborator's open window: same banner, gone after a refresh (§Frontend State 404) | — | — | `auto`: a 404 marks the note gone… | e2e:coverage (`checkRevokeAndDelete`) | covered |
| A sent message's note card keeps the title it had when sent; a click opens the note as it is now | — | `CN::test_the_user_message_records_its_context_notes` | `notesContextStrip`: a sent message records id and title only | e2e:coverage (`checkSentCardKeepsTitle`) | covered |
| The notes HTTP port keeps a failed call's status and body for the workflows (409 note, 404 gone) | — | — | `notesInfrastructure` (three tests) | — | covered |
| Card archive, unarchive, colour, pin and delete roll back with a message when refused; phone reorder numbers the cards (§Frontend State) | — | — | `wf`: archive, unarchive and colour…; accepted card writes… | — | covered |
| Delete or leave failing offline keeps the cards on screen | — | — | `wf`: a delete or leave that fails offline keeps every card on screen | — | **BUG (P3, red)**, see Known bugs |
| Share popover: a click made while the previous change saves | — | — | — | e2e:bugs (share pill clicked while saving); e2e:sharing (Edit right after the switch) | covered; fixed, see Known bugs |
| Copy a note: Ctrl/Cmd+C over a card puts the title and the markdown on the clipboard (§Frontend State) | — | — | — | e2e:coverage (`checkPanelKeys`, desktop) | covered; the card's own Copy and "Agent: solve this" sit in a menu the cards hide (reported by H) |
| Panel keys: Ctrl/Cmd+Z undoes an archive from the window; Escape leaves select mode, then the archive view, before it closes the panel | — | — | `wf` (undo stack) | e2e:coverage (`checkPanelKeys`, desktop; the phone reaches the archive view through the same header toggle, see the phone archive row) | covered |

## Slices K to T

### K — every part of a note editable in place

| Behaviour (slice) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| No block lock, no "can only be edited as markdown" hint | — | — | `notesEditFidelity.test.mjs: 'open, change nothing, save: every note comes back byte for byte, nothing locked'` (asserts `baseline.lossy === false`) | editable (`notesEditableFlow.mjs: checkEditableInPlace`, "no locked block" / "no markdown-only hint") | covered |
| Untouched note saves byte-identical for every former lock trigger | — | — | `notesEditFidelity.test.mjs: 'open, change nothing, save: …'` (30 `CONSTRUCTS` + 4 `ACCOUNT_SHAPED`) | editable (final byte-for-byte compare) | partial: `audio`, `canvas`, `object`, `figure`, `sup`/`sub` raw-HTML siblings beyond the listed fixtures are not in the corpus (`video`, `iframe`, `svg`, `details`, `kbd`/`small`/`ins` are) |
| Edit next to a construct changes only typed text | — | — | `notesEditFidelity.test.mjs: 'typing next to a construct changes only the typed text'`, `'editing inside a construct keeps the rest of it as written'`, `'the diff aligns by line, then by character'` | editable (table cell, formula, link edits keep the other bytes) | covered |
| Patched text that would not render like the edit → canonical text saved | — | — | only the negative case: `notesEditFidelity.test.mjs: 'whitespace HTML collapses does not make an edit fall back to the canonical text'`; `'the diff aligns…'` (`patchSource` returns `null`); notesEditFidelity: 'an edit whose patched source would show differently is saved as the canonical text instead' | — | covered (unit, with a stand-in renderer) |
| Block editor cannot write → kept as stored + console warning | — | — | `notesEditFidelity.test.mjs: 'a block the editor still cannot write is kept as stored, with a warning'` | — | covered |
| Serializer fuzz with new constructs | — | — | `notesEditFidelity.test.mjs: 'fuzz: random notes, random edits in the text around the constructs'` (400 rounds) | — | covered |
| Islands behave as one character: select, delete, cut/paste, type around | — | — | `notesEditFidelity.test.mjs: 'deleting a construct removes all of it'`; `notesMarkdownSerializer.test.mjs: 'what has no rich form is an island in the editor, and lossy without its stamp'` | — | partial: delete and type-around only; select, cut/paste untested |
| Backspace at the start of the line under a code block or table moves the caret to the end of it; the line stays a line, an empty one goes away. Text the browser leaves in a `<pre>` beside its `<code>` is saved as code, not dropped | — | — | `notesMarkdownSerializer.test.mjs: 'text the browser moved into a code block next to its <code> is written as code, not dropped'` | `tests/e2e/notes-qa/notes-edge-cases.spec.mjs`: 'P1 Backspace at the start of the paragraph after a code block…', 'P1 Backspace under a code block or a table…' (Chromium) | covered; V finding 1 (QA finding 12). WebKit not run |
| Click/tap island → in-place popover, island source kept | — | — | — | editable (`checkEditableInPlace`, "The formula is an island", `.note-island-menu`) | covered |
| Aligned table: alignment kept, cells editable, alignment shown in cards | — | — | `notesEditFidelity.test.mjs` (`aligned table` fixture) | editable ("the table keeps its alignment", "the edited table cell") | partial: alignment on cards untested |
| u/sup/sub/mark saved as tags; Cmd+U, Cmd+Shift+H; toolbar; `--warn` tint | — | — | `notesMarkdownSerializer.test.mjs: 'underline, superscript, subscript and highlight are written as their tags'` | editable (underline: keyboard on desktop, toolbar on phone); coverage (`notesCoverageFlow.mjs: checkFormattingAndImageLinks`, highlight by keyboard/toolbar, sup/sub by toolbar) | partial: `--warn` highlight tint not checked |
| Link menu title field; untouched target kept, any scheme | — | — | `notesEditFidelity.test.mjs` (title, mailto, tel, `obsidian://` fixtures); `notesMarkdownSerializer.test.mjs: 'editing a block never drops a link target the renderer cannot show'` | editable ("A mailto: link, edited through the link menu, title included") | covered |
| External images: link chip, never fetched, URL/alt editable, chip movable/deletable | — | — | `notesEditFidelity.test.mjs` (external image fixtures, byte round trip) | coverage (`checkFormattingAndImageLinks`: chip, no `.invalid` request, image link menu, `javascript:` refused) | partial: chip move/delete untested |
| Whole-note "Markdown source" item still present | — | — | — | native (`notesNative.mjs: checkWindowEdges`, "Markdown source: the raw text, editable") | covered |
| SW cache `odysseus-v669-notes-edit-in-place` | — | — | — | — | none |
| Revert check | — | — | — | — | n/a (process claim, not checkable from source) |

### M — a run started from a note may edit that note (scoped, undoable)

| Behaviour (slice) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Runs started from a note get `manage_notes`; read-only notice and `disabled_tools` gone | — | `ES::test_a_note_chat_is_always_offered_manage_notes` (chat_stream, both params) | `notesAgentActions.test.mjs: 'a per-item run creates a session scoped to the note, links it and runs with manage_notes'`, `'prompts carry only the request: …'` (no "read-only. Do not edit") | agent (`notesAgentFlow.mjs: checkAgentActions`, `toolNames(...).includes('manage_notes')`) | covered |
| Prompt line "You may edit the attached note (and only it)…" with no tool name | `test_note_prompt_routing.py::test_the_show_results_notice_changes_no_routing` (reads the constant from `noteRules.js` by regex, then routes it) | — | `notesAgentActions.test.mjs: 'prompts carry only the request: …'` (static: `EDIT_SCOPE_NOTICE` equality, no `manage_` in it) | agent (`checkAgentActions`, "add to my calendar" → `manage_calendar` offered) | covered (JS part static) |
| Scope stored server-side on `sessions.note_scope_id`; client never sets it | `ES::test_the_scope_comes_from_the_session_row` | `ES::test_a_session_created_from_my_note_carries_its_scope`, `ES::test_chat_stream_binds_the_session_scope_for_the_run` | `notesAgentActions.test.mjs: 'a per-item run creates a session …'` (`note_id` on session form only) | context (step 7 creates session with `note_id`) | covered (`test_a_session_created_from_my_note_carries_its_scope` fails by test order per O) |
| `POST /api/session` with another user's note → 404 | — | `ES::test_no_session_is_scoped_to_someone_elses_note` | — | — | covered |
| Scope survives follow-up turns | — | `ES::test_the_scope_survives_a_follow_up_turn` (DB tool) | — | — | covered |
| Scoped note: update/toggle title, content, tasks, colour, tag, pin, reminder, type | — | `ES::test_a_write_on_the_attached_note_goes_through`, `ES::test_an_update_without_an_id_means_the_attached_note`, `ES::test_colour_tag_pin_and_reminder_of_the_attached_note_can_change`; ES::test_the_run_may_change_its_notes_type_and_undo_puts_it_back | — | agentedit (step 1) | covered |
| Reads allowed on all notes | — | `ES::test_reads_reach_every_note` (list/search/view/get) | — | — | covered |
| Write elsewhere, add, delete, archive refused with error naming the note | — | `ES::test_a_write_on_another_note_is_refused`, `ES::test_creating_deleting_and_archiving_are_refused`, `ES::test_archived_false_a_model_fills_in_is_ignored` | — | agentedit (`notesAgentEditFlow.mjs: checkAgentEdits`, step 3 "may change only that note") | covered |
| Read/write split reuses `capabilities_for_action` | indirect only: `ES::test_a_shared_note_asks_before_every_change` (reads stay free) | — | — | — | partial: implementation detail, no direct test |
| Stale write and write-before-read refused | — | `ES::test_a_write_from_a_stale_version_is_refused_until_the_note_is_read_again`, `ES::test_a_write_before_any_read_is_refused` | — | — | covered |
| Agent write never overwrites unsaved typing; user's next save gets 409 | — | — | auto: 'an agent edit arriving under unsaved typing is not taken; the save then meets the conflict, text kept' | — (native `checkWindowEdges` covers "Keep mine after a 409" for user-vs-user only) | covered (unit); not driven in a browser |
| `notes.agent_undo` before each write; same chat shares first snapshot; another chat replaces the slot | — | `ES::test_undo_puts_the_exact_bytes_back` (two edits, one Undo); ES::test_another_chats_edit_takes_the_undo_slot | — | — | covered |
| `POST /api/notes/{id}/agent-undo` restores exact bytes | — | `ES::test_undo_puts_the_exact_bytes_back`, `ES::test_undo_puts_colour_tag_pin_and_reminder_back` | `notesAgentEdits.test.mjs: 'Undo from the toast saves pending typing first, then restores the exact bytes'` | agentedit (step 2) | covered |
| Undo → 409 after a later change; owner only | — | `ES::test_undo_never_overwrites_a_later_change`, `ES::test_undo_is_the_owners` | `notesAgentEdits.test.mjs: 'Undo after a later change restores nothing and shows the note as it is'` | — | covered |
| Undo saves pending typing first | — | — | `notesAgentEdits.test.mjs: 'Undo from the toast saves pending typing first, …'` | — | covered |
| "Agent edited this note — Undo" toast once per edit | — | — | `notesAgentEdits.test.mjs: 'an agent edit lands in the list, its card and the open window, with one Undo toast'`, `'no toast for an edit that is no longer undoable, …'` | agentedit (step 1, toast) | covered |
| Footer/meta "Agent edited <time> · Undo" persists after toast/reload/restart | — | — | e2e:bugs 'agent Undo in the meta line after reopening' | agentedit (step 1 meta regex); coverage (`checkUndoAfterReload`, Undo after reload); notes-qa 'a reopened note still offers Undo for its last agent edit' | covered; was BUG (P2), fixed: the meta line reads ownership from the note, not `ctx.own` |
| Open window syncs to agent edits from the stream | — | — | `notesAgentEdits.test.mjs: 'an agent edit lands in the list, …'`; `notesAgentActions.test.mjs: 'the drained run reports each edit to its note as it lands, …'` | agentedit (step 1 "the edit in the open window") | covered |
| Shared note → "Allow?" on every agent write; private → no prompt | `ES::test_a_shared_note_asks_before_every_change`, `ES::test_a_private_note_gets_no_prompt`, `ES::test_outside_a_note_chat_the_gate_is_unchanged` | — | — | agentedit (step 4 held until Allow; step 1 private) | covered |
| Read-back of the run's own note skips approval | `ES::test_the_run_reads_its_own_note_back_without_a_prompt` | — | — | — | covered |
| Note→chat link saved before the run starts | — | — | `notesAgentActions.test.mjs: 'a per-item run creates a session …'` (patch before `startChat`) | — | covered |
| Note's `Id:` line in context | `ES::test_the_note_context_names_the_note_id` | — | — | — | covered |
| Collaborator cannot start a run (`canRunNoteAgent`) | — | — | `notesAgentActions.test.mjs: 'no run on a note shared with me, an empty prompt or a missing default model'`, `'per-task buttons: my saved notes only, …'` | — | covered |
| `agentedit` flow: desktop + phone, session sent with note id | — | — | `notesAgentActions.test.mjs: 'a per-item run creates a session …'` | agentedit (`checkAgentEdits`, both sizes) | covered |
| Revert check | — | — | — | — | n/a |

### N — notes in a chat's context

| Behaviour (slice) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| New chat shows no note, even with pinned notes | — | `CN::test_an_ordinary_chat_with_nothing_selected_carries_no_note` | `notesContextStrip.test.mjs: 'by default no note is on screen and nothing is read'` | context (`notesContextFlow.mjs: checkContextNotes`, steps 1 & 8) | covered |
| Select → Send to agent → a card per note above the composer; old chip hidden | — | — | `notesContextStrip.test.mjs: 'selected notes are cards with an x above the composer; …'`; `selectedNotesContext.test.mjs: 'pending selections transfer only to the materialized chat'` | context (step 2) | covered |
| Card x removes note from context; next message carries the rest | — | — | `selectedNotesContext.test.mjs: 'a context card\'s x takes that note out of the chat\'s selection only'`; `notesContextStrip.test.mjs: 'the x asks to take the note out of the context; a click or Enter opens it'` | context (step 5) | covered |
| Card click opens note window over chat, panel closed | — | — | `notesContextStrip.test.mjs: 'a note opens in the window over the chat, added to the page\'s list when it is missing'` | context (steps 3, 6) | covered |
| Sent message shows its notes; `context_notes` persisted; survives reload | `CN::test_the_user_message_records_its_context_notes` | `CN::test_a_follow_up_in_a_note_chat_carries_its_note` (`context_notes` passed) | `notesContextStrip.test.mjs: 'a sent message records id and title only'` | context (steps 4 SQLite `context_notes`, 6 reload) | covered |
| Message cards keep the title from send time | — | — | `notesContextStrip.test.mjs: 'a sent message records id and title only'` | coverage (`notesCoverageFlow.mjs: checkSentCardKeepsTitle`) | covered |
| Selection in memory per chat; chat's own note survives reload | — | — | `selectedNotesContext.test.mjs: 'new chat resets old indicator and invalid selection is rejected'` | context (step 6 "a selection does not outlive the page"; step 7 reopen) | covered |
| Chat started from a note: note shown, no x, every turn carries current version | — | `CN::test_a_follow_up_in_a_note_chat_carries_its_note` | `notesContextStrip.test.mjs: 'the chat\'s own note comes first and cannot be removed; …'` | context (step 7) | covered |
| Chat's note first, no duplicate; only my notes, in selection order | — | `CN::test_the_chats_note_comes_first_and_is_not_repeated`, `CN::test_selected_notes_keep_their_order_and_only_mine_count` | `notesContextStrip.test.mjs: 'the chat\'s own note comes first …'` | — | covered |
| Focus todo stays with first turn (N, superseded by O) | — | now asserted the other way: `RC::test_the_focus_todo_is_in_every_turn_of_the_chat` | — | — | n/a (superseded) |
| Sessions list returns `note_scope_id` | — | `CN::test_the_sessions_list_names_a_chats_note` | `notesContextStrip.test.mjs: 'the sessions list arriving later brings the chat\'s note in'` | — | covered |
| Unreadable note left out; newest copy wins; escaping; opening | — | — | `notesContextStrip.test.mjs: 'a note that cannot be read is left out, …'`, `'an older read of a note never overwrites a newer one'`, `'a slower, older read never overwrites a newer selection'`, `'titles are escaped'` | — | covered |
| Model received the note | — | `CN::test_a_follow_up_in_a_note_chat_carries_its_note` (`selected_notes_context`) | — | context (step 4 `requestWith`) | covered |
| Note is untrusted context on every turn, so first-turn approval rules apply to follow-ups | — | — | — | — | none (O tests arming per turn, not on a follow-up) |
| Chat's note is the first child of the conversation, "This chat's note", no x, nothing above composer (N `05594ee5`) | — | — | `notesContextStrip.test.mjs: 'selected notes are cards …'` (label), `'a chat started from a note shows that note at the top, and nothing above the composer'` | context (step 7, `#chat-history > :first-child`) | covered |
| Card stays first after reply, chat switch, messages reload (N `05594ee5`) | — | — | `notesContextStrip.test.mjs: 'loading the chat\'s messages keeps its note first'` | context (step 7 after reply and reselect) | covered |
| Another chat's history never gets the old note back (N `05594ee5`) | — | — | `notesContextStrip.test.mjs: 'loading the chat\'s messages keeps its note first'` (second half), `'switching to a chat with nothing in context clears the strip'` | — | covered |
| Selected notes stay above composer with x (N `05594ee5`) | — | — | `notesContextStrip.test.mjs: 'selected notes are cards with an x above the composer; …'` | context (step 2) | covered |
| Revert checks | — | — | — | — | n/a |

### O — note runs lose the clicked todo

| Behaviour (slice) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Private note does not arm the gate; search/view without approval | `RC::test_the_wrapper_stays_fenced_data_when_it_does_not_arm`, `RC::test_build_chat_context_arms_the_wrapper_only_when_told` | `RC::test_a_run_from_my_private_note_reads_notes_without_approval` | — | focus (`notesFocusFlow.mjs: checkFocusTodo`, step 1 no `.ask-user-option`) | covered |
| Shared note still taints → approval | — | `RC::test_a_run_from_a_shared_note_still_asks_before_reading`, `RC::test_one_shared_note_among_selected_ones_arms_the_gate` | — | focus (step 3) | covered |
| Skills index alone: reads go through; writes and email held | `RC::test_the_skills_index_alone_lets_reads_through_and_still_holds_writes` | — | — | — | covered |
| Focus todo stored on session at creation, in context every turn | `RC::test_focus_todo_parsing` | `RC::test_the_session_keeps_the_focus_todo_it_was_created_with`, `RC::test_the_focus_todo_is_in_every_turn_of_the_chat` | `notesAgentActions.test.mjs: 'a per-item run creates a session …'` (`note_focus_task` on session form) | focus (step 2 "the follow-up carries the focus todo") | covered (`test_the_session_keeps_…` order-dependent per O) |
| Client-sent `note_focus_task` ignored | — | `RC::test_a_requests_focus_todo_is_ignored` | `notesAgentActions.test.mjs: 'a per-item run creates a session …'`, `'the three entry points share one run; …'` (no `note_focus_task` on chat form) | — | covered |
| Every message in a note chat runs in agent mode | — | `RC::test_a_follow_up_sent_in_chat_mode_runs_as_an_agent_turn`, `RC::test_an_ordinary_chat_keeps_the_composers_mode` | — | focus (step 2, Chat-mode toggle → `manage_notes` offered) | covered |
| Approval card shows without reload when opened from toast; Allow resumes | — | — | — | focus (step 3 `startFromToast` → Allow → answer) | covered (e2e only) |
| Answer names the focus item | — | — | — | focus (step 1, stub model) | partial: stub model only; real-model check is manual |
| Revert check | — | — | — | — | n/a |

### P — 500 "Malformed request authority"

| Behaviour (slice) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Note text already implying Notes → no exception, one unrestricted `manage_notes` grant | — | `ES::test_a_note_request_in_a_note_chat_holds_one_notes_grant` (2 params, chat_stream) | — | — | covered |
| Edit on the attached note goes through; `add` refused; model explains | — | `ES::test_a_note_request_in_a_note_chat_holds_one_notes_grant` (add refused, update ok) | — | — | partial: "model explains" untested |
| Authority error → chat message with session id, "Nothing was run", no raw 500 | — | `ES::test_an_authority_that_cannot_be_built_is_an_error_in_the_chat` | — | — | partial: server body only; how the chat UI shows it is untested (still HTTP 500 status) |
| No other append-a-grant site | — | — | — | — | n/a (audit) |

### Q — note agent answers hide results

| Behaviour (slice) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| All three note prompts carry the "If you read anything…" line (Q/R) | `test_note_prompt_routing.py::test_the_show_results_notice_changes_no_routing` (6 params) | — | `notesAgentActions.test.mjs: 'every note prompt asks for what the run read in the answer, in words that leave routing alone'` | — | covered (wording equality is static) |
| Prompt must not contain "list"/"what" | `test_note_prompt_routing.py::test_the_show_results_notice_changes_no_routing` (route tuple includes `_parse_simple_notes_tool_request`) | — | same JS test (static regex `\blist`, `\bwhat`, …) | — | covered (JS half static) |
| Claim kept when every named op has a successful receipt | `CB::test_an_execution_claim_naming_a_successful_receipt_is_kept` (3 params) | — | — | — | covered |
| Still removed: no op, failed op, "remote", unverified external; test/artifact claims untouched | `CB::test_an_execution_claim_without_a_matching_successful_receipt_is_still_removed` (6), `CB::test_a_supported_claim_does_not_vouch_for_a_test_or_artifact_claim_next_to_it`; `test_effect_verification_adapters.py::test_a_named_remote_operation_with_a_receipt_still_cannot_claim_success`, `::test_reported_external_mutation_without_artifacts_is_disclosed` | — | — | — | covered |
| Notice ends "Completed this turn: …"; failed and unverified-external ops omitted | `CB::test_the_omission_notice_names_the_operations_that_did_complete`, `CB::test_the_omission_notice_names_nothing_when_nothing_completed`; `test_effect_verification_adapters.py::test_the_omission_notice_never_lists_an_unverified_external_operation` | — | — | — | covered |
| Mutation after a read in the same turn does not end on its summary; mutation alone does | — | `test_note_read_then_mutation_answer.py::test_a_toggle_after_a_list_leaves_the_answer_to_the_model`, `::test_a_toggle_on_its_own_still_ends_on_its_summary` (agent loop) | — | — | covered |
| Real check: list answer, item ticked, no retraction on follow-up | — | — | — | — | none (manual only) |

### R — "Create a new note…" in Chat mode never gets the notes tool

| Behaviour (slice) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Imperative with ≤2 qualifiers, plurals, `to-do` → notes escalation | `test_action_intents_note_create.py::test_note_create_requests_promote_to_notes` (19 params) | — | — | — | covered |
| A preposition ends the qualifier run ("a summary of these notes") | `test_action_intents_note_create.py::test_note_mentions_without_an_action_stay_plain_chat` | — | — | — | covered |
| Software noun after ("todo app") → workspace route | `test_action_intents_note_create.py::test_code_requests_about_note_apps_stay_on_the_workspace_route` (3) | — | — | — | covered |
| Naming the tool; save/put/store to my notes; save as note / turn into checklist | `test_action_intents_note_create.py::test_note_create_requests_promote_to_notes`, `::test_existing_note_phrasings_still_promote` | — | — | — | covered |
| Negatives: how-to, "notes on this paper", "take notes during…" | `test_action_intents_note_create.py::test_note_mentions_without_an_action_stay_plain_chat` | — | — | — | covered |
| Real check: a Chat-mode "create a note" request → `manage_notes add` + approval | — | — | — | — | none (manual only; no Chat-mode create e2e) |
| Approved create/edit → "Done." + `[View note: …](#note-id)`; link opens window; persisted | — | `test_approved_note_answer_link.py::test_approved_note_write_answers_with_a_link_to_the_note` (add, update; agent loop) | — | — | partial: answer text only; link click and persistence in history untested |
| Approved delete → just "Done." | — | `test_approved_note_answer_link.py::test_approved_note_delete_has_no_link` | — | — | covered |
| "Open in Deep Research" link kept likewise | — | — | — | — | none |
| Prompt wording without "list" so the forced list fallback does not hijack `view` | `test_note_prompt_routing.py::test_the_show_results_notice_changes_no_routing` | — | `notesAgentActions.test.mjs: 'every note prompt asks for …'` (static regex) | — | covered |
| First message shows request, then todo quote, then note card; notices hidden for display only | — | — | `notesAgentActions.test.mjs: 'a note run message is shown as its request, without the notices meant for the model'` (`noteRunDisplayText`) | — | partial: notice stripping only; todo quote and order (`chatRenderer.js`) and copy/edit/resend keeping full text untested |
| `HIDE_NOTE_RUN_NOTICES` rollback flag; false → shown as sent | — | — | `notesAgentActions.test.mjs: 'a note run message is shown as its request, …'` (static: asserts `=== true`) | — | partial: static only; the `false` path is untested |
| Todo recorded with its note on the message; first message only | — | `CN::test_a_chat_started_from_a_todo_records_the_todo_with_its_note` | — | — | partial: "first message only" and "old chats get no quote" untested |
| Chat history render | — | — | `historyResumeRendering.test.mjs` (11 generic tests, none about note runs) | — | partial: no note-specific case |

### S — note version history

| Behaviour (slice) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| `note_versions` table: UNDO_FIELDS, source, session_id, author | — | `NV::test_an_agent_edit_from_the_notes_chat_keeps_the_note_as_it_was` | — | — | covered |
| Version in the same transaction; a write losing CAS keeps none | — | `NV::test_restore_loses_a_race_with_a_409_and_keeps_nothing`, `NV::test_a_refused_agent_write_keeps_nothing` | — | — | covered |
| Every agent update/toggle records a version, note chat and ordinary chat | — | `NV::test_an_agent_edit_from_the_notes_chat_keeps_the_note_as_it_was`, `NV::test_an_agent_edit_from_an_ordinary_chat_keeps_a_version_too`, `NV::test_every_agent_edit_keeps_its_own_version_across_chats` | — | — | covered |
| No-op write keeps nothing; reorder, archive, `/pin` keep nothing | — | `NV::test_an_agent_write_that_changes_nothing_keeps_nothing`, `NV::test_a_save_that_changes_nothing_versioned_keeps_nothing` (`sort_order`); NV::test_the_pin_and_archive_buttons_keep_no_version | — | — | covered |
| Manual save: one version per 10-min gap; tick/added task counts | — | `NV::test_a_manual_save_keeps_a_version_at_most_once_per_gap` | — | — | covered |
| Manual save right after an agent edit always keeps a version | — | `NV::test_a_save_right_after_an_agent_edit_keeps_what_the_agent_wrote` | — | — | covered |
| Manual save by another person inside the gap keeps a version (QA 8) | — | `NV::test_a_save_by_someone_else_inside_the_gap_keeps_the_text_it_replaces` | — | notes-qa: `MAJOR the owner can get back text a collaborator overwrote a minute later` | covered |
| Cap 50, oldest dropped | — | `NV::test_the_cap_drops_the_oldest` (cap patched to 3) | — | — | covered |
| `GET …/versions`, `GET …/versions/{vid}` | — | `NV::test_the_list_is_newest_first_without_the_full_text` | `notesHistory.test.mjs: 'the list and one version come from the owner routes'` | history | covered |
| Restore with `base_updated_at`; stale → 409 + current note | — | `NV::test_restore_refuses_a_note_that_changed_meanwhile` | `notesHistory.test.mjs: 'a note that changed meanwhile restores nothing and shows the note as it is'` | — | covered |
| Restore creates a version (undoable) | — | `NV::test_restore_goes_back_and_can_itself_be_undone` | — | history (sources `restore, restore, user, agent`) | covered |
| Restore with a deleted upload → 409 | — | `NV::test_restore_refuses_an_upload_deleted_since` | `notesHistory.test.mjs: 'a version whose image was deleted says so'` | — | covered |
| Restore brings back a collaborator's image, reserved for its owner (QA 8) | — | `NV::test_restore_brings_back_a_collaborators_image` | — | — | covered |
| Agent Undo saves what it replaces as a version | — | `NV::test_the_agent_undo_keeps_what_it_replaces` | — | — | covered |
| Owner only: collaborators 403, no History button; their edits recorded by name | — | `NV::test_history_is_the_owners_and_keeps_collaborators_edits` | `notesHistory.test.mjs: 'each row says who changed what, and where'` (collaborator named); e2e:sharing (the collaborator's footer is only Leave: no History) | — | covered |
| Version id from another note rejected | — | `NV::test_a_version_id_of_another_note_is_not_found` | — | — | covered |
| Versions deleted on every delete path: API, tool, calendar reminder cleanup, account purge, admin wipe | `NV::test_purging_an_account_deletes_its_notes_versions` | `NV::test_deleting_the_note_deletes_its_versions`, `NV::test_the_agent_deleting_the_note_deletes_its_versions`; NV::test_a_calendar_event_deleting_its_reminder_deletes_the_reminders_versions | — | — | covered except the admin wipe (one `delete()` of the whole table, read in review) |
| UI: History button, list newest first, labels, full version, Restore, "Open chat" | — | — | — | history (`notesHistoryFlow.mjs: checkHistory`, footer icon, rows, restore, chat link) | covered (S "Before …" labels replaced by T sentences) |
| Pending typing saved before history opens and before restore; 409 messages | — | — | `notesHistory.test.mjs: 'Restore saves pending typing first, …'`, `'a restore that fails for another reason, …'`, `'the restore goes ahead when saving pending typing fails, …'` | — | partial: saving before the history opens is untested |
| Fixtures updated | — | `test_notes_checklist_state.py` creates `NoteVersion` table | — | — | n/a |

### T — readable version history

| Behaviour (slice) | Python unit | Python route / integration | JS unit | E2E flow | Status |
|---|---|---|---|---|---|
| Rows describe who and what, compared with the next newer version | `HU::test_describe_change_says_what_happened` (11 params) | `HU::test_each_row_says_what_the_change_after_it_did` | `notesHistory.test.mjs: 'each row says who changed what, and where'` | history (rows "You edited the text", "Agent in … ticked …") | covered |
| Long task quoted and truncated | `HU::test_a_long_task_is_quoted_short` | — | — | — | covered |
| `summary`, `chat_title`, `chat_exists`; owner's chats only; deleted chat not named | — | `HU::test_a_row_names_its_chat_and_a_deleted_chat_is_not_named`, `HU::test_another_accounts_chat_title_is_never_shown` | `notesHistory.test.mjs: 'each row says who changed what, and where'` ("a deleted chat") | history (chat link text) | covered |
| "Now" row; Today/Yesterday/date groups; relative times; exact-time tooltip | — | — | `notesHistory.test.mjs: 'times read as a person says them'`, `'rows are grouped by day, newest first'` | history (Now row, `[title]` tooltip, day heading) | covered |
| Selected row diff against now: fields then lines, `--red`/`--fg`, fold, LCS cap 250k cells | — | — | `notesHistory.test.mjs: 'the line diff keeps order and marks what goes and what comes back'`, `'the diff against now covers the title and fields, …'`, `'long unchanged stretches fold away'`; notesHistory: 'a rewrite too big to compare line by line shows the old middle removed, then the new one added, ends kept' | history (line-through on removed lines) | partial: colours are CSS only, not asserted |
| "Undo up to here" (tooltip "Restore this version"); "Same as now" | — | — | `notesHistory.test.mjs: 'the diff against now covers …'` (`same: true`) | history (button text, exact bytes back) | partial: tooltip and "Same as now" label untested |
| Restore toast + Undo; `undo_version_id`; no Undo when nothing kept | — | `HU::test_a_restore_says_which_version_undoes_it` | `notesHistory.test.mjs: 'the restore toast names the version and its Undo restores what the restore replaced'`, `'a restore that kept nothing offers no Undo'` | history (toast Undo) | covered |
| `showToast` optional second action in `static/js/ui.js` | — | — | JS tests stub `showToast` and never load `ui.js` | history (`checkSeeWhatChanged`, toast "See what changed") | partial: e2e only |
| "History (N)" in meta; `version_count`; none for collaborators | — | `HU::test_the_note_carries_its_version_count_in_the_list_and_alone`, `HU::test_a_collaborator_does_not_get_the_count` | — | history ("History (2)" → "History (4)") | covered |
| "See what changed" on toast and chat tool row opens History on that chat's edit | — | — | `notesAgentEdits.test.mjs: 'the toast offers See what changed, on the chat of the edit, when the history can open'` | history (`openNoteHistory(id,{sessionId})`, `checkSeeWhatChanged` toast + `a[data-note-history]`) | covered |
| API backward compatible with S | — | indirect: `NV::test_the_list_is_newest_first_without_the_full_text` still asserts S fields | — | — | partial: no explicit compatibility test |
| `bd65bc29` agent-edit flow selector update | — | — | — | agentedit (`.note-window-meta-undo:not(.note-window-meta-history)`); coverage (`checkUndoAfterReload`) | covered |

## Known bugs (failing tests, kept red)

Found while filling the gaps above. The fixes are a separate round; each test stays red until then. The e2e ones live in `tests/browser/notesBugsFlow.mjs`, which runs after every other flow and fails the run at the very end.

| Pri | Bug | Test |
|---|---|---|
| P2 (fixed) | A note that already has an undoable agent edit opens without "Agent edited · Undo" in its meta line: `open()` calls `showMeta` before `applyAccess` sets `ctx.own` (`ui/noteWindow.js`). Undo only shows if the edit arrives while the window is open. | e2e `bugs`: agent Undo in the meta line after reopening |
| P3 (fixed) | The share popover ignores every click while a change saves (`apply()` returns while `busy`), after already showing the change: Edit clicked right after switching someone on is dropped, the note stays View. This is why `sharing` failed "on a share pill" since K. Fixed: changes made while one saves are queued and saved in order (`ui/sharePopover.js`). | e2e `bugs`: share pill clicked while saving |
| P3 | A delete or leave that fails because the network is down re-reads the list to put the card back; that read fails too and `_fetchNotes` empties the list, so every card disappears until a later read. | `notesWorkflows.test.mjs`: a delete or leave that fails offline keeps every card on screen; e2e `bugs`: cards kept after a delete fails offline |
| P3 | The service worker's precache (`static/sw.js`) has every Notes module but the three S and T added (`application/noteHistory.js`, `domain/historyText.js`, `ui/historyPanel.js`), and its `CACHE_NAME` was not bumped after O. Offline, `features/notes/index.js` cannot import them and Notes does not load; online is unaffected (JS is network-first). | `test_notes_reference_browser.py::test_notes_complete_graph_imports_first_time_offline` (passes on `dev`) |
| P3 | N added `contextNotes` to the Notes facade's exports without updating the public-identity check, which now fails. Either the export is meant (update `tests/browser/notesReference.mjs`) or it should not be public. | `test_notes_reference_browser.py::test_notes_reference_desktop_mobile_and_public_identity` (passes on `dev`) |

## Measured coverage

Measured on 2026-11-13 on a shared 4-core test machine, `test/notes-t-history-ux @ bd65bc29` (A to T on `dev @ 4d0610a1`). "Before" is that commit as it was; "after" adds the tests this matrix introduced, and no product code.

### Python (coverage.py, branch mode)

Before: the whole suite under coverage. After: the same data combined with a run of every test file added or changed with this matrix (no product code changed, so the union is what the whole suite would measure). "Stack-changed" counts only the lines the Notes stack changed in that file (`git diff 4d0610a1 bd65bc29`), which is the fair figure for the shared files (`chat_routes.py` and the like) and for `note_routes.py`, whose uncovered rest is `dispatch_reminder` (email, ntfy and webhook delivery, LLM synthesis): `dev` code the spec already lists under Current Gaps. Bold: the Notes modules.

| File | Lines before → after | Branches before → after | Stack-changed branches before → after |
|---|---|---|---|
| **`core/note_agent_scope.py`** | 92.8 → 98.6 % | 85.0 → 100.0 % (40/40) | 85.0 → 100.0 % (40/40) |
| **`core/note_versions.py`** | 93.8 → 98.1 % | 87.9 → 95.5 % (63/66) | 87.9 → 95.5 % (63/66) |
| **`core/note_writes.py`** | 95.2 → 97.6 % | 83.3 → 91.7 % (11/12) | 83.3 → 91.7 % (11/12) |
| **`core/notes_markdown.py`** | 97.2 → 97.2 % | 92.4 → 92.4 % (146/158) | 92.4 → 92.4 % (146/158) |
| **`core/notes_sharing.py`** | 94.2 → 100.0 % | 81.8 → 100.0 % (44/44) | 81.8 → 100.0 % (44/44) |
| **`routes/note/note_routes.py`** | 86.2 → 87.5 % | 72.5 → 75.6 % (195/258) | 91.5 → 98.9 % (93/94) |
| **`src/tools/notes.py`** | 89.1 → 90.9 % | 82.5 → 86.2 % (207/240) | 93.9 → 100.0 % (82/82) |
| `core/auth.py` | 80.4 → 80.4 % | 76.4 → 76.4 % (110/144) | — → — % (0/0) |
| `core/database.py` | 75.5 → 75.5 % | 64.4 → 64.4 % (277/430) | 78.6 → 78.6 % (44/56) |
| `routes/admin_wipe/admin_wipe_routes.py` | 31.9 → 31.9 % | 25.0 → 25.0 % (8/32) | — → — % (0/0) |
| `routes/auth_routes.py` | 72.8 → 72.8 % | 61.5 → 61.5 % (150/244) | 100.0 → 100.0 % (4/4) |
| `routes/calendar_routes.py` | 57.2 → 57.2 % | 48.6 → 48.6 % (201/414) | — → — % (0/0) |
| `routes/chat_helpers.py` | 72.1 → 72.1 % | 59.5 → 59.5 % (238/400) | 75.0 → 75.0 % (3/4) |
| `routes/chat_routes.py` | 71.0 → 71.0 % | 60.8 → 60.8 % (749/1232) | 89.5 → 89.5 % (34/38) |
| `routes/codex_routes.py` | 42.3 → 42.3 % | 19.4 → 19.4 % (36/186) | 100.0 → 100.0 % (2/2) |
| `routes/session_routes.py` | 48.3 → 48.3 % | 37.1 → 37.4 % (130/348) | 100.0 → 100.0 % (8/8) |
| `routes/upload_routes.py` | 77.9 → 78.7 % | 70.3 → 72.3 % (107/148) | 71.4 → 92.9 % (13/14) |
| `src/action_intents.py` | 95.7 → 95.7 % | 85.7 → 85.7 % (12/14) | — → — % (0/0) |
| `src/agent_evidence.py` | 92.8 → 92.8 % | 86.2 → 86.2 % (238/276) | 100.0 → 100.0 % (12/12) |
| `src/agent_loop.py` | 66.2 → 66.2 % | 59.1 → 59.1 % (5538/9368) | 75.0 → 75.0 % (3/4) |
| `src/agent_runtime/completion.py` | 96.5 → 96.5 % | 95.8 → 95.8 % (136/142) | 100.0 → 100.0 % (4/4) |
| `src/auth_helpers.py` | 97.0 → 97.0 % | 93.8 → 93.8 % (45/48) | 100.0 → 100.0 % (6/6) |
| `src/builtin_actions.py` | 58.7 → 58.7 % | 49.6 → 49.6 % (379/764) | 100.0 → 100.0 % (6/6) |
| `src/clean_agent_preview.py` | 89.9 → 89.9 % | 84.2 → 84.2 % (1831/2174) | — → — % (0/0) |
| `src/constants.py` | 93.6 → 93.6 % | 62.5 → 62.5 % (5/8) | — → — % (0/0) |
| `src/tool_capabilities.py` | 93.1 → 94.0 % | 88.9 → 89.5 % (145/162) | 88.9 → 94.4 % (17/18) |
| `src/tool_schemas.py` | 75.1 → 75.1 % | 70.5 → 70.5 % (227/322) | — → — % (0/0) |
| `src/tools/calendar.py` | 80.0 → 80.0 % | 70.0 → 70.0 % (196/280) | — → — % (0/0) |

Below 85 % on the stack's own lines: `core/database.py` (78.6 %: the guards for a malformed legacy table, which a fresh schema cannot produce), `routes/chat_helpers.py` and `src/agent_loop.py` (3 of 4 each), and `notes_markdown.py`'s and `note_versions.py`'s last branches are defensive. `commit_if_unchanged`'s branch for a row with no `updated_at` cannot be reached: the current schema makes the column `NOT NULL`.

### JS (V8, c8)

Before: the Notes node tests as they were, plus the existing browser flows in Chromium. After: every Notes node test plus the full flow set (five Chromium runs plus one measuring run). Chromium drops a page's counts when it navigates, so `tests/browser/notesCoverage.mjs` saves them before every `reload` and `goto`; both sides were measured that way. V8 only counts the branches of code that ran, so a file's branch total grows as more of it runs: compare the covered counts, not only the percentage. WebKit has no coverage API.

| File | Lines before → after | Branches covered/seen before → after (after %) |
|---|---|---|
| `application/agentActions.js` | 96.4 → 100.0 % | 55/71 → 75/84 (89.3 %) |
| `application/agentEdits.js` | 98.6 → 100.0 % | 31/37 → 45/45 (100.0 %) |
| `application/cardOperations.js` | 67.1 → 100.0 % | 14/19 → 44/45 (97.8 %) |
| `application/checklistOperations.js` | 94.0 → 100.0 % | 64/81 → 89/96 (92.7 %) |
| `application/deletion.js` | 82.4 → 100.0 % | 15/23 → 38/41 (92.7 %) |
| `application/noteHistory.js` | 98.2 → 100.0 % | 23/30 → 41/41 (100.0 %) |
| `application/noteSession.js` | 98.5 → 100.0 % | 125/152 → 141/160 (88.1 %) |
| `application/noteVersions.js` | 100.0 → 100.0 % | 15/16 → 15/16 (93.8 %) |
| `application/queries.js` | 67.6 → 100.0 % | 28/44 → 71/80 (88.8 %) |
| `application/reminders.js` | 93.1 → 93.1 % | 77/86 → 77/86 (89.5 %) |
| `application/selection.js` | 93.3 → 100.0 % | 15/19 → 22/23 (95.7 %) |
| `application/state.js` | 98.8 → 98.8 % | 74/77 → 75/78 (96.2 %) |
| `application/workflows.js` | 73.4 → 89.9 % | 14/22 → 21/28 (75.0 %) |
| `domain/contextNotes.js` | 100.0 → 100.0 % | 31/35 → 31/35 (88.6 %) |
| `domain/historyText.js` | 98.9 → 100.0 % | 102/117 → 111/125 (88.8 %) |
| `domain/markdownSerializer.js` | 97.7 → 97.7 % | 288/319 → 290/321 (90.3 %) |
| `domain/markdownTasks.js` | 100.0 → 100.0 % | 102/111 → 102/111 (91.9 %) |
| `domain/noteRules.js` | 99.5 → 100.0 % | 101/113 → 103/113 (91.2 %) |
| `domain/recurrence.js` | 97.4 → 97.4 % | 134/148 → 141/154 (91.5 %) |
| `domain/sourceFidelity.js` | 100.0 → 100.0 % | 83/90 → 86/92 (93.5 %) |
| `index.js` | 94.8 → 97.1 % | 176/197 → 182/203 (89.7 %) |
| `infrastructure/http.js` | 100.0 → 100.0 % | 38/58 → 73/75 (97.3 %) |
| `infrastructure/notifications.js` | 75.0 → 100.0 % | 9/13 → 17/17 (100.0 %) |
| `infrastructure/storage.js` | 100.0 → 100.0 % | 25/32 → 25/32 (78.1 %) |
| `ui/cardChecklist.js` | 61.6 → 61.6 % | 23/28 → 23/28 (82.1 %) |
| `ui/cardInteractions.js` | 54.6 → 54.6 % | 32/38 → 32/38 (84.2 %) |
| `ui/cardRendering.js` | 87.4 → 87.4 % | 157/176 → 158/177 (89.3 %) |
| `ui/cardReorder.js` | 21.2 → 21.2 % | 11/12 → 11/12 (91.7 %) |
| `ui/clipboard.js` | 18.1 → 44.7 % | 2/2 → 5/9 (55.5 %) |
| `ui/colors.js` | 95.0 → 95.0 % | 29/34 → 29/34 (85.3 %) |
| `ui/contextStrip.js` | 100.0 → 100.0 % | 87/101 → 88/102 (86.3 %) |
| `ui/drawing.js` | 56.6 → 56.6 % | 26/47 → 26/47 (55.3 %) |
| `ui/filters.js` | 72.7 → 88.7 % | 43/65 → 68/87 (78.2 %) |
| `ui/historyPanel.js` | 93.8 → 93.8 % | 61/79 → 61/79 (77.2 %) |
| `ui/layout.js` | 96.4 → 96.4 % | 71/78 → 71/78 (91.0 %) |
| `ui/lifecycle.js` | 100.0 → 100.0 % | 22/23 → 22/23 (95.7 %) |
| `ui/mobileReorder.js` | 29.0 → 29.0 % | 7/10 → 7/10 (70.0 %) |
| `ui/noteAgent.js` | 100.0 → 100.0 % | 48/58 → 48/58 (82.8 %) |
| `ui/noteImages.js` | 100.0 → 100.0 % | 32/36 → 32/36 (88.9 %) |
| `ui/noteTags.js` | 96.7 → 96.7 % | 39/48 → 39/48 (81.2 %) |
| `ui/noteWindow.js` | 95.9 → 96.6 % | 293/364 → 297/369 (80.5 %) |
| `ui/panel.js` | 75.5 → 80.7 % | 44/80 → 63/100 (63.0 %) |
| `ui/quickAdd.js` | 78.9 → 78.9 % | 7/8 → 7/8 (87.5 %) |
| `ui/reminderDisplay.js` | 95.3 → 96.1 % | 35/40 → 37/41 (90.2 %) |
| `ui/reminderPicker.js` | 15.6 → 79.6 % | 7/10 → 52/84 (61.9 %) |
| `ui/richBody.js` | 83.9 → 88.2 % | 335/424 → 356/444 (80.2 %) |
| `ui/selection.js` | 98.5 → 98.5 % | 10/12 → 10/12 (83.3 %) |
| `ui/sharePopover.js` | 98.6 → 98.6 % | 86/96 → 91/100 (91.0 %) |
| `ui/text.js` | 94.9 → 94.9 % | 16/23 → 16/23 (69.6 %) |
| `ui/toolbar.js` | 67.9 → 93.6 % | 8/11 → 18/18 (100.0 %) |
| `ui/windowLayout.js` | 63.1 → 63.1 % | 10/19 → 10/19 (52.6 %) |

Totals: lines 81.7 % → 86.7 %, branches 3180/3832 → 3592/4185 (83.0 % → 85.8 %). Files under 85 %, and why:

- Drag and drop (`ui/cardReorder.js`, `ui/mobileReorder.js`, `ui/windowLayout.js`): real mouse and touch drags of cards and of the docked panel. The unit tests cover the order they produce; a synthetic drag would test Playwright more than the app.
- The sketch canvas (`ui/drawing.js`): pointer strokes on a canvas. The flow inserts a sketch; the strokes are not driven.
- `ui/clipboard.js`: its menu sits in the card action row the cards hide (H); Copy itself is covered through Ctrl/Cmd+C.
- `ui/panel.js`: the rest is docking, minimising and the first-open hint.
- `ui/reminderPicker.js`: the monthly and nth-weekday repeat menus are not driven (weekly is).
- `ui/historyPanel.js`, `ui/noteWindow.js`, `ui/richBody.js` (77 to 80 %): the branches left are keyboard and touch variants of paths the flows drive with the mouse; worth a later pass, not done here.

## E2E repetitions (2026-11-13)

Five full runs per engine, phone size as Playwright device profiles (Pixel 7 under Chromium, iPhone 15 under WebKit), every flow run even after another fails. Passes per flow and size:

| Flow | Chromium desktop | Chromium phone | WebKit desktop | WebKit phone |
|---|---|---|---|---|
| `native` | 5/5 | 5/5 | 5/5 | 5/5 |
| `gaps` | 5/5 | 5/5 | 5/5 | 5/5 |
| `editable` | 5/5 | 5/5 | 5/5 | 5/5 |
| `ui` | 5/5 | 5/5 | 5/5 | 5/5 |
| `sharing` | 5/5 | 5/5 | 4/5 **1 failed** | 5/5 |
| `agent` | 5/5 | 5/5 | 3/5 **2 failed** | 3/5 **2 failed** |
| `agentedit` | 5/5 | 5/5 | 5/5 | 5/5 |
| `context` | 5/5 | 5/5 | 4/5 **1 failed** | 5/5 |
| `focus` | 5/5 | 5/5 | 4/5 **1 failed** | 5/5 |
| `history` | 5/5 | 5/5 | 4/5 **1 failed** | 5/5 |
| `coverage` | 5/5 | 5/5 | 5/5 | 5/5 |
| `accounts` | 5/5 | 5/5 | 5/5 | 5/5 |

Chromium never failed in these five runs. Five of the six WebKit failures have one shape: a click or tap on a card does not open its window (or Playwright reports the card "not stable" for 30 s), late in a run on a loaded machine. `agent` passes in WebKit when run alone, and a probe found no card movement beyond the one scroll that brings a card into view after its window closes; the cause is not found. The sixth is `context` waiting more than 30 s for the stub model's reply. The e2e bug checks (`bugs`) failed in every run that reached them, as they should.

Flakes found in earlier attempts the same day, and what was done:

| Flake | Cause | Done |
|---|---|---|
| `native`, phone: image click "outside of the viewport" (T saw it 2 in 9) | the click came while the sheet was still sliding up | the step waits for the window's animations to settle (cancelled ones count as settled) |
| `history`: "the diff shows the agent text going" | the diff was read before the version had been fetched | waits for the diff text |
| `gaps`: "no draft left for the first note" | localStorage read before the page had the save's answer | waits for the draft to go |
| `sharing`: Edit pill never active (K: "fails on a share pill, same on base") | the product drops a click made while the previous change saves | fixed in the popover (clicks are queued); the flow clicks Edit right away again; `bugs` holds the request to pin it |
| `focus`, Chromium desktop, 2 runs in a row: `ECONNRESET` on a note read | the app's 45 s request timeout fired; not seen again in the five runs | not found |
| whole runs: server not up in 45 s, flows over 900 s | the shared disk was saturated | `NOTES_SERVER_START_TIMEOUT`, `NOTES_RUN_TIMEOUT` |

## What no test here can check

- A real iPhone or Safari on iOS: focus zoom, the on-screen keyboard over the sheet, real touch scrolling. WebKit at phone size is the stand-in.
- A real OS drag (files from the desktop, card reorder with a real mouse or finger). The flows drop a synthetic `DataTransfer`.
- A real model's choices. Every automated agent run uses `tests/browser/openai_stub.py`; the slices' real-model checks (O, Q, R) were manual.
- Firefox, Docker, Windows.
