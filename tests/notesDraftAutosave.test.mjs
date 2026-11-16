import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesPersistence } from '../frontend/src/features/notes/infrastructure/storage.js';
import { createNoteSession, taskFlips } from '../frontend/src/features/notes/application/noteSession.js';
import { chipLabel } from '../frontend/src/features/notes/ui/noteWindow.js';
import { sizeRefusal } from '../frontend/src/features/notes/domain/noteRules.js';

// The note window's edit session, driven with a fake API, clock and timers.
function harness({ note = { id: 'note1', title: 'Title', content: 'Original', updated_at: 'v1' }, patch, save, toggle } = {}) {
  const data = new Map(), timers = new Map(), calls = [], events = [];
  let nextTimer = 0, now = 1000;
  const storage = { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) };
  const persistence = createNotesPersistence({ storage });
  const view = { title: note?.title || '', content: note?.content || '' };
  let version = 1;
  const api = {
    patchNote: patch || (async (id, body) => { calls.push(['PUT', id, body]); version += 1; return { id, title: body.title ?? view.title, content: body.content ?? view.content, updated_at: `v${version}`, ...body }; }),
    saveNote: save || (async body => { calls.push(['POST', body]); return { ...body, id: 'created1', updated_at: 'c1' }; }),
    toggleTask: toggle || (async (id, index, text, done) => { calls.push(['TOGGLE', id, index, text, done]); version += 1; return { ok: true, note: { id, title: view.title, content: view.content, updated_at: `v${version}` } }; }),
  };
  const session = createNoteSession({
    api, persistence, note,
    clock: { now: () => (now += 1) },
    scheduler: { setTimeout: f => { timers.set(++nextTimer, f); return nextTimer; }, clearTimeout: t => timers.delete(t) },
    collect: () => ({ ...view }),
    effects: { saved: n => events.push(['saved', n]), created: n => events.push(['created', n]), conflict: c => events.push(['conflict', c]), failed: e => events.push(['failed', e.message]), reloaded: n => events.push(['reloaded', n]), gone: () => events.push(['gone']), unsaved: held => events.push(['unsaved', held]) },
  });
  return {
    session, view, calls, events, data,
    type: (content, title) => { view.content = content; if (title !== undefined) view.title = title; session.markDirty(); },
    drain: async () => { for (const f of [...timers.values()]) f(); timers.clear(); for (let i = 0; i < 5; i++) await new Promise(setImmediate); },
    draft: id => JSON.parse(data.get('odysseus-note-draft-' + (id || 'note1')) || 'null'),
  };
}

test('opening and closing a note writes nothing', async () => {
  const h = harness();
  await h.session.close();
  await h.drain();
  assert.deepEqual(h.calls, []);
  assert.equal(h.draft(), null);
});

test('a blur or close without a real edit never saves, even if collect() differs', async () => {
  const h = harness();
  h.view.content = 'Normalised by the serializer';
  await h.session.flush();
  await h.session.close();
  assert.deepEqual(h.calls, []);
});

test('typing is backed up synchronously, before the autosave timer', () => {
  const h = harness();
  h.type('Just typed');
  assert.equal(h.draft().content, 'Just typed');
  assert.equal(h.draft().base_updated_at, 'v1');
  assert.deepEqual(h.calls, []);
});

test('one debounced PUT carries title, content and the base it was loaded at', async () => {
  const h = harness();
  h.type('a'); h.type('ab'); h.type('abc', 'New title');
  await h.drain();
  assert.deepEqual(h.calls, [['PUT', 'note1', { title: 'New title', content: 'abc', base_updated_at: 'v1' }]]);
  assert.equal(h.session.base, 'v2');
  assert.equal(h.draft(), null, 'a saved draft is cleared');
  h.type('abcd');
  await h.drain();
  assert.equal(h.calls[1][2].base_updated_at, 'v2', 'the next save builds on the server copy it just wrote');
});

test('close flushes a pending edit immediately', async () => {
  const h = harness();
  h.type('quick then close');
  await h.session.close();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0][2].content, 'quick then close');
});

