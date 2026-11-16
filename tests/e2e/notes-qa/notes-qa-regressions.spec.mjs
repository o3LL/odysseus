// Notes QA regression spec, written against test/notes-t-history-ux @ bd65bc29.
// Every test asserts the EXPECTED behaviour, so it is red while its bug is present.
//
// Run: venv/bin/python tests/e2e/notes-qa/run_notes_qa.py (see README.md here).
// It needs AUTH_ENABLED=true, an admin (NOTES_QA_OWNER) and one non-admin (NOTES_QA_COLLAB).
import { test, expect, request, devices } from '@playwright/test';

const ORIGIN = process.env.NOTES_QA_ORIGIN;
const OWNER = { username: process.env.NOTES_QA_OWNER, password: process.env.NOTES_QA_OWNER_PW };
const COLLAB = { username: process.env.NOTES_QA_COLLAB, password: process.env.NOTES_QA_COLLAB_PW };

test.use({ viewport: { width: 1440, height: 900 } });

async function signIn(account) {
  const api = await request.newContext({ baseURL: ORIGIN });
  for (let i = 0; i < 40; i++) {
    const r = await api.post('/api/auth/login', { data: account });
    if (r.status() !== 429) { expect(r.ok(), `login ${account.username}`).toBeTruthy(); break; }
    await new Promise(done => setTimeout(done, 2000));
  }
  return api;
}
const created = [];
async function makeNote(api, title, content, extra = {}) {
  const note = await (await api.post('/api/notes', { data: { title, content, ...extra } })).json();
  created.push(note.id);
  return note.id;
}
const read = async (api, id) => (await api.get(`/api/notes/${id}`)).json();
async function openNote(browser, api, id) {
  const context = await browser.newContext({ storageState: await api.storageState() });
  await context.addInitScript(() => localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'));
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(ORIGIN);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).click();
  return { context, page, errors, body: page.locator('#note-window-modal .note-window-body') };
}

let owner, collab;
test.beforeAll(async () => { owner = await signIn(OWNER); collab = await signIn(COLLAB); });
test.afterAll(async () => { for (const id of created) await owner.delete(`/api/notes/${id}`); });

test('BLOCKER an edit in a note with a bullet list followed by a numbered list is saved', async ({ browser }) => {
  // This content is what the editor itself writes for: Bullet list, two items, Enter Enter, Numbered list, two items.
  const id = await makeNote(owner, 'QA lists', '- apples\n- pears\n1. wash\n2. peel');
  const { context, page, body } = await openNote(browser, owner, id);
  await body.locator('li', { hasText: 'apples' }).first().click();
  await page.keyboard.press('Home');
  await page.keyboard.type('green ');
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toContain('green apples');
  await context.close();
});

test('BLOCKER the rendered note keeps the numbered list a list (no orphan <li>)', async ({ browser }) => {
  const id = await makeNote(owner, 'QA lists render', '- apples\n- pears\n1. wash\n2. peel');
  const { context, body } = await openNote(browser, owner, id);
  await expect(body.locator('ol > li')).toHaveCount(2);
  expect(await body.evaluate(root => [...root.querySelectorAll('li')].filter(li => !li.parentElement.matches('ul, ol')).length)).toBe(0);
  await context.close();
});

for (const [name, content] of [['inline fence', 'see ```js\nfoo()``` here'], ['fence inside a quote', '> ```js\n> x\n> ```']]) {
  test(`BLOCKER a note with a ${name} opens in the window`, async ({ browser }) => {
    const id = await makeNote(owner, `QA ${name}`, content);
    const { context, page, errors, body } = await openNote(browser, owner, id);
    await page.waitForTimeout(1200);
    expect(errors, 'page errors').toEqual([]);
    await expect(body).not.toBeEmpty();
    await expect(page.locator('#note-window-modal, .note-window')).toHaveCount(1);
    await context.close();
  });
}

