/**
 * What every registry action does. Keys, the command palette, slash commands,
 * buttons and the bulk bar all call `runAction(id)`, so behaviour lives once.
 * Single-item actions apply to the selection when one exists (`targetIds`).
 */
import { api, ApiError, fetchItems, flattenTags, invalidateLibrary, listOfQuery, queryClient, tagsQuery, type Item, type ItemPage, type TagNode } from './api';
import { currentSearch, goQuery, goTo, hasDrawer, isLibraryPath, pathname, setSearch, withDefaults } from './nav';
import { toggleToken } from './query';
import { activeId, closeOverlay, drawerId, getState, openOverlay, setState, targetIds, toast, toastError } from './store';
import type { View } from './constants';

/** Handlers a page registers for itself (the Proposed tags screen: approve, reject). */
let pageHandlers: Record<string, () => void> = {};
export const setPageHandlers = (handlers: Record<string, () => void>) => void (pageHandlers = handlers);

const plural = (n: number, word = 'item') => `${n} ${word}${n === 1 ? '' : 's'}`;

/** Moves the focus outline and brings the item into view; the drawer follows when open. */
export function focusItem(id: number | null, scroll = true) {
  setState({ focusId: id });
  if (id == null) return;
  if (hasDrawer() && currentSearch().item && currentSearch().item !== id) setSearch({ item: id }, true);
  if (scroll) requestAnimationFrame(() => reveal(document.querySelector(`[data-focus-id="${id}"]`)));
}

/** Scrolls just enough to show the element below the sticky header; a card taller than the
 *  window is shown from its top, so its author line is never hidden. */
function reveal(el: Element | null) {
  if (!el) return;
  // A list that scrolls inside its own box (the proposed-tag tree) moves that box, not the window.
  if (el.closest('[data-own-scroll]')) return el.scrollIntoView({ block: 'nearest' });
  const top = (document.querySelector('[data-sticky-header]')?.getBoundingClientRect().bottom ?? 0) + 8;
  const bottom = window.innerHeight - 16;
  const r = el.getBoundingClientRect();
  if (r.top < top || (r.bottom > bottom && r.height > bottom - top)) window.scrollBy(0, r.top - top);
  else if (r.bottom > bottom) window.scrollBy(0, r.bottom - bottom);
}

function move(delta: number, extend = false) {
  const { pageIds, focusId, selection } = getState();
  if (!pageIds.length) return;
  const at = focusId == null ? -1 : pageIds.indexOf(focusId);
  const next = at < 0 ? (delta > 0 ? 0 : pageIds.length - 1) : Math.min(pageIds.length - 1, Math.max(0, at + delta));
  if (extend) setState({ selection: new Set([...selection, ...(at >= 0 ? [pageIds[at]] : []), pageIds[next]]) });
  focusItem(pageIds[next]);
}

/** Updates items in every cached list at once, so a key press shows before the server answers. */
function patchCached(ids: number[], change: Partial<Item>) {
  const set = new Set(ids);
  queryClient.setQueriesData<ItemPage>({ queryKey: ['items'] }, (page) => page && { ...page, items: page.items.map((i) => (set.has(i.id) ? { ...i, ...change } : i)) });
  for (const id of ids) queryClient.setQueryData<Item>(['item', id], (i) => i && { ...i, ...change });
}

/** One change to many items through the bulk endpoint, which records an undo batch. */
export async function bulk(ids: number[], change: Record<string, unknown>, message: string) {
  if (!ids.length) return;
  try {
    const result = await api<{ updated: number; undo_batch_id: number }>('/api/items/bulk', { method: 'POST', body: { ids, ...change } });
    setState({ undoStack: [...getState().undoStack, result.undo_batch_id] });
    toast(message, { undoBatch: result.undo_batch_id });
  } catch (error) {
    toastError(error);
  }
  invalidateLibrary();
}