test('failed network save keeps recoverable edits, including empty text', async () => {
  const h = harness({ patch: async () => { throw new Error('offline'); } });
  h.type('', '');
  await h.drain();
  assert.equal(h.draft().content, '');
  assert.equal(h.draft().title, '');
  assert.deepEqual(h.events, [['failed', 'offline']]);
  assert.equal(h.session.dirty, true, 'still dirty, so the next flush retries');
});

test('older save cannot clear newer draft and writes are serialized', async () => {
  const releases = [], writes = [];
  const h = harness({ patch: (id, body) => { writes.push(body.content); return new Promise(r => releases.push(() => r({ id, ...body, updated_at: `r${writes.length}` }))); } });
  h.type('First'); await h.drain();
  h.type('Second'); await h.drain();
  assert.deepEqual(writes, ['First']);
  releases.shift()(); await h.drain();
  assert.equal(h.draft().content, 'Second');
  assert.deepEqual(writes, ['First', 'Second']);
  releases.shift()(); await h.drain();
  assert.equal(h.draft(), null);
});

test('a 409 keeps the local text, reports the server copy and stops autosaving', async () => {
  const server = { id: 'note1', title: 'Title', content: 'Changed on the phone', updated_at: 'v9' };
  let fail = true;
  const sent = [];
  const h = harness({ patch: async (id, body) => {
    sent.push(body);
    if (fail) { const e = new Error('Failed to update note'); e.status = 409; e.body = { detail: { message: 'Note changed elsewhere', note: server } }; throw e; }
    return { id, ...body, updated_at: 'v10' };
  } });
  h.type('Typed on the laptop');
  await h.drain();
  assert.equal(h.events[0][0], 'conflict');
  assert.deepEqual(h.events[0][1].server, server);
  assert.equal(h.draft().content, 'Typed on the laptop', 'the local text survives in draft storage');
  h.type('Still typing during the conflict');
  await h.drain();
  assert.equal(sent.length, 1, 'no further PUT until the conflict is resolved');
  assert.equal(h.draft().content, 'Still typing during the conflict');

  fail = false;
  await h.session.resolveConflict('keep');
  assert.equal(sent.length, 2);
  assert.deepEqual(sent[1], { title: 'Title', content: 'Still typing during the conflict', base_updated_at: 'v9' });
  assert.equal(h.draft(), null);
});

test('reloading after a 409 adopts the server copy without writing', async () => {
  const server = { id: 'note1', title: 'Theirs', content: 'Server text', updated_at: 'v9' };
  const h = harness({ patch: async () => { const e = new Error('x'); e.status = 409; e.body = { detail: { note: server } }; throw e; } });
  h.type('Mine');
  await h.drain();
  await h.session.resolveConflict('reload');
  assert.deepEqual(h.events.at(-1), ['reloaded', server]);
  assert.deepEqual(h.session.saved, { title: 'Theirs', content: 'Server text' });
  assert.equal(h.session.base, 'v9');
  assert.equal(h.draft(), null);
});

test('a new note closed untouched creates nothing; the first real edit POSTs once', async () => {
  const empty = harness({ note: null });
  empty.session.patchMeta({ color: 'blue' });
  await empty.session.close();
  assert.deepEqual(empty.calls, []);

  const h = harness({ note: null });
  await h.session.patchMeta({ color: 'green', label: 'home' });
  h.type('', '   ');
  await h.drain();
  assert.deepEqual(h.calls, [], 'whitespace is not a note');
  h.type('- [ ] \n  - [ ]', '');
  await h.drain();
  assert.deepEqual(h.calls, [], 'nor are empty checkboxes');
  h.type('Buy bread');
  await h.drain();
  assert.deepEqual(h.calls, [['POST', { color: 'green', label: 'home', title: '', content: 'Buy bread' }]]);
  assert.equal(h.session.id, 'created1');
  assert.equal(h.events[0][0], 'created');
  assert.equal(h.data.get('odysseus-note-draft-__new__'), undefined);
  h.type('Buy bread and milk');
  await h.drain();
  assert.deepEqual(h.calls[1], ['PUT', 'created1', { title: '', content: 'Buy bread and milk', base_updated_at: 'c1' }]);
});

