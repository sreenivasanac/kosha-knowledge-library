/**
 * UI state shared by the keyboard handlers, the palette and the components:
 * focus, selection, the open overlay, toasts and the undo stack. A plain
 * module-level store so actions can run from anywhere, not only from hooks.
 */
import { useSyncExternalStore } from 'react';
import type { Item, Media } from './api';
import { STORAGE_KEYS, TOAST_MS } from './constants';

export type Overlay =
  | { kind: 'picker'; mode: 'add' | 'remove'; ids: number[] }
  | { kind: 'palette'; scope?: 'tags' | 'lists'; initial?: string }
  | { kind: 'help' }
  | { kind: 'lists'; ids: number[] }
  | { kind: 'jev'; question?: string }
  | { kind: 'mediaText'; ids: number[] | 'all' }
  | { kind: 'saveView'; name?: string }
  | { kind: 'lightbox'; media: Media[]; index: number }
  | { kind: 'confirm'; title: string; body: string; action: string; danger?: boolean; run: () => void | Promise<void> };

export interface Toast {
  id: number;
  text: string;
  undoBatch?: number;
  error?: boolean;
}

interface State {
  /** Ids in the order shown on the current page; what j/k walk over. */
  pageIds: number[];
  /** The page's items by id (empty on pages that list something else). */
  pageItems: Map<number, Item>;
  total: number;
  focusId: number | null;
  selection: Set<number>;
  /** Where a Shift+j/k run started. */
  anchorId: number | null;
  overlay: Overlay | null;
  toasts: Toast[];
  undoStack: number[];
  sidebarOpen: boolean;
  mobileNav: boolean;
  /** Keys typed so far of a sequence (`g`), shown as a hint. */
  pendingKeys: string;
  /** Bumped to ask the drawer to put the caret in the note editor. */
  noteFocus: number;
}

const stored = (key: string) => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
};

let state: State = {
  pageIds: [],
  pageItems: new Map(),
  total: 0,
  focusId: null,
  selection: new Set(),
  anchorId: null,
  overlay: null,
  toasts: [],
  undoStack: [],
  sidebarOpen: stored(STORAGE_KEYS.sidebar) !== 'closed',
  mobileNav: false,
  pendingKeys: '',
  noteFocus: 0,
};
const listeners = new Set<() => void>();

export const getState = () => state;
export function setState(patch: Partial<State>) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}
const subscribe = (l: () => void) => (listeners.add(l), () => void listeners.delete(l));
export const useStore = <T>(select: (s: State) => T): T => useSyncExternalStore(subscribe, () => select(state));

export const openOverlay = (overlay: Overlay) => setState({ overlay });
export const closeOverlay = () => setState({ overlay: null });

let toastId = 0;
export function toast(text: string, extra: Partial<Toast> = {}) {
  const id = ++toastId;
  // One toast at a time keeps the Undo target unambiguous.
  setState({ toasts: [{ id, text, ...extra }] });
  setTimeout(() => setState({ toasts: getState().toasts.filter((t) => t.id !== id) }), TOAST_MS);
}
export const toastError = (error: unknown) => toast(error instanceof Error ? error.message : String(error), { error: true });

export function setSidebarOpen(open: boolean) {
  try {
    localStorage.setItem(STORAGE_KEYS.sidebar, open ? 'open' : 'closed');
  } catch {
    /* private mode: the choice just does not persist */
  }
  setState({ sidebarOpen: open });
}

/** The items an action applies to: the selection when there is one, else the focused item. */
export function targetIds(): number[] {
  if (state.selection.size) return [...state.selection];
  return state.focusId != null ? [state.focusId] : [];
}
