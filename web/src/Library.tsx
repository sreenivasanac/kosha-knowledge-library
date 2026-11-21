/** The main view (also the Archive): search bar, tag chip row, the items in one of three layouts, numbered pages. */
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Link, useSearch } from '@tanstack/react-router';
import { Archive, ArchiveRestore, BookmarkPlus, ChevronLeft, ChevronRight, LayoutGrid, List, ListPlus, Menu, Rows3, Sparkles, Star, Tag, Trash2, X } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, type ReactNode } from 'react';
import { runAction, setArchived } from './actions';
import { ArchiveTools, deleteForever } from './ArchiveTools';
import { api, ApiError, fetchItems, useCapabilities, type Item, type Suggest } from './api';
import { PAGE_SIZES, SORTS, STORAGE_KEYS, type View } from './constants';
import { ItemCard } from './ItemCard';
import { setSearch, withDefaults, type LibrarySearch } from './nav';
import { hasToken, quoteValue, toggleToken } from './query';
import { FilterChips, SearchBar } from './SearchBar';
import { getState, openOverlay, setState, useStore } from './store';
import { ErrorNote, useHint } from './ui';

/** The reading column; notices and empty states share it so they line up with the list. */
export const COLUMN = 'mx-auto max-w-[680px]';
const LAYOUT: Record<View, string> = {
  // A reading column of about 680px, centred in the space the sidebar and the drawer leave.
  list: `${COLUMN} overflow-hidden rounded-lg border border-line bg-surface`,
  grid: 'grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-3',
  compact: 'mx-auto max-w-[1100px] overflow-hidden rounded-lg border border-line bg-surface',
};
/** Placeholder heights close to the real cards, so loading does not shift the page. */
const SKELETON: Record<View, string> = { list: 'h-44 border-b border-line', grid: 'h-72 rounded-lg', compact: 'h-11 border-b border-line' };

function Skeletons({ view }: { view: View }) {
  return (
    <div className={LAYOUT[view]} aria-busy="true" aria-label="Loading items">
      {Array.from({ length: view === 'compact' ? 16 : 6 }, (_, i) => (
        <div key={i} className={`${SKELETON[view]} p-3`}>
          <div className="skeleton h-full w-full" />
        </div>
      ))}
    </div>
  );
}

/** Tags of the current result with counts; clicking one narrows the view. Counts come from the server's
 *  suggestions when it has them, otherwise from the items on this page. */