test('meta writes move the base only while the content is still ours', async () => {
  const h = harness();
  await h.session.patchMeta({ color: 'red' });
  assert.equal(h.session.base, 'v2');
  const other = harness({ patch: async (id, body) => ({ id, title: 'Title', content: 'Someone else wrote this', updated_at: 'v5', ...body }) });
  await other.session.patchMeta({ color: 'red' });
  assert.equal(other.session.base, 'v1', 'a changed body keeps the old base so the next content save conflicts');
});

test('colour, tag, reminder and archive patches carry no base; content saves do', async () => {
  const h = harness();
  h.type('Edited');
  await h.drain();
  for (const patch of [{ color: 'blue' }, { label: 'home' }, { due_date: '2026-10-28T09:00', repeat: 'none' }, { archived: true }]) await h.session.patchMeta(patch);
  assert.deepEqual(h.calls.map(call => call[2]), [
    { title: 'Title', content: 'Edited', base_updated_at: 'v1' },
    { color: 'blue' }, { label: 'home' }, { due_date: '2026-10-28T09:00', repeat: 'none' }, { archived: true },
  ]);
  for (const [, , body] of h.calls.slice(1)) assert.equal('base_updated_at' in body, false);
});

test('a checkbox flip saves pending text first, then uses the toggle endpoint', async () => {
  const h = harness({ note: { id: 'note1', title: 'T', content: '- [ ] a', updated_at: 'v1' } });
  h.type('- [ ] a\n- [ ] b');
  const result = await h.session.toggleTask(() => 1);
  assert.deepEqual(h.calls.map(c => c[0]), ['PUT', 'TOGGLE']);
  assert.equal(h.calls[1][2], 1);
  assert.equal(h.calls[1][3], 'b', 'the tick names the task the box showed');
  assert.equal(h.calls[1][4], true, 'and the state it is going to, not a flip');
  assert.ok(result.ok);
  assert.equal(h.session.base, 'v3');

  const clean = harness({ note: { id: 'note1', title: 'T', content: '- [ ] a', updated_at: 'v1' } });
  await clean.session.toggleTask(() => 0);
  assert.deepEqual(clean.calls.map(c => c[0]), ['TOGGLE'], 'no whole-content PUT for a checkbox');
});

test('a checkbox in the window asks the server for the state it is going to', async () => {
  const h = harness({ note: { id: 'note1', title: 'T', content: '- [x] a\n- [ ] b', updated_at: 'v1' } });
  await h.session.toggleTask(() => 0);
  await h.session.toggleTask(() => 1);
  assert.deepEqual(h.calls.map(c => c.slice(2)), [[0, 'a', false], [1, 'b', true]]);
});

test('an earlier unsaved draft on this device is offered back, an identical one is dropped', () => {
  const h = harness();
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, title: 'Title', content: 'Unsaved from yesterday' }));
  assert.deepEqual(h.session.storedDraft(), { title: 'Title', content: 'Unsaved from yesterday' });
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, title: 'Title', content: 'Original' }));
  assert.equal(h.session.storedDraft(), null);
  assert.equal(h.draft(), null);
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, note_type: 'todo', title: 'Title', items: [{ text: 'old form row', done: false }] }));
  assert.deepEqual(h.session.storedDraft(), { title: 'Title', content: '- [ ] old form row' }, 'drafts left by the old form still restore');
});

test('the first write carries the one-off fields, later ones do not', async () => {
  const data = new Map(), calls = [];
  let timer = null;
  const session = (await import('../frontend/src/features/notes/application/noteSession.js')).createNoteSession({
    api: { patchNote: async (id, body) => { calls.push(body); return { id, ...body, updated_at: `v${calls.length + 1}` }; } },
    persistence: createNotesPersistence({ storage: { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) } }),
    clock: { now: () => 1 }, scheduler: { setTimeout: f => (timer = f), clearTimeout: () => {} },
    note: { id: 'n', title: 't', content: 'a', updated_at: 'v1' },
    collect: () => ({ title: 't', content: `edit ${calls.length}` }),
    firstWrite: () => ({ sort_order: -7 }),
  });
  session.markDirty(); await session.flush();
  session.markDirty(); await session.flush();
  assert.equal(calls[0].sort_order, -7);
  assert.equal('sort_order' in calls[1], false);
});

