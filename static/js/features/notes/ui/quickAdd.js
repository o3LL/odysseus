/** Quick-add bar: the start of a new note, which opens in the note window. */
export function createQuickAdd({
  openNewNote,
  openPanel,
  state
}) {
  function _renderQuickAdd(body) {
    const wrap = document.createElement('div');
    wrap.className = 'notes-quick-add';
    // 2-pill Note/Todo toggle. The pill that's active steers both the
    // placeholder and how the new note starts.
    wrap.innerHTML = `
    <div class="notes-quick-type-seg is-todo" role="group" aria-label="New item type">
      <button type="button" class="notes-quick-type-pill" data-type="note" aria-label="Note" aria-pressed="false" title="Note">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="14" y2="18"/></svg>
      </button>
      <button type="button" class="notes-quick-type-pill active" data-type="todo" aria-label="To-do" aria-pressed="true" title="To-do">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
      </button>
    </div>
    <input type="text" class="notes-quick-input" placeholder="Add a to-do…" />
    <button class="notes-quick-icon" data-action="photo" title="Attach photo">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
    </button>
  `;
    body.appendChild(wrap);

    const input = wrap.querySelector('.notes-quick-input');
    const seg = wrap.querySelector('.notes-quick-type-seg');
    let currentType = 'todo';
    const setType = (t) => {
      if (t !== 'note' && t !== 'todo') return;
      currentType = t;
      seg.classList.toggle('is-todo', t === 'todo');
      seg.classList.toggle('is-note', t === 'note');
      seg.querySelectorAll('.notes-quick-type-pill').forEach(p => {
        const on = p.dataset.type === t;
        p.classList.toggle('active', on);
        p.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      input.placeholder = t === 'note' ? 'Add a note…' : 'Add a to-do…';
    };
    seg.querySelectorAll('.notes-quick-type-pill').forEach(p => {
      p.addEventListener('click', (e) => {
        e.stopPropagation();
        setType(p.dataset.type);
      });
    });
    // A click on the input or the first keystroke opens a new note window,
    // carrying what was typed as its title; a to-do also starts with an empty
    // task. Focus alone (stolen by a missed nearby click) opens nothing.
    const expand = () => {
      const text = input.value;
      input.value = '';
      openNewNote({ type: currentType, text });
    };
    input.addEventListener('click', expand);
    input.addEventListener('input', expand);
    wrap.querySelector('[data-action="photo"]').addEventListener('click', (e) => {
      e.stopPropagation();
      openNewNote({ type: currentType, text: '', pickImage: true });
    });
  }

  function newNote() {
    if (!state.open) openPanel();
    requestAnimationFrame(() => openNewNote({ type: 'note' }));
  }

  return { _renderQuickAdd, newNote };
}