// A fault injected into one module, so the window's guard is tested on its own,
// whatever the renderer gets right: the served file is patched in flight.
async function openWithFault(browser, api, id, file, find, inject) {
  const context = await browser.newContext({ storageState: await api.storageState(), serviceWorkers: 'block' });
  await context.addInitScript(() => localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'));
  await context.route(url => url.pathname.endsWith(file), async route => {
    const response = await route.fetch();
    const text = await response.text();
    if (!text.includes(find)) throw new Error(`fault anchor missing in ${file}`);
    await route.fulfill({ response, body: text.replace(find, find + inject) });
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(ORIGIN);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  const card = page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`);
  await card.click();
  return { context, page, errors, card };
}

test('BLOCKER a note the editor fails to render opens once, in Markdown source, and still saves', async ({ browser }) => {
  const content = 'QA_RENDER_FAULT first line\n\nsecond line';
  const id = await makeNote(owner, 'QA render fault', content);
  const { context, page, errors, card } = await openWithFault(browser, owner, id, '/static/js/markdown.js',
    'export function mdToHtml(src, opts) {',
    "\n  if (opts && opts.sourceMap && String(src).includes('QA_RENDER_FAULT')) throw new TypeError('injected render fault');");
  const modal = page.locator('#note-window-modal');
  await expect(modal).toHaveCount(1);
  await expect(modal.locator('.note-window-banner')).toContainText('open in Markdown source');
  const source = modal.locator('.note-window-source');
  await expect(source).toBeVisible();
  await expect(source).toHaveValue(content);
  await card.click();
  await page.waitForTimeout(500);
  await expect(page.locator('#note-window-modal, .note-window')).toHaveCount(1);
  // Back to the formatted view fails the same way: the window stays in source, text intact.
  await modal.locator('.note-window-toolbar [data-md="source"]').click();
  await expect(source).toBeVisible();
  await expect(source).toHaveValue(content);
  await source.click();
  await page.keyboard.press('Control+End');
  await page.keyboard.type(' edited');
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toBe(content + ' edited');
  expect(errors, 'page errors').toEqual([]);
  await context.close();
});

test('BLOCKER a note window that fails to build leaves nothing behind and says so', async ({ browser }) => {
  const id = await makeNote(owner, 'QA mount fault', 'body', { label: 'qafault' });
  const { context, page, card } = await openWithFault(browser, owner, id, '/static/js/features/notes/ui/noteTags.js',
    "function mount(container, { label = '', knownTags = () => [], onChange, onEscape = () => {} }) {",
    "\n    if (String(label).includes('qafault')) throw new Error('injected mount fault');");
  await expect(page.getByText('This note could not be opened.').first()).toBeVisible();
  await expect(page.locator('#note-window-modal, .note-window')).toHaveCount(0);
  await card.click();
  await page.waitForTimeout(500);
  await expect(page.locator('#note-window-modal, .note-window')).toHaveCount(0);
  await context.close();
});

test('MAJOR editing a note written with "*" bullets does not rewrite it as italics', async ({ browser }) => {
  const id = await makeNote(owner, 'QA star bullets', '* alpha\n* beta');
  const { context, page, body } = await openNote(browser, owner, id);
  await expect(body.locator('ul > li')).toHaveCount(2);
  await body.getByText('alpha').first().click();
  await page.keyboard.press('End');
  await page.keyboard.type(' x');
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toMatch(/^[*-] alpha x\n[*-] beta$/);
  await context.close();
});

test('MAJOR a card tick made while the window holds typed text survives the next keystroke', async ({ browser }) => {
  const id = await makeNote(owner, 'QA tick revert', '- [ ] one\n- [ ] two\n\ntail text');
  const { context, page, body } = await openNote(browser, owner, id);
  const tail = body.locator('p', { hasText: 'tail text' });
  await tail.click(); await page.keyboard.press('End'); await page.keyboard.type(' A');
  await expect.poll(async () => (await read(owner, id)).content).toContain('tail text A');
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .md-task`, { hasText: 'one' }).locator('.md-task-box').evaluate(box => box.click());
  await expect.poll(async () => (await read(owner, id)).content).toContain('- [x] one');
  await tail.click(); await page.keyboard.press('End'); await page.keyboard.type('B');
  await page.waitForTimeout(1800);
  expect((await read(owner, id)).content).toContain('- [x] one');
  await context.close();
});

test('MAJOR a collaborator\'s change reaches an idle open note that has the caret in it', async ({ browser }) => {
  const id = await makeNote(owner, 'QA idle window refresh', 'first line\n\nsecond line');
  await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'edit' } });
  const { context, page, body } = await openNote(browser, owner, id);
  // The caret in the body, nothing typed: the usual state of an open note.
  await body.locator('p', { hasText: 'second line' }).click();
  const current = await read(collab, id);
  const put = await collab.put(`/api/notes/${id}`, { data: { content: 'first line\n\nsecond line from bob', base_updated_at: current.updated_at } });
  expect(put.ok()).toBeTruthy();
  // One 20 s refresh tick, plus slack.
  await expect(body).toContainText('second line from bob', { timeout: 30_000 });
  await expect(page.locator(`#notes-pane .note-card[data-note-id="${id}"]`)).toContainText('second line from bob');
  // The window is on bob's version: typing now saves on top of it, no conflict.
  await body.locator('p', { hasText: 'second line from bob' }).click();
  await page.keyboard.press('End'); await page.keyboard.type(' and alice');
  await expect.poll(async () => (await read(owner, id)).content).toContain('second line from bob and alice');
  await expect(page.locator('#note-window-modal')).not.toContainText('changed elsewhere');
  await context.close();
});