test('a checkbox flip whose pending text failed to save is left to the content path', async () => {
  const h = harness({ patch: async () => { throw new Error('offline'); } });
  h.type('- [ ] a typed');
  let asked = false;
  const result = await h.session.toggleTask(() => { asked = true; return 0; });
  assert.equal(result, null);
  assert.equal(asked, false, 'the index is never resolved against unsaved text');
  assert.deepEqual(h.calls, []);
});

test('a 404 marks the note gone: one attempt, no retries, no draft kept', async () => {
  const sent = [];
  const h = harness({ patch: async (id, body) => { sent.push(body); const e = new Error('Failed to update note'); e.status = 404; throw e; } });
  h.type('Typed after the owner was deleted');
  await h.drain();
  assert.deepEqual(h.events, [['gone']]);
  assert.equal(h.session.gone, true);
  assert.equal(h.session.dirty, false);
  assert.equal(h.draft(), null, 'no note will ever offer this draft back');
  h.type('Still typing');
  await h.drain();
  await h.session.close();
  assert.equal(sent.length, 1, 'nothing is retried once the note is gone');
  assert.equal(h.draft(), null);
  assert.deepEqual(h.events, [['gone']], 'reported once, never as a conflict or a failure');
});

test('a 404 on a checkbox flip marks the note gone too', async () => {
  const h = harness({ toggle: async () => { const e = new Error('Failed to toggle item'); e.status = 404; throw e; } });
  await assert.rejects(h.session.toggleTask(() => 0));
  assert.equal(h.session.gone, true);
  assert.deepEqual(h.events, [['gone']]);
  assert.equal(await h.session.toggleTask(() => 0), null, 'no further toggles are sent');
});

test('a 404 while creating a note is an ordinary failure, not gone', async () => {
  const h = harness({ note: null, save: async () => { const e = new Error('Failed to save note'); e.status = 404; throw e; } });
  h.type('New note text', 'New');
  await h.drain();
  assert.equal(h.session.gone, false);
  assert.equal(h.events[0][0], 'failed');
  assert.equal(h.draft('__new__').content, 'New note text');
});

test('a minimised note window is labelled with its title, 28 characters at most', () => {
  assert.equal(chipLabel('Weekend'), 'Note: Weekend');
  assert.equal(chipLabel('   '), 'Note: Untitled');
  assert.equal(chipLabel(null), 'Note: Untitled');
  const long = chipLabel('A very long title that goes past the limit');
  assert.equal(long, 'Note: A very long title tha…');
  assert.equal(long.length, 28);
});

test('a checkbox flip refused because the task changed takes the server copy', async () => {
  const server = { id: 'note1', title: 'T', content: '- [ ] new\n- [ ] a', updated_at: 'v9' };
  const toggle = async () => { throw Object.assign(new Error('changed'), { status: 409, body: { detail: { note: server } } }); };
  const h = harness({ note: { id: 'note1', title: 'T', content: '- [ ] a', updated_at: 'v1' }, toggle });
  await assert.rejects(h.session.toggleTask(() => 0), err => err.status === 409);
  assert.equal(h.session.saved.content, '- [ ] new\n- [ ] a');
  assert.equal(h.session.base, 'v9');
});

test('a damaged or empty stored draft is ignored, and a draft without a title keeps the saved one', () => {
  const h = harness();
  for (const stored of ['{not json', 'null', '"text"', JSON.stringify({ _ts: 1, title: 'Title' })]) {
    h.data.set('odysseus-note-draft-note1', stored);
    assert.equal(h.session.storedDraft(), null, stored);
  }
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, content: 'Typed offline' }));
  assert.deepEqual(h.session.storedDraft(), { title: 'Title', content: 'Typed offline' });
});

