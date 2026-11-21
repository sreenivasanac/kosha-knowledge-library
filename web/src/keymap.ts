/**
 * Central keymap registry: every action the app has, with its default keys.
 * The `?` overlay, tooltips, the command palette and the Settings remap page
 * are all generated from ACTIONS. This file is pure (no DOM, no React) so it
 * can be tested directly.
 *
 * Key notation: a binding is one or more strokes separated by spaces
 * (`g a`). A stroke is optional modifiers then a key: `Mod+k` (Cmd on a Mac,
 * Ctrl elsewhere), `Shift+j`, `Enter`, `?`. Several alternatives for one
 * action are separated by ` | ` (`Enter | o`).
 */

export type Area = 'Move' | 'Go to' | 'Act' | 'Select' | 'Search' | 'View' | 'Proposed tags';

export interface ActionDef {
  id: string;
  area: Area;
  label: string;
  keys: string;
}

export const ACTIONS: ActionDef[] = [
  { id: 'move.next', area: 'Move', label: 'Next item', keys: 'j' },
  { id: 'move.prev', area: 'Move', label: 'Previous item', keys: 'k' },
  { id: 'move.first', area: 'Move', label: 'First on the page', keys: 'g g' },
  { id: 'move.last', area: 'Move', label: 'Last on the page', keys: 'Shift+g' },
  { id: 'page.prev', area: 'Move', label: 'Previous page', keys: '[' },
  { id: 'page.next', area: 'Move', label: 'Next page', keys: ']' },
  { id: 'item.open', area: 'Move', label: 'Open the reader drawer', keys: 'Enter | o' },
  { id: 'item.openOriginal', area: 'Move', label: 'Open the original in a new tab', keys: 'Shift+o' },
  { id: 'escape', area: 'Move', label: 'Close drawer, clear selection, leave search', keys: 'Escape' },

  { id: 'go.all', area: 'Go to', label: 'All', keys: 'g a' },
  { id: 'go.untagged', area: 'Go to', label: 'Untagged', keys: 'g u' },
  { id: 'go.unread', area: 'Go to', label: 'Unread', keys: 'g r' },
  { id: 'go.starred', area: 'Go to', label: 'Starred', keys: 'g s' },
  { id: 'go.proposed', area: 'Go to', label: 'Proposed tags', keys: 'g p' },
  { id: 'go.archive', area: 'Go to', label: 'Archive', keys: 'g x' },
  { id: 'go.ask', area: 'Go to', label: 'Ask', keys: 'g k' },
  { id: 'go.console', area: 'Go to', label: 'Pipeline console', keys: 'g q' },
  { id: 'go.tag', area: 'Go to', label: 'Jump to a tag', keys: 'g t' },
  { id: 'go.list', area: 'Go to', label: 'Jump to a list', keys: 'g l' },

  { id: 'item.tag', area: 'Act', label: 'Add a tag', keys: 't' },
  { id: 'item.untag', area: 'Act', label: 'Remove a tag', keys: 'Shift+t' },
  { id: 'item.star', area: 'Act', label: 'Star', keys: 's' },
  { id: 'item.read', area: 'Act', label: 'Toggle read', keys: 'Shift+r' },
  { id: 'item.note', area: 'Act', label: 'Edit note', keys: 'n' },
  { id: 'item.archive', area: 'Act', label: 'Mark for deletion (to Archive)', keys: 'd' },
  { id: 'undo', area: 'Act', label: 'Undo the last action', keys: 'z' },
  { id: 'item.list', area: 'Act', label: 'Add to list', keys: 'l' },
  { id: 'item.copyLink', area: 'Act', label: 'Copy link', keys: 'c' },
  { id: 'item.copyMarkdown', area: 'Act', label: 'Copy as Markdown', keys: 'Shift+c' },
  { id: 'item.mediaText', area: 'Act', label: 'Get media text', keys: 'm' },
  { id: 'item.play', area: 'Act', label: 'Play or pause the video', keys: 'p' },

  { id: 'select.toggle', area: 'Select', label: 'Select or unselect', keys: 'x' },
  { id: 'select.extendDown', area: 'Select', label: 'Extend the selection down', keys: 'Shift+j' },
  { id: 'select.extendUp', area: 'Select', label: 'Extend the selection up', keys: 'Shift+k' },
  { id: 'select.all', area: 'Select', label: 'Select the page, then all results', keys: 'Mod+a' },

  { id: 'search.focus', area: 'Search', label: 'Focus search', keys: '/' },
  { id: 'palette.open', area: 'Search', label: 'Command palette', keys: 'Mod+k' },
  { id: 'search.author', area: 'Search', label: "Filter by this item's author", keys: 'f' },
  { id: 'search.similar', area: 'Search', label: 'Find similar items', keys: 'Shift+f' },

  { id: 'view.list', area: 'View', label: 'List view', keys: 'v l' },
  { id: 'view.grid', area: 'View', label: 'Grid view', keys: 'v g' },
  { id: 'view.compact', area: 'View', label: 'Compact rows', keys: 'v c' },
  { id: 'help', area: 'View', label: 'Shortcut help', keys: '?' },

  { id: 'proposed.approve', area: 'Proposed tags', label: 'Approve the proposed tag', keys: 'a' },
  { id: 'proposed.reject', area: 'Proposed tags', label: 'Reject the proposed tag', keys: 'r' },
  { id: 'proposed.approveTree', area: 'Proposed tags', label: 'Approve the tag and all under it', keys: 'Shift+a' },
  { id: 'proposed.rejectTree', area: 'Proposed tags', label: 'Reject the tag and all under it', keys: 'Shift+x' },
  { id: 'proposed.doubtful', area: 'Proposed tags', label: 'Show only doubtful items (conf below 0.9)', keys: 'u' },
];