test('MAJOR (security) an edit collaborator cannot null the owner-only fields through /api/codex/todos', async () => {
  const id = await makeNote(owner, 'QA collaborator null fields', 'x');
  await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'edit' } });
  await collab.post('/api/codex/todos', { data: { action: 'update', id, content: 'x', archived: null, pinned: null } });
  const listed = (await (await owner.get('/api/notes')).json()).notes.some(note => note.id === id);
  expect(listed, "the note is still in the owner's list").toBeTruthy();
  const note = await read(owner, id);
  expect([note.archived, note.pinned]).toEqual([false, false]);
});

test('MAJOR the owner can get back text a collaborator overwrote a minute later', async () => {
  const id = await makeNote(owner, 'QA history authors', 'v0 original');
  await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'edit' } });
  let current = await read(owner, id);
  await owner.put(`/api/notes/${id}`, { data: { content: 'v1 OWNER wrote this paragraph', base_updated_at: current.updated_at } });
  current = await read(collab, id);
  await collab.put(`/api/notes/${id}`, { data: { content: 'v2 collaborator replaced everything', base_updated_at: current.updated_at } });
  const listing = await (await owner.get(`/api/notes/${id}/versions`)).json();
  const rows = Array.isArray(listing) ? listing : listing.versions;
  const texts = await Promise.all(rows.map(async row => (await (await owner.get(`/api/notes/${id}/versions/${row.id}`)).json()).content));
  expect(texts.some(text => (text || '').includes('OWNER wrote'))).toBeTruthy();
});

test('MAJOR adding a to-do to a note with an unclosed code fence works', async () => {
  const id = await makeNote(owner, 'QA unclosed fence', 'notes\n```\ncode');
  const response = await owner.post(`/api/notes/${id}/items`, { data: { text: 'buy milk' } });
  expect(response.status()).toBeLessThan(500);
  expect((await read(owner, id)).items?.length || 0).toBe(1);
});

test('MINOR a reopened note still offers Undo for its last agent edit', async ({ browser }) => {
  // Needs the stub model as the owner's endpoint (run_notes_qa.py).
  const before = 'Keep  these bytes\n\n- [ ] EDITTEST reopen';
  const id = await makeNote(owner, 'QA undo after reopen', before);
  const { context, page, body } = await openNote(browser, owner, id);
  const row = body.locator('li.md-task', { hasText: 'EDITTEST' });
  await row.hover();
  await row.locator('.note-task-agent').click();
  await page.locator('.note-ask-menu [data-act="solve"]').click();
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 30_000 }).toMatch(/^Edited by the agent: reopen/);
  await expect(body).toContainText('Edited by the agent', { timeout: 15_000 });
  await page.locator('#note-window-modal .modal-close').click();
  await expect(page.locator('#note-window-modal')).toHaveCount(0);
  // A fresh page: the toast is gone, the server still says the edit can be undone.
  await page.reload();
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  expect((await read(owner, id)).agent_edit?.undoable).toBe(true);
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).click();
  const meta = page.locator('#note-window-modal .note-window-meta');
  await expect(meta).toContainText(/Agent edited .* · Undo/);
  await meta.locator('.note-window-meta-undo:not(.note-window-meta-history)').click();
  await expect.poll(async () => (await read(owner, id)).content).toBe(before);
  await expect(body).toContainText('Keep');
  await expect(meta).not.toContainText('Agent edited');
  await context.close();
});