test('an edit typed back to what was saved writes nothing and drops its draft', async () => {
  const h = harness();
  h.type('Original, then more');
  assert.ok(h.draft(), 'the edit is backed up');
  h.type('Original');
  await h.drain();
  assert.deepEqual(h.calls, []);
  assert.equal(h.draft(), null);
  assert.equal(h.session.dirty, false);
});

test('a full or blocked localStorage is reported once per backup and the save still goes out', async () => {
  const calls = [];
  let unavailable = 0;
  const persistence = createNotesPersistence({ storage: { getItem: () => null, setItem: () => { throw new Error('QuotaExceededError'); }, removeItem() {} } });
  const view = { title: 'Title', content: 'Original' };
  const timers = [];
  const session = createNoteSession({
    api: { patchNote: async (id, body) => { calls.push(body); return { id, ...body, updated_at: 'v2' }; } },
    persistence, note: { id: 'note1', ...view, updated_at: 'v1' },
    clock: { now: () => 1 },
    scheduler: { setTimeout: f => timers.push(f), clearTimeout() {} },
    collect: () => ({ ...view }),
    effects: { backupUnavailable: () => { unavailable += 1; } },
  });
  view.content = 'Typed with a full disk';
  session.markDirty();
  await session.flush();
  assert.ok(unavailable >= 1);
  assert.deepEqual(calls.map(c => c.content), ['Typed with a full disk']);
});

test('a 409 without the server copy still stops autosave; Keep mine then writes over it', async () => {
  let fail = true;
  const h = harness({ patch: async (id, body) => {
    h.calls.push(['PUT', id, body]);
    if (fail) { fail = false; throw Object.assign(new Error('conflict'), { status: 409, body: {} }); }
    return { id, ...body, updated_at: 'v3' };
  } });
  h.type('Mine');
  await h.drain();
  assert.deepEqual(h.session.conflict, { local: { title: 'Title', content: 'Mine' }, server: null });
  await h.session.resolveConflict('keep');
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1][2].base_updated_at, 'v1', 'no newer copy known: the old base is sent again');
  assert.equal(await h.session.resolveConflict('keep'), undefined, 'no conflict left: nothing to resolve');
});

test('an agent edit arriving under unsaved typing is not taken; the save then meets the conflict, text kept', async () => {
  const h = harness({ patch: async (id, body) => {
    h.calls.push(['PUT', id, body]);
    // The server has the agent's version: the base the window sends is older.
    throw Object.assign(new Error('conflict'), { status: 409, body: { detail: { note: { id, title: 'Title', content: 'Agent text', updated_at: 'v2' } } } });
  } });
  h.type('Typed before the agent finished');
  h.session.replace({ id: 'note1', title: 'Title', content: 'Agent text', updated_at: 'v2' });
  assert.equal(h.session.base, 'v1', 'the typing is not overwritten, nor its base moved');
  await h.drain();
  assert.equal(h.calls[0][2].base_updated_at, 'v1');
  assert.equal(h.session.conflict.local.content, 'Typed before the agent finished');
  assert.equal(h.session.conflict.server.content, 'Agent text');
  assert.equal(h.draft().content, 'Typed before the agent finished', 'the typing stays in the draft');
});

test('an edit the editor could not write is held on this device and said so, never dropped', async () => {
  const h = harness();
  // The window kept the block's stored text; `unsaved` is the note with the typed edit.
  h.view.unsaved = 'Original TYPED';
  h.type('Original');
  await h.drain();
  assert.deepEqual(h.calls, [], 'nothing else changed, nothing to send');
  assert.deepEqual(h.events, [['unsaved', true]]);
  assert.equal(h.draft().content, 'Original TYPED');
  await h.session.close();
  await h.drain();
  assert.equal(h.session.storedDraft()?.content, 'Original TYPED', 'offered back on the next open');

  // The rest of the note still saves; the draft keeps the held edit.
  h.view.unsaved = 'Original TYPED\n\nmore';
  h.type('Original\n\nmore');
  await h.drain();
  assert.deepEqual(h.calls.map(call => call[2].content), ['Original\n\nmore']);
  assert.equal(h.draft().content, 'Original TYPED\n\nmore');

  // Fixed (in the source, say): saved, the draft goes, the window is told.
  h.view.unsaved = null;
  h.type('Original TYPED\n\nmore');
  await h.drain();
  assert.equal(h.calls.at(-1)[2].content, 'Original TYPED\n\nmore');
  assert.deepEqual(h.events.filter(([name]) => name === 'unsaved'), [['unsaved', true], ['unsaved', false]]);
  assert.equal(h.draft(), null);
});