export type Bindings = Record<string, string>;

/** Actions that still fire while the caret is in a text field. */
export const WORKS_WHILE_TYPING = new Set(['palette.open', 'escape']);

export const defaultBindings = (): Bindings => Object.fromEntries(ACTIONS.map((a) => [a.id, a.keys]));

/** Defaults with the user's remaps on top; overrides for unknown actions are dropped. */
export function resolveBindings(overrides: Bindings): Bindings {
  const out = defaultBindings();
  for (const [id, keys] of Object.entries(overrides)) if (id in out && keys.trim()) out[id] = keys;
  return out;
}

/** The alternatives of a binding, each as a list of strokes. */
export const sequencesOf = (keys: string): string[][] =>
  keys.split('|').map((alt) => alt.trim().split(/\s+/).filter(Boolean)).filter((seq) => seq.length > 0);

export interface KeyLike {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
}

/**
 * One keydown as a stroke, or null for a bare modifier press. Shift is kept
 * only for letters and named keys: `?` already implies it, and keeping it
 * would make the binding depend on the keyboard layout.
 */
export function strokeOf(e: KeyLike): string | null {
  if (['Shift', 'Meta', 'Control', 'Alt', 'CapsLock'].includes(e.key)) return null;
  const letter = e.key.length === 1 && /[a-z]/i.test(e.key);
  const key = e.key === ' ' ? 'Space' : e.key.length === 1 ? e.key.toLowerCase() : e.key;
  let out = '';
  if (e.metaKey || e.ctrlKey) out += 'Mod+';
  if (e.altKey) out += 'Alt+';
  if (e.shiftKey && (letter || e.key.length > 1)) out += 'Shift+';
  return out + key;
}

export type Match = { type: 'action'; id: string } | { type: 'pending' } | { type: 'none' };

/**
 * Feeds strokes one at a time and reports when they complete a binding.
 * `pending` means the strokes so far start a longer sequence (`g` of `g a`);
 * the caller resets after a timeout. `allowed` filters which actions may match
 * (used to ignore most keys while typing).
 */
export function createMatcher(bindings: Bindings) {
  const table = Object.entries(bindings).flatMap(([id, keys]) => sequencesOf(keys).map((seq) => ({ id, seq })));
  let buffer: string[] = [];
  const startsWith = (seq: string[], prefix: string[]) => prefix.every((s, i) => seq[i] === s);

  function feed(stroke: string, allowed: (id: string) => boolean = () => true): Match {
    const attempt = (strokes: string[]): Match => {
      const candidates = table.filter((b) => allowed(b.id) && startsWith(b.seq, strokes));
      const exact = candidates.find((b) => b.seq.length === strokes.length);
      if (exact) return { type: 'action', id: exact.id };
      return candidates.length ? { type: 'pending' } : { type: 'none' };
    };
    let result = attempt([...buffer, stroke]);
    // A stroke that does not continue the sequence starts a new one (`g` then `j` moves down).
    if (result.type === 'none' && buffer.length) {
      buffer = [];
      result = attempt([stroke]);
    }
    buffer = result.type === 'pending' ? [...buffer, stroke] : [];
    return result;
  }
  return { feed, reset: () => void (buffer = []), strokes: () => [...buffer] };
}

export interface Conflict {
  a: string;
  b: string;
  keys: string;
}

/**
 * Two actions conflict when they share a sequence, or when one's sequence is
 * the beginning of the other's (`g` would hide `g a`).
 */
export function findConflicts(bindings: Bindings): Conflict[] {
  const all = Object.entries(bindings).flatMap(([id, keys]) => sequencesOf(keys).map((seq) => ({ id, seq })));
  const out: Conflict[] = [];
  for (let i = 0; i < all.length; i++) {
    for (let j = i + 1; j < all.length; j++) {
      const [x, y] = [all[i], all[j]];
      if (x.id === y.id) continue;
      const [short, long] = x.seq.length <= y.seq.length ? [x, y] : [y, x];
      if (short.seq.every((s, k) => long.seq[k] === s)) out.push({ a: x.id, b: y.id, keys: short.seq.join(' ') });
    }
  }
  return out;
}

/** A binding as people read it: `⌘K`, `Shift+J`, `g a`, `Enter or o`. */
export function formatKeys(keys: string, mac = true): string {
  const stroke = (s: string) =>
    s
      .split('+')
      .map((part) => (part === 'Mod' ? (mac ? '⌘' : 'Ctrl') : part === 'Escape' ? 'Esc' : part.length === 1 && s.includes('+') ? part.toUpperCase() : part))
      .join('+')
      .replace('⌘+', '⌘');
  return sequencesOf(keys).map((seq) => seq.map(stroke).join(' ')).join(' or ');
}