test('MINOR typing at the end of a list leaves the lines it did not touch as they were', async ({ browser }) => {
  const content = '1. Do X\n2. Do Y\n   - sub';
  const id = await makeNote(owner, 'QA untouched lines', content);
  const { context, page, body } = await openNote(browser, owner, id);
  await body.getByText('sub').first().click();
  await page.keyboard.press('End');
  await page.keyboard.type('ZQ');
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toContain('ZQ');
  expect((await read(owner, id)).content).toBe(`${content}ZQ`);
  await context.close();
});

test('MINOR a paragraph typed under a checklist is saved as a block of its own', async ({ browser }) => {
  // Written in the editor, so any markdown renderer reads two blocks: a blank
  // line between the list and the text, not a single newline.
  const id = await makeNote(owner, 'QA typed blocks', 'Start');
  const { context, page, body } = await openNote(browser, owner, id);
  await body.getByText('Start').first().click();
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  await page.locator('#note-window-modal [data-md="check"]').click();
  await page.keyboard.type('task one');
  await page.keyboard.press('Enter');
  await page.keyboard.type('task two');
  await page.keyboard.press('Enter');
  await page.keyboard.press('Enter');
  await page.keyboard.type('after the list');
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toContain('after the list');
  expect(JSON.stringify((await read(owner, id)).content)).toBe(JSON.stringify('Start\n\n- [ ] task one\n- [ ] task two\n\nafter the list'));
  await context.close();
});

test('MINOR a one-line code block on a grid card is not one character per line', async ({ browser }) => {
  const id = await makeNote(owner, 'QA snippet card', 'Run this:\n\n```bash\ndocker compose up -d --build\n```');
  const context = await browser.newContext({ storageState: await owner.storageState() });
  await context.addInitScript(() => { localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'); localStorage.setItem('odysseus-notes-view', 'grid'); });
  const page = await context.newPage();
  await page.goto(ORIGIN);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  const card = page.locator(`#notes-pane .note-card[data-note-id="${id}"]`);
  const pre = card.locator('pre');
  await pre.scrollIntoViewIfNeeded();
  await card.hover();
  expect((await pre.boundingBox()).height).toBeLessThan(120);
  // Chat marks a one-line <pre> as .pre-compact and reserves 200px on the
  // right for its Run/Edit/Copy row. Cards show none of those buttons, so the
  // text must get the whole width of the block.
  const room = await pre.evaluate(el => el.clientWidth - parseFloat(getComputedStyle(el).paddingLeft) - parseFloat(getComputedStyle(el).paddingRight));
  expect(room).toBeGreaterThan((await pre.boundingBox()).width * 0.8);
  await expect(card.getByTitle('Run code')).toBeHidden();
  await expect(card.locator('pre :is(.run-code, .edit-code, .copy-code)')).toHaveCount(3);
  for (const button of await card.locator('pre button').all()) await expect(button).toBeHidden();
  await context.close();
});

test('MINOR a 3 MB body or a 50,000-character title is refused with 413', async () => {
  // Both used to be stored (200).
  const body = await owner.post('/api/notes', { data: { title: 'QA huge body', content: 'x'.repeat(3_000_000) } });
  expect(body.status()).toBe(413);
  expect((await body.json()).detail).toMatchObject({ field: 'content', limit: 1_000_000 });
  const title = await owner.post('/api/notes', { data: { title: 't'.repeat(50_000), content: 'ok' } });
  expect(title.status()).toBe(413);
  expect((await title.json()).detail).toMatchObject({ field: 'title', limit: 1_000 });
});

test('MINOR a note too long to save says why, keeps the text, and saves once it is shortened', async ({ browser }) => {
  // On the client: the 413 must not read as "offline".
  const id = await makeNote(owner, 'QA long title', 'body');
  const { context, page } = await openNote(browser, owner, id);
  const title = page.locator('#note-window-modal .note-window-title');
  const banner = page.locator('#note-window-modal .note-window-banner');
  await title.fill('T'.repeat(1_001));
  await expect(banner).toBeVisible({ timeout: 10_000 });
  await expect(banner).toContainText('too long to save');
  await expect(banner).toContainText('1,000');
  await expect(page.locator('#toast')).not.toContainText('Note not synced');
  expect((await read(owner, id)).title).toBe('QA long title');
  expect(await title.inputValue()).toBe('T'.repeat(1_001));
  await title.fill('QA long title, shortened');
  await expect.poll(async () => (await read(owner, id)).title, { timeout: 10_000 }).toBe('QA long title, shortened');
  await expect(banner).toBeHidden();
  await context.close();
});

test('MINOR an edit typed offline is saved when the connection comes back, and says so', async ({ browser }) => {
  // QA "Offline, then back online", W5. The window stays open and nothing is typed after reconnecting.
  const id = await makeNote(owner, 'QA offline retry', 'start');
  const { context, page, body } = await openNote(browser, owner, id);
  const toast = page.locator('#toast');
  await context.setOffline(true);
  await body.locator('p', { hasText: 'start' }).click();
  await page.keyboard.press('End');
  await page.keyboard.type(' offline-edit');
  await expect(toast).toContainText('Note not synced', { timeout: 10_000 });
  await context.setOffline(false);
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 10_000 }).toBe('start offline-edit');
  await expect(toast).toContainText('Note synced');
  await expect(toast).not.toHaveClass(/\berror\b/);
  await expect(page.locator(`#notes-pane .note-card[data-note-id="${id}"]`)).toContainText('offline-edit');
  await context.close();
});