// A tick on the card while the window held typed text was only
// rebased, never drawn, so the next keystroke saved the old box over it.
test('a card tick behind a window whose typing is saved is taken, and the next save keeps it', async () => {
  const h = harness({ note: { id: 'note1', title: 'Title', content: '- [ ] one\n- [ ] two\n\ntail text', updated_at: 'v1' } });
  h.type('- [ ] one\n- [ ] two\n\ntail text A');
  await h.drain();
  assert.equal(h.session.base, 'v2');
  const ticked = { id: 'note1', title: 'Title', content: '- [x] one\n- [ ] two\n\ntail text A', updated_at: 'v3' };
  assert.equal(h.session.follow(ticked, { title: 'Title', content: '- [ ] one\n- [ ] two\n\ntail text A' }), 'take');
  assert.equal(h.session.base, 'v3');
  assert.equal(h.session.saved.content, ticked.content, 'the window redraws from the card\'s version');
  // The window drew the tick; the next keystroke goes on top of it.
  h.type('- [x] one\n- [ ] two\n\ntail text AB');
  await h.drain();
  assert.equal(h.calls.at(-1)[2].content, '- [x] one\n- [ ] two\n\ntail text AB');
  assert.equal(h.calls.at(-1)[2].base_updated_at, 'v3');
});

test('a card tick behind unsaved typing keeps the old base, so that save gets the conflict choice', () => {
  const h = harness({ note: { id: 'note1', title: 'Title', content: '- [ ] one\n\ntail', updated_at: 'v1' } });
  h.type('- [ ] one\n\ntail typed');
  const ticked = { id: 'note1', title: 'Title', content: '- [x] one\n\ntail', updated_at: 'v2' };
  assert.equal(h.session.follow(ticked, { title: 'Title', content: '- [ ] one\n\ntail typed' }), 'keep');
  assert.equal(h.session.base, 'v1', 'never moved on silently: the save meets the 409');
  assert.equal(h.session.saved.content, '- [ ] one\n\ntail');
});

test('a card write that left the text as saved only moves the base', () => {
  const h = harness();
  h.type('Original typed');
  assert.equal(h.session.follow({ id: 'note1', title: 'Title', content: 'Original', updated_at: 'v2' }, { title: 'Title', content: 'Original typed' }), 'base');
  assert.equal(h.session.base, 'v2');
  assert.equal(h.session.dirty, true);
});

test('taskFlips names the boxes that changed, and only when nothing else did', () => {
  assert.deepEqual(taskFlips('- [ ] a\n- [x] b\n\ntext', '- [x] a\n- [ ] b\n\ntext'), [0, 1]);
  assert.deepEqual(taskFlips('- [ ] a', '- [ ] a'), []);
  assert.equal(taskFlips('- [ ] a', '- [x] a\n- [ ] b'), null, 'an item was added');
  assert.equal(taskFlips('- [ ] a\n\ntext', '- [x] a\n\nother'), null, 'the text changed too');
  assert.equal(taskFlips('- [ ] a', '- [ ] renamed'), null);
});

