/**
 * Checklist item workflows. Supplied feedback callbacks update the invoking view.
 * A note open in the note window is written to from a card too: `effects.beforeWrite(id)`
 * lets the window finish its pending save first, so the two writes never race, and
 * `effects.followed(id)` runs once the card's write has landed, so the window takes it.
 * Every write names the version or the task it was made against: a tick sends the
 * task's text, an edit or removal sends `base_updated_at`. When the server says the
 * note changed (409), the card takes the server copy instead of overwriting it.
 */
import { applyItemsToContent, appendTaskLine, toggleTask as toggleTaskLine } from '../domain/markdownTasks.js';
import { sizeRefusal } from '../domain/noteRules.js';

export function createChecklistOperations({
  app,
  api,
  effects,
  uid
}) {
    const state = app.state;
    // Items are task lines inside `content`. Every optimistic change updates
    // both, using the same rules the server applies, so the card (which renders
    // content) and the item indices stay in step until the next fetch.
    function itemNote(id, index) {
      const note = state.notes.find(n => n.id === id);
      return note && Array.isArray(note.items) && note.items[index] ? note : null;
    }
    const snapshot = note => ({ content: note.content, items: note.items });
    // Straight away unless the window has a save to finish, so requests keep their order.
    const write = (id, call) => {
      const pending = effects.beforeWrite?.(id);
      return pending ? Promise.resolve(pending).catch(() => {}).then(call) : call();
    };
    /**
     * The server's copy after a toggle or an append: it also carries whatever the window saved just before.
     * The card was changed in place; when the server's copy differs from it, redraw, since the
     * next poll compares against state and would not see anything new.
     */
    function landed(note, result) {
      if (result?.note && typeof result.note.content === 'string') {
        const shown = note.content;
        app.mergeNote(note, { content: result.note.content, items: result.note.items });
        if (result.note.content !== shown) effects.render();
      }
      effects.followed?.(note.id);
    }
    function setItems(note, items) {
      app.mergeNote(note, { items, content: applyItemsToContent(note.content, items) });
    }
    function toggledContent(content, index) {
      try { return toggleTaskLine(content || '', index).content; } catch { return content; }
    }
    /** The newer note a 409 carries, or null for any other failure. */
    const newerCopy = error => (error?.status === 409 && error.body?.detail?.note) || null;
    function takeServerCopy(note, server) {
      app.mergeNote(note, { content: server.content, items: server.items, title: server.title, updated_at: server.updated_at });
    }
    const sameText = (a, b) => String(a || '').trim().split(/\s+/).join(' ') === String(b || '').trim().split(/\s+/).join(' ');
    /**
     * Rewrite the checklist with `change(items, index)` against the version the
     * card showed. If someone else changed the note first, redo the change once
     * on the server copy, locating the item again by its text; if it is gone or
     * ambiguous there, keep the server copy and report it.
     */
    function rewriteItems(note, index, change, failure) {
      const before = snapshot(note);
      const oldText = note.items[index].text;
      const send = (items, base) => api.patchNote(note.id, base ? { items, base_updated_at: base } : { items });
      const base = note.updated_at;
      setItems(note, change(note.items, index));
      effects.render();
      write(note.id, () => send(note.items, base)).catch(error => {
        const server = newerCopy(error);
        if (!server) throw error;
        takeServerCopy(note, server);
        const items = Array.isArray(server.items) ? server.items : [];
        const matches = items.map((it, i) => (sameText(it.text, oldText) ? i : -1)).filter(i => i >= 0);
        if (matches.length !== 1) { effects.render(); effects.showError('This checklist changed elsewhere. It has been refreshed.'); return 'refreshed'; }
        const retried = change(items, matches[0]);
        setItems(note, retried);
        effects.render();
        return send(retried, server.updated_at);
      }).then(outcome => { if (outcome !== 'refreshed') effects.followed?.(note.id); }, error => {
        const server = newerCopy(error);
        if (server) takeServerCopy(note, server); else app.mergeNote(note, before);
        effects.render(); effects.showError(sizeRefusal(error) || failure);
      });
    }
    function removeItem(id, index) {
      const note = itemNote(id, index);
      if (!note) return;
      rewriteItems(note, index, (items, at) => items.filter((_, i) => i !== at), 'Failed to remove item');
    }
    function editItem(note, index, text) {
      const oldText = (note.items[index].text || '').trim();
      if (text === oldText) { effects.render(); return; }
      rewriteItems(note, index, (items, at) => (text ? items.map((it, i) => (i === at ? { ...it, text } : it)) : items.filter((_, i) => i !== at)), 'Failed to update item');
    }
    function addItem(id, text, added) {
      const note = state.notes.find(n => n.id === id);
      if (!note) return;
      const before = snapshot(note);
      const items = Array.isArray(note.items) ? [...note.items] : [];
      items.push({ id: uid(), text, done: false });
      app.mergeNote(note, { items, content: appendTaskLine(note.content, text).content });
      added(); effects.render();
      // Server-side append: no whole-checklist PUT that could overwrite a
      // concurrent change to the same note.
      return () => write(id, () => api.appendTask(id, text)).then(result => landed(note, result), error => {
        app.mergeNote(note, before); effects.render(); effects.showError(sizeRefusal(error) || 'Failed to add item');
      });
    }
    function toggleItem(id, index, changed, completed) {
      const note = itemNote(id, index);
      if (!note) return;
      const wasDone = note.items.length > 0 && note.items.every(it => it.done);
      const before = note.content;
      app.updateItem(note, index, { done: !note.items[index].done });
      app.mergeNote(note, { content: toggledContent(before, index) });
      changed(note.items[index].done);
      if (!wasDone && note.items.length > 0 && note.items.every(it => it.done)) completed();
      // The card is updated in place, with no redraw: tell the note window,
      // which would otherwise save over this version and get a 409.
      const text = note.items[index].text;
      const done = !!note.items[index].done;
      write(id, () => api.toggleTask(id, index, text, done)).then(result => landed(note, result), error => {
        const server = newerCopy(error);
        if (server) {
          // The task moved or went away elsewhere: show the list as it is now.
          takeServerCopy(note, server);
          effects.render(); effects.showError('This checklist changed elsewhere. It has been refreshed.');
          return;
        }
        app.mergeNote(note, { content: before });
        app.updateItem(note, index, { done: !note.items[index].done }); changed(note.items[index].done);
      });
    }
    async function toggleTodayStep(id, index, changed, completed) {
      const note = itemNote(id, index);
      if (!note) return;
      const before = note.content;
      app.updateItem(note, index, { done: !note.items[index].done });
      app.mergeNote(note, { content: toggledContent(before, index) }); changed();
      const text = note.items[index].text;
      const done = !!note.items[index].done;
      try {
        await write(id, () => api.toggleTask(id, index, text, done)); effects.render();
        if (note.items.every(it => it.done)) completed();
      } catch (error) {
        const server = newerCopy(error);
        if (server) {
          takeServerCopy(note, server);
          effects.render(); effects.showError('This checklist changed elsewhere. It has been refreshed.');
          return;
        }
        app.mergeNote(note, { content: before });
        app.updateItem(note, index, { done: !note.items[index].done });
      }
    }
    return { removeItem, editItem, addItem, toggleItem, toggleTodayStep };
}
