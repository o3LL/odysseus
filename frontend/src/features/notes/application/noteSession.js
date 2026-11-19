/** One open note's edit session: dirty flag, debounced autosave, stale-write conflicts and the local draft backup. The view supplies collect() and reacts through effects. */
import { itemsToMarkdown, parseTaskLines, toggleTask as toggleTaskLine } from '../domain/markdownTasks.js';
import { joinSegments, splitMarkdownBlocks } from '../domain/markdownSerializer.js';

export const AUTOSAVE_DELAY_MS = 600;
// The first edit is backed up at once; the edits that follow within this
// window are backed up together, so a keystroke in a long note does not
// serialize the whole document every time.
export const DRAFT_BACKUP_INTERVAL_MS = 300;
// A new note with nothing but empty checkboxes in it is still empty.
const isBlank = text => !String(text || '').replace(/^[ \t]*[-*+][ \t]+\[[ xX]\][ \t]*$/gm, '').trim();
// The text a window shows for a stored note it has not edited: the editor
// reads CRLF as LF and drops blank lines at either end and runs of them
// between blocks. Only for comparing; nothing is saved from it.
const asShown = text => joinSegments(splitMarkdownBlocks(text).map((block, group) => ({ group, source: block.source })));

/** The task indices whose box differs between two versions of a note, when that is all that differs; null otherwise. */
export function taskFlips(before, after) {
  const was = parseTaskLines(before);
  const now = parseTaskLines(after);
  if (was.length !== now.length) return null;
  let content = before || '';
  const flips = [];
  now.forEach((item, index) => {
    if (item.done === was[index].done) return;
    flips.push(index);
    content = toggleTaskLine(content, index).content;
  });
  return content === (after || '') ? flips : null;
}