export async function undo(batch?: number) {
  const id = batch ?? getState().undoStack.at(-1);
  if (id == null) return toast('Nothing to undo');
  // A request that never got an answer (offline, server error) can be tried again; a refusal cannot.
  let retry = false;
  try {
    await api(`/api/undo/${id}`, { method: 'POST' });
    toast('Undone');
  } catch (error) {
    retry = !(error instanceof ApiError) || error.status >= 500;
    toastError(error);
  }
  // Read the stack now, not before the request: a change made meanwhile has added its own batch.
  if (!retry) setState({ undoStack: getState().undoStack.filter((b) => b !== id) });
  invalidateLibrary();
}

/** Turns a flag on for the targets, or off when every target already has it. */
function toggleFlag(field: 'starred' | 'read', onText: string, offText: string) {
  const ids = targetIds();
  const known = ids.map(itemById);
  const value = !known.every((i) => i?.[field]);
  patchCached(ids, { [field]: value });
  void bulk(ids, { [field]: value }, `${value ? onText : offText} ${plural(ids.length)}`);
}

export function setArchived(ids: number[], archived: boolean) {
  if (!ids.length) return;
  // Keep the outline on a neighbour instead of losing it with the items that leave the list.
  const { pageIds, focusId } = getState();
  const gone = new Set(ids);
  if (focusId != null && gone.has(focusId)) {
    const at = pageIds.indexOf(focusId);
    const next = pageIds.slice(at + 1).find((id) => !gone.has(id)) ?? pageIds.slice(0, at).reverse().find((id) => !gone.has(id)) ?? null;
    setState({ focusId: next });
  }
  setState({ selection: new Set() });
  if (currentSearch().item && gone.has(currentSearch().item!)) setSearch({ item: getState().focusId ?? undefined }, true);
  void bulk(ids, { archived }, archived ? `Moved ${plural(ids.length)} to Archive` : `Restored ${plural(ids.length)}`);
}

/** Finds a tag by full path, creating the missing levels (`ai/agents/memory`). */
export async function ensureTagPath(path: string): Promise<number> {
  const names = path.split('/').map((s) => s.trim()).filter(Boolean);
  if (!names.length) throw new Error('A tag needs a name');
  let level: TagNode[] = await queryClient.fetchQuery(tagsQuery);
  let parentId: number | null = null;
  for (const name of names) {
    const found: TagNode | undefined = level.find((n) => n.name.toLowerCase() === name.toLowerCase());
    if (found) {
      parentId = found.id;
      level = found.children;
    } else {
      parentId = (await api<{ id: number }>('/api/tags', { method: 'POST', body: { name, parent_id: parentId } })).id;
      level = [];
    }
  }
  void queryClient.invalidateQueries({ queryKey: ['tags'] });
  return parentId!;
}

export async function tagByPath(path: string, remove = false) {
  const ids = targetIds();
  if (!ids.length) return toast('Focus or select an item first');
  try {
    if (remove) {
      const tag = flattenTags(await queryClient.fetchQuery(tagsQuery)).find((t) => t.path.toLowerCase() === path.toLowerCase());
      if (!tag) return toast(`No tag named ${path}`, { error: true });
      await bulk(ids, { remove_tag_ids: [tag.id] }, `Removed #${tag.path} from ${plural(ids.length)}`);
    } else {
      await bulk(ids, { add_tag_ids: [await ensureTagPath(path)] }, `Tagged ${plural(ids.length)} #${path}`);
    }
  } catch (error) {
    toastError(error);
  }
}

async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`Copied ${what}`);
  } catch {
    toast('The browser blocked copying', { error: true });
  }
}

/** An item on the page, or one the drawer loaded (a quoted post, a thread parent). */
const itemById = (id: number): Item | undefined => getState().pageItems.get(id) ?? queryClient.getQueryData<Item>(['item', id]);
const targetItems = (): Item[] => targetIds().map(itemById).filter((i): i is Item => !!i);

