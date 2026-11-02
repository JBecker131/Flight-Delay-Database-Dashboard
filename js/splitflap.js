/*
 * Split-flap tiles. Each tile advances through the drum in order until it
 * reaches its target character, like a real Solari board.
 */
window.SplitFlap = (function () {
  const DRUM = ' ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:.-/%';
  const STEP_MS = 55;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const active = new Set();
  let frameId = null;

  function paint(tile, ch) {
    tile._char = ch;
    tile.textContent = ch === ' ' ? '' : ch;
    tile._flip = !tile._flip;
    tile.classList.remove(tile._flip ? 'fb' : 'fa');
    tile.classList.add(tile._flip ? 'fa' : 'fb');
  }

  function frame(now) {
    for (const tile of active) {
      if (now < tile._next) continue;
      paint(tile, tile._seq.shift());
      if (tile._seq.length) tile._next = now + STEP_MS;
      else active.delete(tile);
    }
    frameId = active.size ? requestAnimationFrame(frame) : null;
  }

  function setChar(tile, ch, delay) {
    if (tile._target === ch) return;
    tile._target = ch;

    if (reducedMotion.matches) {
      active.delete(tile);
      tile._char = ch;
      tile.textContent = ch === ' ' ? '' : ch;
      return;
    }

    const to = DRUM.indexOf(ch);
    const from = DRUM.indexOf(tile._char);
    const seq = [];
    if (to < 0 || from < 0) {
      for (let i = 0; i < 4; i++) seq.push(DRUM[1 + Math.floor(Math.random() * (DRUM.length - 1))]);
    } else {
      // Skip most of the drum so a full board settles in about a second.
      const distance = (to - from + DRUM.length) % DRUM.length;
      const steps = Math.min(distance, 5 + Math.floor(Math.random() * 8));
      for (let i = steps - 1; i >= 1; i--) seq.push(DRUM[(to - i + DRUM.length) % DRUM.length]);
    }
    seq.push(ch);

    tile._seq = seq;
    tile._next = performance.now() + delay;
    active.add(tile);
    if (!frameId) frameId = requestAnimationFrame(frame);
  }

  function create(parent, count, className) {
    const tiles = [];
    for (let i = 0; i < count; i++) {
      const tile = document.createElement('span');
      tile.className = className || 'tile';
      tile.setAttribute('aria-hidden', 'true');
      tile._char = ' ';
      tile._target = ' ';
      parent.appendChild(tile);
      tiles.push(tile);
    }
    return tiles;
  }

  function write(tiles, text, { align = 'left', delay = 0, stagger = 22 } = {}) {
    let value = String(text || '').toUpperCase().slice(0, tiles.length);
    value = align === 'right' ? value.padStart(tiles.length) : value.padEnd(tiles.length);
    tiles.forEach((tile, i) => setChar(tile, value[i], delay + i * stagger));
  }

  function clear(tiles) {
    tiles.forEach((tile) => {
      active.delete(tile);
      tile._char = tile._target = ' ';
      tile.textContent = '';
    });
  }

  return { create, write, clear };
})();