export function createNoteSession({
  api,
  persistence,
  scheduler,
  clock,
  note = null,
  collect,
  meta = {},
  firstWrite = () => ({}),
  // A note shared with this account as view only: nothing is saved.
  readOnly = () => false,
  delay = AUTOSAVE_DELAY_MS,
  effects = {},
}) {
  let id = note?.id || null;
  let base = note?.updated_at || null;
  let saved = { title: note?.title || '', content: note?.content || '' };
  // `saved` is the title and text of version `base`. Not so after Restore
  // (the draft's older base) or Keep mine (the newer copy's base).
  let savedIsBase = true;
  const pendingMeta = { ...meta };
  let dirty = false;
  let timer = null;
  let backupTimer = null;
  let backupPending = false;
  let pending = Promise.resolve();
  let conflict = null;
  // The server answered 404: the note was deleted, its owner's account was
  // removed, or it is no longer shared with this account. Nothing more is
  // written, and the draft goes too: no note will ever offer it back.
  let gone = false;
  let wrote = false;
  const key = () => persistence._draftKey(id || '__new__');

  // Every write goes through one queue, so a colour change can never land
  // between a content save and its response.
  const enqueue = work => {
    const run = pending.then(work);
    pending = run.catch(() => {});
    return run;
  };

  // collect() may return `unsaved`: the note with an edit the editor could
  // not write (it kept the block's stored text in `content`). That edit is
  // what the draft holds, and it stays on this device until it is gone.
  let holding = false;
  function hold(draft) {
    const now = draft.unsaved != null;
    if (now !== holding) { holding = now; effects.unsaved?.(now); }
    return now;
  }

  function backup(draft = collect()) {
    const record = JSON.stringify({ _ts: clock.now(), title: draft.title, content: draft.unsaved ?? draft.content, base_updated_at: base });
    try { persistence.setItem(key(), record); }
    catch { effects.backupUnavailable?.(); }
    return record;
  }

  function flushBackup() {
    scheduler.clearTimeout(backupTimer);
    backupTimer = null;
    if (!backupPending) return;
    backupPending = false;
    if (dirty && !gone && !readOnly()) backup();
  }

  function scheduleBackup() {
    if (backupTimer !== null) { backupPending = true; return; }
    backup();
    backupTimer = scheduler.setTimeout(flushBackup, DRAFT_BACKUP_INTERVAL_MS);
  }

  function markGone() {
    if (gone) return;
    gone = true;
    dirty = false;
    scheduler.clearTimeout(timer);
    timer = null;
    scheduler.clearTimeout(backupTimer);
    backupTimer = null;
    backupPending = false;
    persistence._clearDraft(id || '__new__');
    effects.gone?.();
  }

  function adopt(serverNote, fallback) {
    if (!serverNote) return;
    id = serverNote.id || id;
    base = serverNote.updated_at ?? base;
    savedIsBase = true;
    saved = { title: serverNote.title ?? fallback?.title ?? saved.title, content: serverNote.content ?? fallback?.content ?? saved.content };
  }

  // The note now has an id, but a held edit, or typing done while the
  // create request was out, was backed up under the new-note key. Left
  // there, the next new note would offer it back and Restore would create
  // the note a second time; it moves to this note, on the version just made.
  function settleNewDraft(draftKey, record, held) {
    let left = null;
    try { left = persistence.getItem(draftKey); persistence.removeItem(draftKey); } catch { return; }
    if (left == null || (left === record && !held)) return;
    let moved = null;
    try { moved = JSON.parse(left); } catch { return; }
    if (!moved || typeof moved !== 'object') return;
    try { persistence.setItem(key(), JSON.stringify({ ...moved, base_updated_at: base })); }
    catch { effects.backupUnavailable?.(); }
  }

  async function save(rebased = false) {
    if (readOnly()) { dirty = false; return; }
    if (!dirty || conflict || gone) return;
    const draft = collect();
    const held = hold(draft);
    if (draft.title === saved.title && draft.content === saved.content) {
      dirty = false;
      if (held) backup(draft);
      else persistence._clearDraft(id || '__new__');
      return;
    }
    if (!id && !draft.title.trim() && isBlank(draft.content)) { dirty = false; return; }
    backupPending = false;
    const record = backup(draft);
    const draftKey = key();
    // The first write of a session also carries what the old Save button
    // sent once, such as the sort order that lifts an edited note to the top.
    const extras = wrote ? {} : firstWrite();
    dirty = false;
    try {
      if (!id) {
        const created = await api.saveNote({ ...pendingMeta, ...extras, title: draft.title, content: draft.content });
        wrote = true;
        adopt(created, draft);
        settleNewDraft(draftKey, record, held);
        effects.created?.(created);
        return;
      }
      const body = { ...extras, title: draft.title, content: draft.content };
      if (base) body.base_updated_at = base;
      const result = await api.patchNote(id, body);
      wrote = true;
      adopt(result, draft);
      if (!held && persistence.getItem(draftKey) === record) persistence.removeItem(draftKey);
      effects.saved?.(result);
    } catch (error) {
      if (error?.status === 409) {
        const server = error.body?.detail?.note || null;
        // Only the colour, tag, pin or another field outside the text moved
        // on: the title and text this edit started from are still the stored
        // ones, so writing it on the newer version overwrites nothing. Once.
        if (!rebased && savedIsBase && server?.updated_at && server.updated_at !== base && server.title === saved.title && server.content === saved.content) {
          base = server.updated_at;
          dirty = true;
          return save(true);
        }
        conflict = { local: draft, server };
        effects.conflict?.(conflict);
        return;
      }
      if (error?.status === 404 && id) { markGone(); return; }
      // The window never sends an owner-only field for a collaborator, so a
      // 403 on a text save means the note is now view only for this account.
      // Retrying cannot help; the draft stays on this device.
      if (error?.status === 403 && id) { effects.viewOnly?.(error); return; }
      dirty = true;
      effects.failed?.(error);
    }
  }

  function flush() {
    flushBackup();
    scheduler.clearTimeout(timer);
    timer = null;
    if (!dirty || conflict || gone) return pending;
    return enqueue(save);
  }

  /** A real edit happened: back it up, save it after the debounce. */
  function markDirty() {
    if (readOnly() || gone) return;
    dirty = true;
    scheduleBackup();
    if (conflict) return;
    scheduler.clearTimeout(timer);
    timer = scheduler.setTimeout(() => { timer = null; enqueue(save); }, delay);
  }

  /** Colour, tags, reminder, archive: never conflict-checked. A new note keeps them for its first save. */
  function patchMeta(patch) {
    if (!id) { Object.assign(pendingMeta, patch); return Promise.resolve(null); }
    return enqueue(async () => {
      const result = await api.patchNote(id, patch);
      // Only move the base on when the content is still what this session
      // last saved; otherwise someone else's edit would be silently accepted.
      if (result && result.content === saved.content && (result.title ?? saved.title) === saved.title) base = result.updated_at ?? base;
      return result;
    });
  }

  /** Checkbox flips go through the toggle endpoint, after any pending text is saved. */
  function toggleTask(resolveIndex) {
    return enqueue(async () => {
      scheduler.clearTimeout(timer);
      timer = null;
      if (dirty && !conflict) await save();
      // A failed save leaves the edit dirty; the view then flips the box as a
      // content edit instead, so the click is not lost.
      if (dirty || conflict || gone || !id) return null;
      const index = resolveIndex();
      if (index == null || Number.isNaN(index)) return null;
      // The text of the task the box shows, so a tick can't land on another
      // task if lines were added or removed elsewhere since the last save.
      const task = parseTaskLines(saved.content)[index];
      const text = task?.text;
      // The state the box is going to, so a tick made elsewhere since the
      // last save is kept instead of undone.
      const done = task ? !task.done : undefined;
      // What the server should hold once this tick lands, if nothing else
      // was written since the last save.
      const before = { title: saved.title, content: saved.content };
      let result;
      try { result = await api.toggleTask(id, index, text, done); }
      catch (error) {
        if (error?.status === 404) markGone();
        // The task changed elsewhere: take the server copy; the view redraws from it.
        // Not over text typed while the tick was out: its save must still be
        // checked against the newer copy, so the base stays where it was.
        if (error?.status === 409 && error.body?.detail?.note && !dirty) adopt(error.body.detail.note);
        throw error;
      }
      // Text typed while the tick was out was typed on the copy the window
      // shows. If the answer holds more than this tick (a collaborator's
      // edit not received yet), the window never showed it: moving the base
      // there would let the next save write over it unseen. The base stays,
      // so that save meets the newer copy and asks Keep mine or Reload.
      if (dirty && !tickOnly(before, index, result?.note)) return result;
      adopt(result?.note);
      return result;
    });
  }

  function tickOnly(before, index, note) {
    if (!note || (note.title ?? before.title) !== before.title) return false;
    try { return note.content === toggleTaskLine(before.content, index).content; }
    catch { return false; }
  }

  function resolveConflict(choice) {
    if (!conflict) return pending;
    const server = conflict.server;
    conflict = null;
    if (choice === 'reload') {
      adopt(server);
      dirty = false;
      persistence._clearDraft(id || '__new__');
      effects.reloaded?.(server);
      return pending;
    }
    // Keep mine: write the local text over the newer server copy.
    base = server?.updated_at ?? base;
    savedIsBase = false;
    dirty = true;
    return enqueue(save);
  }

  /** An earlier unsaved draft of this note on this device, if it differs from what was loaded. */
  function storedDraft() {
    let draft = null;
    try { draft = JSON.parse(persistence.getItem(key()) || 'null'); } catch { draft = null; }
    if (!draft || typeof draft !== 'object') return null;
    const content = typeof draft.content === 'string' ? draft.content : (Array.isArray(draft.items) ? itemsToMarkdown(draft.items) : null);
    const title = typeof draft.title === 'string' ? draft.title : saved.title;
    if (content === null) return null;
    if (content === saved.content && title === saved.title) { persistence._clearDraft(id || '__new__'); return null; }
    return { title, content };
  }

  /**
   * The person chose Restore: the draft's text is saved against the version
   * it was typed on, not the one just loaded, so a newer copy written since
   * meets the conflict choice instead of being overwritten. Call it before
   * the restored text is put in the window, whose edit backs up the draft
   * again. A draft with no recorded version keeps the loaded one.
   */
  function restoreDraft() {
    let draft = null;
    try { draft = JSON.parse(persistence.getItem(key()) || 'null'); } catch { draft = null; }
    if (typeof draft?.base_updated_at === 'string' && draft.base_updated_at) { base = draft.base_updated_at; savedIsBase = false; }
  }

  /** Stop without saving: the note is being deleted. */
  function abandon() {
    scheduler.clearTimeout(timer);
    timer = null;
    scheduler.clearTimeout(backupTimer);
    backupTimer = null;
    backupPending = false;
    dirty = false;
    conflict = null;
    persistence._clearDraft(id || '__new__');
  }

  return {
    markDirty,
    abandon,
    /**
     * A same-tab write elsewhere (a card tick, a card item edit) landed while
     * the window is open. `current` is what the window shows now. Returns
     * 'base' when that write left the text as saved, 'take' when the window
     * has nothing unsaved and adopts it (the view then redraws), and 'keep'
     * when it has: the base stays, so the next save meets the conflict choice
     * instead of writing the old text over the newer version.
     */
    follow(serverNote, current) {
      if (!serverNote?.updated_at) return 'keep';
      const title = serverNote.title ?? saved.title;
      const content = serverNote.content ?? saved.content;
      if (content === saved.content && title === saved.title) { base = serverNote.updated_at; return 'base'; }
      if (dirty || conflict || gone || holding || current?.title !== saved.title || typeof current?.content !== 'string' || asShown(current.content) !== asShown(saved.content)) return 'keep';
      adopt(serverNote);
      return 'take';
    },
    /** Take a newer server copy while nothing local is pending. */
    replace: serverNote => { if (!dirty) adopt(serverNote); },
    flush,
    /** The page is going away: write a pending draft backup now. */
    flushBackup,
    patchMeta,
    toggleTask,
    resolveConflict,
    storedDraft,
    restoreDraft,
    discardDraft: () => persistence._clearDraft(id || '__new__'),
    close: () => flush(),
    get id() { return id; },
    get base() { return base; },
    get saved() { return { ...saved }; },
    get dirty() { return dirty; },
    get conflict() { return conflict; },
    get gone() { return gone; },
    get meta() { return { ...pendingMeta }; },
  };
}