export const markdownOf = (i: Item) => {
  const title = (i.title ?? i.body ?? '').replace(/\s+/g, ' ').slice(0, 80).trim();
  return `[${i.author.name ?? i.author.handle ?? i.source}: ${title}](${i.url ?? ''})`;
};

/** Selects every result of the current search, page by page. */
// ponytail: collects ids by paging (100 a request); add a bulk-by-query endpoint if libraries grow past ~50k.
async function selectAllResults() {
  const s = withDefaults(currentSearch());
  const ids: number[] = [];
  try {
    for (let page = 1; ; page++) {
      const result = await fetchItems({ q: s.q, mode: s.mode, sort: s.sort, page, size: 100, archived: pathname() === '/archive', snap: s.snap });
      ids.push(...result.items.map((i) => i.id));
      if (page >= result.pages) break;
    }
    setState({ selection: new Set(ids) });
    toast(`Selected all ${plural(ids.length, 'result')}`);
  } catch (error) {
    toastError(error);
  }
}

function selectAll() {
  const { pageIds, selection, total } = getState();
  if (!pageIds.length || !isLibraryPath()) return;
  const pageSelected = pageIds.every((id) => selection.has(id));
  if (!pageSelected) return setState({ selection: new Set([...selection, ...pageIds]) });
  // Second press: offer the whole result, never select it silently.
  if (total > pageIds.length && selection.size < total)
    openOverlay({ kind: 'confirm', title: `Select all ${total.toLocaleString()} results?`, body: `The ${pageIds.length} items on this page are selected. This selects every item the current view matches, on all pages.`, action: `Select all ${total.toLocaleString()}`, run: selectAllResults });
}

function focusSearch() {
  const focus = () => (document.getElementById('search-input') as HTMLInputElement | null)?.focus();
  // On the library the bar is there: focus at once, so the next key typed is not lost.
  if (isLibraryPath()) return focus();
  goTo('/');
  // After a route change the bar is not mounted yet; wait for it.
  requestAnimationFrame(() => setTimeout(focus, 0));
}

function escape() {
  const active = document.activeElement as HTMLElement | null;
  if (getState().overlay) return closeOverlay();
  if (active && active !== document.body && active.matches('input, textarea, select, [contenteditable="true"]')) return active.blur();
  if (getState().mobileNav) return setState({ mobileNav: false });
  if (hasDrawer() && currentSearch().item) return setSearch({ item: undefined });
  if (getState().selection.size) return setState({ selection: new Set() });
}

function togglePlay() {
  // The drawer's video when it is open; otherwise the focused card's.
  const video = document.querySelector<HTMLVideoElement>(drawerId() != null ? '[data-drawer] video[controls]' : `[data-focus-id="${getState().focusId}"] video[controls]`);
  if (!video) return toast('This item has no video');
  if (video.paused) void video.play();
  else video.pause();
}

const setView = (view: View) => setSearch({ view });
const pageBy = (delta: number) => {
  const page = withDefaults(currentSearch()).page + delta;
  if (isLibraryPath() && page >= 1 && page <= Math.max(1, Math.ceil(getState().total / withDefaults(currentSearch()).size))) setSearch({ page, item: undefined });
};
const withFocused = (fn: (item: Item) => void) => () => {
  const id = activeId();
  const item = id != null ? itemById(id) : undefined;
  if (item) fn(item);
};
const needTargets = (fn: (ids: number[]) => void) => () => {
  const ids = targetIds();
  if (ids.length) fn(ids);
};

