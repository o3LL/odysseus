/** Note versions this tab wrote itself. The note window follows those (a card checkbox ticked behind it) instead of calling them a conflict. */
export function createNoteVersions({ app }) {
  const state = app.state;
  const local = new Set();
  function record(note) {
    if (!note?.id || !note.updated_at) return;
    local.add(`${note.id}@${note.updated_at}`);
    const existing = state.notes.find(n => n.id === note.id);
    if (existing && existing.updated_at !== note.updated_at) app.mergeNote(existing, { updated_at: note.updated_at });
  }
  const after = (call, pick) => async (...args) => { const result = await call(...args); record(pick(result)); return result; };
  function track(api) {
    return {
      ...api,
      saveNote: after(api.saveNote, r => r),
      patchNote: after(api.patchNote, r => r),
      toggleTask: after(api.toggleTask, r => r?.note),
      appendTask: after(api.appendTask, r => r?.note),
    };
  }
  return { track, record, isLocal: (id, updatedAt) => local.has(`${id}@${updatedAt}`) };
}