// QA "Phone profile" (13-phone-window-252px.png), W2/W3. iPhone 15, the device the QA pass used.
async function phonePage(browser, api) {
  const context = await browser.newContext({ ...devices['iPhone 15'], storageState: await api.storageState() });
  await context.addInitScript(() => localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'));
  const page = await context.newPage();
  await page.goto(ORIGIN);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  return { context, page };
}
// The window slides up as a sheet: measure once it has stopped moving.
async function settledWindow(page) {
  let last = null;
  await expect.poll(async () => {
    const top = await page.locator('#note-window-modal .note-window-content').evaluate(e => Math.round(e.getBoundingClientRect().top));
    const still = top === last; last = top; return still;
  }, { intervals: [150] }).toBe(true);
}

test('MINOR on a phone the note window keeps the screen for the text while typing', async ({ browser }) => {
  const id = await makeNote(owner, 'QA phone room', Array.from({ length: 40 }, (_, i) => `Line ${i}`).join('\n\n'));
  const { context, page } = await phonePage(browser, owner);
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).tap();
  const win = page.locator('#note-window-modal');
  const toolbar = win.locator('.note-window-toolbar');
  const footer = win.locator('.note-window-footer');
  const room = () => win.locator('.note-window-scroll').evaluate(e => e.getBoundingClientRect().height);
  await settledWindow(page);
  // The toolbar is one row of buttons, not three.
  const bold = await toolbar.locator('[data-md="bold"]').boundingBox();
  expect((await toolbar.boundingBox()).height).toBeLessThan(bold.height * 2);
  await expect(footer).toBeVisible();
  // Typing: the footer (colours, tags, actions) gets out of the way.
  await win.locator('.note-window-body p', { hasText: 'Line 0' }).tap();
  await page.keyboard.type('x');
  await expect(footer).toBeHidden();
  const viewport = page.viewportSize().height;
  expect(await room()).toBeGreaterThanOrEqual(viewport * 0.6);
  // Done typing: the footer comes back.
  await win.locator('.note-window-title').tap();
  await expect(footer).toBeVisible();
  // Every formatting action can still be reached: swiped into view, a tap lands on it.
  const missed = await toolbar.evaluate(bar => Array.from(bar.querySelectorAll('[data-md]')).filter(b => {
    b.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    const r = b.getBoundingClientRect();
    const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !(at === b || b.contains(at));
  }).map(b => b.dataset.md));
  expect(missed).toEqual([]);
  // The tap leaves the caret at the end of the short line.
  await expect.poll(async () => (await read(owner, id)).content).toMatch(/^Line 0x\n/);
  await context.close();
});

