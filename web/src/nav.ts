/** URL state of the library views, and the few navigation helpers actions need. */
import type { AnyRouter } from '@tanstack/react-router';
import { DEFAULT_SORT, MODES, PAGE_SIZES, VIEWS, type Mode, type View } from './constants';
import { preferredPageSize, preferredView } from './prefs';

/** Everything a library view needs is in the URL, so reload and Back restore it. */
export interface LibrarySearch {
  q?: string;
  mode?: Mode;
  sort?: string;
  view?: View;
  page?: number;
  size?: number;
  /** The item open in the reader drawer. */
  item?: number;
  /** A Jev check's result set, and the question that made it (for the chip). */
  snap?: string;
  jq?: string;
}

const oneOf = <T extends string>(allowed: readonly T[], v: unknown): T | undefined => (allowed.includes(v as T) ? (v as T) : undefined);
const positive = (v: unknown): number | undefined => (Number.isInteger(Number(v)) && Number(v) > 0 ? Number(v) : undefined);

export function validateLibrarySearch(raw: Record<string, unknown>): LibrarySearch {
  return {
    // The router parses `q=2026` as a number; the bar always wants text.
    q: raw.q == null || raw.q === '' ? undefined : String(raw.q),
    mode: oneOf(MODES, raw.mode),
    sort: typeof raw.sort === 'string' ? raw.sort : undefined,
    view: oneOf(VIEWS, raw.view),
    page: positive(raw.page),
    size: PAGE_SIZES.includes(Number(raw.size) as (typeof PAGE_SIZES)[number]) ? Number(raw.size) : undefined,
    item: positive(raw.item),
    snap: raw.snap == null ? undefined : String(raw.snap),
    jq: raw.jq == null ? undefined : String(raw.jq),
  };
}

export const withDefaults = (s: LibrarySearch) => ({
  ...s,
  q: s.q ?? '',
  mode: s.mode ?? 'hybrid',
  sort: s.sort ?? DEFAULT_SORT,
  view: s.view ?? preferredView(),
  page: s.page ?? 1,
  size: s.size ?? preferredPageSize(),
});

let router: AnyRouter;
export const setRouter = (r: AnyRouter) => void (router = r);
export const getRouter = () => router;

// `latestLocation` moves as soon as a navigation starts, so keys pressed in quick succession
// (Esc then `v g`) each build on the one before instead of on the page still shown.
export const pathname = () => router.latestLocation.pathname;
export const isLibraryPath = (path = pathname()) => path === '/' || path === '/archive';
/** Pages that list items and can show one in the reader drawer (`item` in the URL). */
export const hasDrawer = (path = pathname()) => isLibraryPath(path) || path === '/ask';
export const currentSearch = (): LibrarySearch => validateLibrarySearch(router.latestLocation.search as Record<string, unknown>);

/** Drops values equal to the defaults so URLs stay short. */
function clean(s: LibrarySearch): LibrarySearch {
  const out: Record<string, unknown> = {};
  const d = withDefaults({});
  for (const [k, v] of Object.entries(s)) if (v != null && v !== '' && v !== (d as Record<string, unknown>)[k]) out[k] = v;
  return out;
}

/** Changes some URL state of the current library view. */
export function setSearch(patch: LibrarySearch, replace = false) {
  void router.navigate({ to: hasDrawer() ? pathname() : '/', search: clean({ ...currentSearch(), ...patch }), replace });
}

/** Shows the library filtered by `q`, keeping the view, sort, mode and page size. */
export function goQuery(q: string, replace = false) {
  const { view, size, mode, sort } = currentSearch();
  void router.navigate({ to: '/', search: clean({ q, view, size, mode, sort }), replace });
}

export const goTo = (to: string) => void router.navigate({ to });
