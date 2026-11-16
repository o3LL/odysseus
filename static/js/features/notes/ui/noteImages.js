/** Inline images in the note window: S / M / L sizes and Remove from a small menu, and image files carried by a paste or a drop. */
import { imageWidthTitle } from '../domain/markdownSerializer.js';

const IMAGE_SIZES = [
  { label: 'S', name: 'Small', width: 33 },
  { label: 'M', name: 'Medium', width: 66 },
  { label: 'L', name: 'Full width', width: 100 },
];

/** Image files in a paste or drop, in order. Text and other files are left to the caller. */
export function imageFiles(transfer) {
  let files = Array.from(transfer?.files || []);
  if (!files.length) files = Array.from(transfer?.items || []).filter(item => item.kind === 'file').map(item => item.getAsFile()).filter(Boolean);
  return files.filter(file => /^image\//i.test(file.type || ''));
}

const imageWidth = img => {
  const width = parseInt(img.dataset.w, 10);
  return Number.isFinite(width) ? width : 100;
};

/** Size an image the way the renderer would show its markdown. Full width carries no attribute, so it writes no title. */
export function setImageWidth(img, width) {
  const title = imageWidthTitle(width);
  if (!title) {
    delete img.dataset.w;
    img.style.removeProperty('width');
    if (!img.getAttribute('style')) img.removeAttribute('style');
    return;
  }
  const value = title.slice(2);
  img.dataset.w = value;
  img.style.width = `${value}%`;
}

/** Remove an image, and the paragraph it leaves with nothing in it. */
export function removeImage(img) {
  const parent = img.parentElement;
  img.remove();
  if (parent?.nodeName === 'P' && !parent.textContent.trim() && !parent.querySelector('img, hr')) parent.remove();
}

export function createImageMenu({ bindMenuDismiss, dismissOrRemove, portalZ = () => null }) {
  const closeAll = () => document.querySelectorAll('.note-image-menu').forEach(dismissOrRemove);

  /** Open beside the tap (`at`), or under the image; always fully on screen. */
  /** `keyboard`: opened from the keyboard, so focus moves into the menu and `returnFocus` gives it back when the menu closes. */
  function open(img, { at = null, onResize, onRemove, keyboard = false, returnFocus = () => {} }) {
    closeAll();
    const current = imageWidth(img);
    const menu = document.createElement('div');
    menu.className = 'note-reminder-menu note-image-menu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `<div class="note-reminder-menu-title">Image size</div>
      <div class="note-image-menu-sizes">${IMAGE_SIZES.map(size => `<button type="button" class="note-reminder-menu-item${size.width === current ? ' active' : ''}" data-w="${size.width}" title="${size.name}" aria-label="${size.name}" aria-pressed="${size.width === current}"><span>${size.label}</span></button>`).join('')}</div>
      <div class="note-reminder-menu-divider"></div>
      <button type="button" class="note-reminder-menu-item" data-act="remove"><span>Remove</span></button>`;
    // Above the note window, whose z-index climbs with every bring-to-front.
    const z = portalZ();
    if (z) menu.style.zIndex = String(z);
    document.body.appendChild(menu);
    const rect = img.getBoundingClientRect();
    const x = at ? at.x : rect.left;
    const y = at ? at.y + 8 : rect.bottom + 4;
    menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8))}px`;
    menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8))}px`;
    const close = bindMenuDismiss(menu, () => {
      const hadFocus = menu.contains(document.activeElement);
      menu.remove();
      if (hadFocus) returnFocus();
    });
    menu.addEventListener('mousedown', event => event.preventDefault());
    // A menu: the arrow keys move between its items (Safari does not Tab to buttons).
    menu.addEventListener('keydown', event => {
      const items = Array.from(menu.querySelectorAll('button'));
      const at = items.indexOf(document.activeElement);
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
      if (at < 0 || !step) return;
      event.preventDefault();
      items[(at + step + items.length) % items.length].focus();
    });
    if (keyboard) (menu.querySelector('[data-w].active') || menu.querySelector('button'))?.focus();
    menu.querySelectorAll('[data-w]').forEach(button => button.addEventListener('click', event => {
      event.stopPropagation();
      close();
      onResize(Number(button.dataset.w));
    }));
    menu.querySelector('[data-act="remove"]').addEventListener('click', event => {
      event.stopPropagation();
      close();
      onRemove();
    });
    return close;
  }

  return { open, closeAll };
}