function TagChips({ q, items }: { q: string; items: Item[] }) {
  const caps = useCapabilities();
  // The suggest endpoint counts each value inside the current result: ask it for tags (`#`) and for `is:` states.
  const ask = (suffix: string) => {
    const probe = q ? `${q} ${suffix}` : suffix;
    return { queryKey: ['suggest', 'chips', probe], queryFn: () => api<Suggest>(`/api/search/suggest?${new URLSearchParams({ q: probe, cursor: String(probe.length) })}`), enabled: caps.suggest, placeholderData: keepPreviousData };
  };
  const suggestedTags = useQuery(ask('#')).data;
  const suggestedStates = useQuery(ask('is:')).data;

  let tags: { token: string; label: string; count: number | null }[];
  let untagged: number | null;
  if (suggestedTags) {
    // `#a/*` ("only what is beneath") is a refinement for the bar, not a chip.
    tags = suggestedTags.suggestions.filter((s) => s.kind === 'tag' && !/\/\*"?\s*$/.test(s.insert) && s.count !== 0).map((s) => ({ token: s.insert.trim(), label: s.label, count: s.count }));
    untagged = suggestedStates?.suggestions.find((s) => s.insert.trim() === 'untagged')?.count ?? null;
  } else {
    const counts = new Map<string, number>();
    for (const item of items) for (const t of item.tags) counts.set(t.path.join('/'), (counts.get(t.path.join('/')) ?? 0) + 1);
    tags = [...counts].sort((a, b) => b[1] - a[1]).map(([path, n]) => ({ token: `#${quoteValue(path)}`, label: path, count: n }));
    untagged = items.filter((i) => !i.tags.length).length;
  }
  const chip = (token: string, label: string, n: number | null) => {
    const on = hasToken(q, token);
    return (
      <button key={token} aria-pressed={on} onClick={() => setSearch({ q: toggleToken(q, token), page: undefined, item: undefined })} className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-sm ${on ? 'border-accent bg-accent-soft text-accent' : 'border-line bg-surface text-ink-2 hover:border-ink-3'}`}>
        {label}
        {n != null && <span className="text-xs tabular-nums text-ink-3">{n.toLocaleString()}</span>}
      </button>
    );
  };
  return (
    <div className="flex items-center gap-1.5 overflow-x-auto py-0.5" aria-label="Filter by tag" title={suggestedTags ? undefined : 'Counts are for the items on this page'}>
      {chip('is:untagged', 'Untagged', untagged)}
      {tags.slice(0, 24).map((t) => chip(t.token, t.label, t.count))}
    </div>
  );
}

function BulkBar({ archived }: { archived: boolean }) {
  const n = useStore((s) => s.selection.size);
  const total = useStore((s) => s.total);
  const hint = useHint();
  const button = (label: string, action: string, icon: ReactNode) => (
    <button className="btn btn-ghost h-8 px-2" title={hint(label, action)} onClick={() => runAction(action)}>
      {icon}
      <span className="max-lg:sr-only">{label}</span>
    </button>
  );
  return (
    // On a phone the bar is fixed at the bottom; on a desktop it takes the toolbar's place, so nothing moves.
    <div role="toolbar" aria-label="Actions for the selection" className="flex h-10 items-center gap-1 rounded-lg border border-accent bg-surface px-2 max-sm:fixed max-sm:inset-x-2 max-sm:bottom-2 max-sm:z-30 max-sm:overflow-x-auto max-sm:shadow-pop">
      <span className="shrink-0 px-1 text-sm font-semibold tabular-nums">{n.toLocaleString()} selected</span>
      {n < total && (
        <button className="shrink-0 text-sm text-accent hover:underline max-sm:hidden" onClick={() => runAction('select.all')} title={hint('Select all', 'select.all')}>
          Select all
        </button>
      )}
      <span className="mx-1 h-5 w-px shrink-0 bg-line max-sm:hidden" />
      {button('Tag', 'item.tag', <Tag size={15} />)}
      {button('Untag', 'item.untag', <Tag size={15} className="opacity-50" />)}
      {button('Star', 'item.star', <Star size={15} />)}
      {button('Read', 'item.read', <List size={15} />)}
      {button('Add to list', 'item.list', <ListPlus size={15} />)}
      <span className="flex-1" />
      <button className="btn btn-ghost h-8 px-2 text-ink-3" aria-label="Clear the selection" onClick={() => setState({ selection: new Set() })}>
        <span className="max-sm:hidden">Clear</span>
        <X size={15} className="sm:hidden" />
      </button>
      {/* Set apart from the rest: these remove items from the view. */}
      <span className="mx-1 h-5 w-px bg-line" />
      {archived ? (
        <>
          <button className="btn h-8" onClick={() => setArchived([...getState().selection], false)}>
            <ArchiveRestore size={15} /> Restore
          </button>
          <button className="btn btn-danger h-8 shrink-0" onClick={() => deleteForever([...getState().selection])}>
            <Trash2 size={15} /> Delete
          </button>
        </>
      ) : (
        <button className="btn btn-danger h-8 shrink-0" title={hint('Move to Archive', 'item.archive')} onClick={() => runAction('item.archive')}>
          <Archive size={15} /> Archive
        </button>
      )}
    </div>
  );
}

function Toolbar({ s, total }: { s: ReturnType<typeof withDefaults>; total: number | undefined }) {
  const caps = useCapabilities();
  const hint = useHint();
  const views: [View, string, ReactNode][] = [
    ['list', 'List view', <List size={15} />],
    ['grid', 'Grid view', <LayoutGrid size={15} />],
    ['compact', 'Compact rows', <Rows3 size={15} />],
  ];
  return (
    <div className="flex min-h-10 min-w-0 items-center gap-2 max-sm:flex-wrap">
      <span className="shrink-0 text-sm tabular-nums text-ink-2" aria-live="polite">
        {total == null ? '' : `${total.toLocaleString()} item${total === 1 ? '' : 's'}`}
      </span>
      <FilterChips q={s.q} jevQuestion={s.snap ? (s.jq ?? 'result set') : undefined} />
      <button className="btn btn-ghost px-2" disabled={!caps.jev} title={caps.jev ? 'Ask a yes/no question of every item in this result' : 'Jev check is not available on this server yet'} onClick={() => openOverlay({ kind: 'jev' })}>
        <Sparkles size={15} /> <span className="max-md:sr-only">Jev check</span>
      </button>
      <button className="btn btn-ghost px-2" disabled={!caps.savedViews} title={caps.savedViews ? 'Save this search as a view' : 'Saved views are not available on this server yet'} onClick={() => openOverlay({ kind: 'saveView' })}>
        <BookmarkPlus size={15} /> <span className="max-md:sr-only">Save view</span>
      </button>
      <select aria-label="Sort" className="field min-w-0 max-w-40 shrink" value={s.sort} onChange={(e) => setSearch({ sort: e.target.value, page: undefined })}>
        {SORTS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
        {!SORTS.some((o) => o.value === s.sort) && <option value={s.sort}>{s.sort}</option>}
      </select>
      <div role="radiogroup" aria-label="Layout" className="flex shrink-0 rounded-md border border-line bg-surface p-0.5">
        {views.map(([view, label, icon]) => (
          <button key={view} role="radio" aria-checked={s.view === view} aria-label={label} title={hint(label, `view.${view}`)} onClick={() => setSearch({ view })} className={`grid h-7 w-7 place-items-center rounded ${s.view === view ? 'bg-accent-soft text-accent' : 'text-ink-2 hover:text-ink'}`}>
            {icon}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Page numbers to show: both ends, and a window around the current page. */
function pageWindow(page: number, pages: number): (number | null)[] {
  const wanted = new Set([1, 2, pages - 1, pages, page - 2, page - 1, page, page + 1, page + 2].filter((p) => p >= 1 && p <= pages));
  const sorted = [...wanted].sort((a, b) => a - b);
  return sorted.flatMap((p, i) => (i > 0 && p - sorted[i - 1] > 1 ? [null, p] : [p]));
}

function Pagination({ page, pages, size, view }: { page: number; pages: number; size: number; view: View }) {
  const hint = useHint();
  const go = (p: number) => setSearch({ page: p, item: undefined });
  return (
    <nav aria-label="Pages" className={`flex flex-wrap items-center gap-x-4 gap-y-2 py-4 text-sm ${view === 'grid' ? '' : view === 'compact' ? 'mx-auto max-w-[1100px]' : COLUMN}`}>
      <div className="flex items-center gap-1">
        <button className="btn px-2" disabled={page <= 1} onClick={() => go(page - 1)} title={hint('Previous page', 'page.prev')} aria-label="Previous page">
          <ChevronLeft size={15} />
        </button>
        {pageWindow(page, pages).map((p, i) =>
          p == null ? (
            <span key={`gap${i}`} className="px-1 text-ink-3">
              …
            </span>
          ) : (
            <button key={p} onClick={() => go(p)} aria-current={p === page ? 'page' : undefined} className={`h-8 min-w-8 rounded-md px-2 tabular-nums ${p === page ? 'bg-accent font-semibold text-surface' : 'text-ink-2 hover:bg-sunken'}`}>
              {p}
            </button>
          ),
        )}
        <button className="btn px-2" disabled={page >= pages} onClick={() => go(page + 1)} title={hint('Next page', 'page.next')} aria-label="Next page">
          <ChevronRight size={15} />
        </button>
      </div>
      <form
        className="flex items-center gap-2 text-ink-2"
        onSubmit={(e) => {
          e.preventDefault();
          const p = Number(new FormData(e.currentTarget).get('page'));
          if (p >= 1 && p <= pages) go(p);
          e.currentTarget.reset();
        }}
      >
        <label htmlFor="jump-page">Go to page</label>
        <input id="jump-page" name="page" type="number" min={1} max={pages} placeholder={String(page)} className="field w-20" />
        <span>of {pages.toLocaleString()}</span>
      </form>
      <label className="flex items-center gap-2 text-ink-2">
        Per page
        <select className="field" value={size} onChange={(e) => setSearch({ size: Number(e.target.value), page: undefined })}>
          {PAGE_SIZES.map((n) => (
            <option key={n}>{n}</option>
          ))}
        </select>
      </label>
    </nav>
  );
}

/** Nothing saved at all: how to get the first items in. The steps are a real sequence, so they are numbered. */
function FirstRun() {
  const step = 'rounded-lg border border-line bg-surface px-4 py-3';
  return (
    <div className={`${COLUMN} space-y-3`}>
      <h2 className="text-xl font-semibold tracking-tight">Bring your saved posts in</h2>
      <p className="text-ink-2">Kosha fills from your own browser: the Kosha Capture extension copies what X and LinkedIn already show you.</p>
      <ol className="list-inside list-decimal space-y-2 marker:font-semibold marker:text-ink-3">
        <li className={step}>
          <span className="font-medium">Load the extension.</span>
          <span className="mt-1 block text-sm text-ink-2">
            Run <code className="rounded bg-sunken px-1">pnpm install && pnpm build</code> in <code className="rounded bg-sunken px-1">extension/</code>. In Chrome open <code className="rounded bg-sunken px-1">chrome://extensions</code>, turn on Developer mode, press “Load unpacked” and choose <code className="rounded bg-sunken px-1">extension/dist</code>.
          </span>
        </li>
        <li className={step}>
          <span className="font-medium">Point it at this server.</span>
          <span className="mt-1 block text-sm text-ink-2">
            In the extension’s popup enter this server’s address and an API token (
            <Link to="/settings" className="text-accent hover:underline">
              create one in Settings
            </Link>
            ), then press “Test connection”.
          </span>
        </li>
        <li className={step}>
          <span className="font-medium">Start a backfill.</span>
          <span className="mt-1 block text-sm text-ink-2">Press “Backfill” in the popup: it opens your X bookmarks and scrolls them to the end, slowly, then each folder. Keep that tab visible. For LinkedIn, press “Open saved posts”, then “Capture”. Items appear here as they arrive.</span>
        </li>
      </ol>
    </div>
  );
}

function Empty({ q, archived }: { q: string; archived: boolean }) {
  if (!q && !archived) return <FirstRun />;
  return (
    <div className={`${COLUMN} rounded-lg border border-dashed border-line px-6 py-12 text-center`}>
      <p className="font-semibold">{archived ? 'The Archive is empty' : 'Nothing matches this search'}</p>
      <p className="mt-1 text-sm text-ink-2">
        {archived ? 'Items you mark for deletion wait here until you remove or restore them.' : 'Remove a filter chip, or try fewer words.'}
      </p>
      {q && (
        <button className="btn mt-4" onClick={() => setSearch({ q: undefined, snap: undefined, jq: undefined, page: undefined })}>
          Clear the search
        </button>
      )}
    </div>
  );
}

/** True on first load and after Back/Forward: the cases where the saved position is put back.
 *  A link or a page button starts at the top instead. */
let restoreNext = true;
if (typeof window !== 'undefined') window.addEventListener('popstate', () => void (restoreNext = true));

export function Library({ archived = false }: { archived?: boolean }) {
  const search = useSearch({ strict: false }) as LibrarySearch;
  const s = withDefaults(search);
  const params = { q: s.q, mode: s.mode, sort: s.sort, page: s.page, size: s.size, archived, snap: s.snap };
  const query = useQuery({ queryKey: ['items', params], queryFn: () => fetchItems(params), placeholderData: keepPreviousData });
  const selecting = useStore((st) => st.selection.size > 0);
  const data = query.data;
  const fresh = data && !query.isPlaceholderData;

  // Everything that identifies this page of results; position is remembered per key.
  const positionKey = STORAGE_KEYS.position + JSON.stringify(params);
  const restored = useRef('');

  // Hand the page to the keyboard handlers, and put focus and scroll back where they were
  // (reload, Back). A page seen for the first time starts at the top.
  useLayoutEffect(() => {
    if (!fresh) return;
    const ids = data.items.map((i) => i.id);
    const patch = { pageIds: ids, pageItems: new Map(data.items.map((i) => [i.id, i])), total: data.total };
    const comingBack = restoreNext;
    restoreNext = false;
    if (restored.current === positionKey) return setState(patch);
    restored.current = positionKey;
    let saved: { focusId?: number; y?: number } = {};
    try {
      if (comingBack) saved = JSON.parse(sessionStorage.getItem(positionKey) ?? '{}');
    } catch {
      /* nothing saved */
    }
    setState({ ...patch, focusId: saved.focusId != null && ids.includes(saved.focusId) ? saved.focusId : null });
    window.scrollTo(0, saved.y ?? 0);
  }, [fresh, data, positionKey]);

  useEffect(() => {
    const save = () => {
      if (restored.current !== positionKey) return;
      try {
        sessionStorage.setItem(positionKey, JSON.stringify({ focusId: getState().focusId, y: window.scrollY }));
      } catch {
        /* storage full or blocked: position is simply not kept */
      }
    };
    window.addEventListener('scroll', save, { passive: true });
    window.addEventListener('pagehide', save);
    return () => {
      save();
      window.removeEventListener('scroll', save);
      window.removeEventListener('pagehide', save);
    };
  }, [positionKey]);
  useEffect(() => () => setState({ pageIds: [], pageItems: new Map(), total: 0, selection: new Set(), focusId: null }), []);

  const syntaxError = query.error instanceof ApiError && query.error.status === 422;
  return (
    <div className="px-3 pb-24 sm:px-5">
      <header data-sticky-header className="sticky top-0 z-20 -mx-3 space-y-1.5 border-b border-line bg-bg px-3 py-2 sm:-mx-5 sm:px-5">
        <div className="flex items-start gap-2">
          <button className="btn btn-ghost h-9 px-2 lg:hidden" aria-label="Open the sidebar" onClick={() => setState({ mobileNav: true })}>
            <Menu size={18} />
          </button>
          <SearchBar q={s.q} mode={s.mode} error={query.error} leftover={fresh ? data.leftover : []} />
        </div>
        <div className={selecting ? 'sm:hidden' : ''}>
          <Toolbar s={s} total={data?.total} />
        </div>
        {selecting && <BulkBar archived={archived} />}
        {!archived && <TagChips q={s.q} items={data?.items ?? []} />}
      </header>

      <main className="pt-3" aria-label={archived ? 'Archive' : 'Library'}>
        {archived && <ArchiveTools />}
        {fresh && data.notice && (
          <p role="status" className={`${COLUMN} mb-3 rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink-2`}>
            {data.notice}
          </p>
        )}
        {query.isPending ? (
          <Skeletons view={s.view} />
        ) : query.error && !syntaxError ? (
          <ErrorNote error={query.error} retry={() => void query.refetch()} />
        ) : !data || (syntaxError && !data.items.length) ? null : data.items.length === 0 ? (
          <Empty q={s.q || (s.snap ? 'jev' : '')} archived={archived} />
        ) : (
          // Dimmed while the next result loads; the old one stays in place so nothing jumps.
          <div className={`${LAYOUT[s.view]} ${query.isPlaceholderData ? 'opacity-60' : ''}`}>
            {data.items.map((item) => (
              <ItemCard key={item.id} item={item} view={s.view} />
            ))}
          </div>
        )}
        {data && data.pages > 1 && <Pagination page={s.page} pages={data.pages} size={s.size} view={s.view} />}
        {data && data.pages <= 1 && data.total > 25 && <Pagination page={1} pages={1} size={s.size} view={s.view} />}
      </main>
    </div>
  );
}