const handlers: Record<string, () => void> = {
  'move.next': () => move(1),
  'move.prev': () => move(-1),
  'move.first': () => focusItem(getState().pageIds[0] ?? null),
  'move.last': () => focusItem(getState().pageIds.at(-1) ?? null),
  'page.prev': () => pageBy(-1),
  'page.next': () => pageBy(1),
  'item.open': withFocused((i) => setSearch({ item: i.id })),
  'item.openOriginal': withFocused((i) => i.url && window.open(i.url, '_blank', 'noopener')),
  escape,

  'go.all': () => goQuery(''),
  'go.untagged': () => goQuery('is:untagged'),
  'go.unread': () => goQuery('is:unread'),
  'go.starred': () => goQuery('is:starred'),
  'go.proposed': () => goTo('/proposed'),
  'go.archive': () => goTo('/archive'),
  'go.ask': () => goTo('/ask'),
  'go.console': () => goTo('/console'),
  'go.tag': () => openOverlay({ kind: 'palette', scope: 'tags' }),
  'go.list': () => openOverlay({ kind: 'palette', scope: 'lists' }),

  'item.tag': needTargets((ids) => openOverlay({ kind: 'picker', mode: 'add', ids })),
  'item.untag': needTargets((ids) => openOverlay({ kind: 'picker', mode: 'remove', ids })),
  'item.star': needTargets(() => toggleFlag('starred', 'Starred', 'Unstarred')),
  'item.read': needTargets(() => toggleFlag('read', 'Marked read:', 'Marked unread:')),
  'item.note': withFocused((i) => {
    setSearch({ item: i.id });
    setState({ noteFocus: getState().noteFocus + 1 });
  }),
  'item.archive': needTargets((ids) => setArchived(ids, pathname() !== '/archive')),
  undo: () => void undo(),
  'item.list': needTargets((ids) => openOverlay({ kind: 'lists', ids })),
  'list.moveUp': () => void moveInList(-1),
  'list.moveDown': () => void moveInList(1),
  'item.copyLink': () => {
    const items = targetItems();
    if (items.length) void copy(items.map((i) => i.url).join('\n'), items.length === 1 ? 'link' : `${items.length} links`);
  },
  'item.copyMarkdown': () => {
    const items = targetItems();
    if (items.length) void copy(items.map(markdownOf).join('\n'), 'as Markdown');
  },
  'item.mediaText': needTargets((ids) => openOverlay({ kind: 'mediaText', ids })),
  'item.play': togglePlay,

  'select.toggle': () => {
    const { focusId, selection } = getState();
    if (focusId == null) return;
    const next = new Set(selection);
    if (!next.delete(focusId)) next.add(focusId);
    setState({ selection: next });
  },
  'select.extendDown': () => move(1, true),
  'select.extendUp': () => move(-1, true),
  'select.all': selectAll,

  'search.focus': focusSearch,
  'palette.open': () => (getState().overlay?.kind === 'palette' ? closeOverlay() : openOverlay({ kind: 'palette' })),
  'search.author': withFocused((i) => i.author.handle && goQuery(toggleToken(currentSearch().q ?? '', `from:${i.author.handle}`))),
  'search.similar': withFocused((i) => goQuery(`similar:${i.id}`)),

  'view.list': () => setView('list'),
  'view.grid': () => setView('grid'),
  'view.compact': () => setView('compact'),
  help: () => openOverlay({ kind: 'help' }),
};

/** Moves the focused item one place in the list being viewed, and saves the list's new order. */
async function moveInList(delta: -1 | 1) {
  const { focusId, pageIds } = getState();
  const list = pathname() === '/' ? await listOfQuery(currentSearch().q ?? '') : undefined;
  if (!list) return toast('Open one list on its own (g l) to reorder it', { error: true });
  const from = focusId == null ? -1 : pageIds.indexOf(focusId);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= pageIds.length) return;
  const order = [...pageIds];
  [order[from], order[to]] = [order[to], order[from]];
  try {
    await api(`/api/lists/${list.id}/order`, { method: 'PUT', body: { item_ids: order } });
    invalidateLibrary();
  } catch (error) {
    toastError(error);
  }
}

export function runAction(id: string) {
  (pageHandlers[id] ?? handlers[id])?.();
}