test('MINOR on a phone a note archived from its window can be found again', async ({ browser }) => {
  const id = await makeNote(owner, 'QA phone archive', 'Archive me from a phone');
  const { context, page } = await phonePage(browser, owner);
  const card = page.locator(`#notes-pane .note-card[data-note-id="${id}"]`);
  await card.locator('.note-card-title').tap();
  await settledWindow(page);
  await page.locator('#note-window-modal [data-act="archive"]').tap();
  await expect.poll(async () => (await read(owner, id)).archived).toBe(true);
  await expect(page.locator('#note-window-modal')).toHaveCount(0);
  await expect(card).toHaveCount(0);
  const toggle = page.locator('#notes-archive-toggle');
  await expect(toggle).toBeVisible();
  await toggle.tap();
  await expect(card).toBeVisible();
  // And back out of the archive the same way.
  await toggle.tap();
  await expect(card).toHaveCount(0);
  await context.close();
});

// The agent button on a task row asks first, as the note's own
// Ask does, and the keyboard reaches it.
const agentToast = page => page.locator('#toast').filter({ hasText: 'Agent working in the background' });
async function noRunStarted(page, id) {
  await page.waitForTimeout(1500);
  expect((await read(owner, id)).agent_session_id ?? null).toBeNull();
  await expect(agentToast(page)).toBeHidden();
}

test('MINOR the agent button on a task asks before it starts a run, and the keyboard reaches it', async ({ browser }) => {
  const id = await makeNote(owner, 'QA item agent asks', 'Trip\n\n- [ ] ITEMASK book the train');
  const { context, page, body } = await openNote(browser, owner, id);
  const row = body.locator('li.md-task', { hasText: 'ITEMASK' });
  const button = row.locator('.note-task-agent');
  const menu = page.locator('.note-ask-menu');
  // One click opens the question about this item; nothing runs yet.
  await row.hover();
  await button.click();
  await expect(menu).toBeVisible();
  await expect(menu).toHaveAttribute('aria-label', 'Ask about this item');
  await expect(menu).toContainText('ITEMASK book the train');
  await noRunStarted(page, id);
  await page.keyboard.press('Escape');
  await expect(menu).toHaveCount(0);
  await noRunStarted(page, id);
  // Keyboard only: Tab from the title reaches the button, Enter opens the same question.
  await page.locator('#note-window-modal .note-window-title').focus();
  let reached = false;
  for (let i = 0; i < 20 && !reached; i++) {
    await page.keyboard.press('Tab');
    reached = await button.evaluate(el => el === document.activeElement);
  }
  expect(reached, 'Tab reaches the agent button').toBe(true);
  await expect(button).toHaveAttribute('aria-label', /.+/);
  expect(await button.evaluate(el => getComputedStyle(el).opacity)).not.toBe('0');
  await page.keyboard.press('Enter');
  await expect(menu).toBeVisible();
  await noRunStarted(page, id);
  // Enter opened the question and did nothing to the row.
  expect((await read(owner, id)).content).toBe('Trip\n\n- [ ] ITEMASK book the train');
  await expect(body.locator('li.md-task')).toHaveCount(1);
  // The run starts from the menu, about this item.
  await menu.getByRole('button', { name: /Agent: help with this/ }).click();
  await expect(agentToast(page)).toBeVisible();
  await expect.poll(async () => (await read(owner, id)).agent_session_id ?? null, { timeout: 15_000 }).not.toBeNull();
  await context.close();
});

test('MINOR on a phone a tap on a task\'s agent button does not start a run', async ({ browser }) => {
  const id = await makeNote(owner, 'QA phone item agent', 'Trip\n\n- [ ] PHONEASK pack the bags');
  const { context, page } = await phonePage(browser, owner);
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).tap();
  await settledWindow(page);
  const row = page.locator('#note-window-modal .note-window-body li.md-task', { hasText: 'PHONEASK' });
  await row.locator('.note-task-agent').tap();
  const menu = page.locator('.note-ask-menu');
  await expect(menu).toBeVisible();
  await expect(menu).toContainText('PHONEASK pack the bags');
  await noRunStarted(page, id);
  await context.close();
});