test('typing while a new note is being created leaves no new-note draft to restore as a second note', async () => {
  let answer;
  const h = harness({ note: null, save: body => { h.calls.push(['POST', body]); return new Promise(resolve => { answer = resolve; }); } });
  h.type('Buy bread');
  await h.drain();
  assert.equal(h.calls.length, 1, 'the create request is out');
  h.type('Buy bread and milk');
  answer({ title: '', content: 'Buy bread', id: 'created1', updated_at: 'c1' });
  for (let i = 0; i < 5; i++) await new Promise(setImmediate);
  assert.equal(h.data.get('odysseus-note-draft-__new__'), undefined, 'no new-note window will offer it back');
  assert.equal(h.draft('created1').content, 'Buy bread and milk', 'the typing is kept under the note it belongs to');
  assert.equal(h.draft('created1').base_updated_at, 'c1', 'and builds on the version just created');
  await h.drain();
  assert.deepEqual(h.calls[1], ['PUT', 'created1', { title: '', content: 'Buy bread and milk', base_updated_at: 'c1' }]);
  assert.equal(h.draft('created1'), null);
  const fresh = harness({ note: null });
  for (const [k, v] of h.data) fresh.data.set(k, v);
  assert.equal(fresh.session.storedDraft(), null, 'a new note opened later has nothing to restore');
});

test('a held edit on a new note moves to the created note, not to the next new note', async () => {
  const h = harness({ note: null });
  h.view.unsaved = 'Kept text';
  h.type('Saved text');
  await h.drain();
  assert.equal(h.session.id, 'created1');
  assert.equal(h.data.get('odysseus-note-draft-__new__'), undefined);
  assert.equal(h.draft('created1').content, 'Kept text');
});

test('a restored draft is saved against the version it was typed on, so a newer server copy meets the conflict choice', async () => {
  const h = harness({
    note: { id: 'note1', title: 'Title', content: 'Someone else rewrote this', updated_at: 'v3' },
    patch: async (id, body) => {
      h.calls.push(['PUT', id, body]);
      if (body.base_updated_at !== 'v3') throw Object.assign(new Error('conflict'), { status: 409, body: { detail: { note: { id, title: 'Title', content: 'Someone else rewrote this', updated_at: 'v3' } } } });
      return { id, ...body, updated_at: 'v4' };
    },
  });
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, title: 'Title', content: 'Typed offline on v1', base_updated_at: 'v1' }));
  const draft = h.session.storedDraft();
  assert.deepEqual(draft, { title: 'Title', content: 'Typed offline on v1' });
  h.session.restoreDraft();
  h.type(draft.content, draft.title);
  await h.drain();
  assert.equal(h.calls[0][2].base_updated_at, 'v1', 'sent with the base the draft was typed on');
  assert.equal(h.events.at(-1)[0], 'conflict');
  assert.equal(h.draft().content, 'Typed offline on v1', 'the restored text is still kept');
  await h.session.resolveConflict('mine');
  assert.equal(h.calls[1][2].base_updated_at, 'v3', 'Keep mine then writes over the newer copy, knowingly');
});

test('restoring a draft with no recorded base keeps the loaded base', async () => {
  const h = harness();
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, title: 'Title', items: [{ text: 'old form row', done: false }] }));
  const draft = h.session.storedDraft();
  h.session.restoreDraft();
  h.type(draft.content, draft.title);
  await h.drain();
  assert.equal(h.calls[0][2].base_updated_at, 'v1');
});

test('a save refused as too long hands the window its reason and keeps the text on this device', async () => {
  const refusal = Object.assign(new Error('Failed to update note'), { status: 413, body: { detail: { message: 'The note\'s title is too long to save: 1,001 characters, the limit is 1,000.' } } });
  const h = harness({ patch: async () => { throw refusal; } });
  h.type('kept', 'T'.repeat(1001));
  await h.drain();
  assert.deepEqual(h.events, [['failed', 'Failed to update note']]);
  assert.equal(sizeRefusal(refusal), 'The note\'s title is too long to save: 1,001 characters, the limit is 1,000.');
  assert.equal(sizeRefusal({ status: 413, body: null }), 'This note is too long to save.');
  assert.equal(sizeRefusal({ status: 500 }), null);
  assert.equal(h.draft().title, 'T'.repeat(1001));
  assert.equal(h.draft().content, 'kept');
  assert.equal(h.session.dirty, true, 'still dirty, so a shorter edit saves');
});
