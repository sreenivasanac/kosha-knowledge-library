/** App frame: the collapsible sidebar, the page, the reader drawer and the overlays. */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Outlet, useLocation, useNavigate, useSearch } from '@tanstack/react-router';
import { Archive, ChevronRight, CircleDot, HelpCircle, Inbox, LogOut, MessageCircleQuestion, PanelLeftClose, PanelLeftOpen, Pencil, RefreshCw, Search, Settings, Sparkles, Star, Tags, Terminal, X } from 'lucide-react';
import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { runAction } from './actions';
import { api, collectionsQuery, facetsQuery, flattenTags, invalidateLibrary, meQuery, setMe, statsQuery, tagsQuery, useCapabilities, useIsDemo, useLists, useSavedViews, type TagNode } from './api';
import { APP_NAME, DEMO_BANNER, REPO_URL, SYNC_POLL_MS, X_API_SYNC_SOURCE } from './constants';
import { ago, fullDate } from './format';
import { Drawer } from './Drawer';
import { KeyboardLayer } from './KeyboardLayer';
import { goQuery, hasDrawer, setSearch, validateLibrarySearch } from './nav';
import { Overlays, Toasts } from './Overlays';
import { quoteValue } from './query';
import { openOverlay, setSidebarOpen, setState, toast, toastError, useStore } from './store';
import { useHint } from './ui';

/** One sidebar row: label on the left, count on the right, highlighted when it is the current view. */
function Row({ label, count, active, onClick, icon, title, depth = 0, children }: { label: string; count?: number | null; active?: boolean; onClick: () => void; icon?: ReactNode; title?: string; depth?: number; children?: ReactNode }) {
  return (
    <div className={`group/row flex items-center rounded-md ${active ? 'bg-accent-soft font-semibold text-accent' : 'text-ink-2 hover:bg-sunken hover:text-ink'}`} style={{ paddingLeft: depth * 14 }}>
      {children}
      <button onClick={onClick} title={title} aria-current={active ? 'page' : undefined} className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1 text-left text-sm">
        {icon}
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {count != null && <span className="shrink-0 text-xs tabular-nums text-ink-3">{count.toLocaleString()}</span>}
      </button>
    </div>
  );
}

/** A titled group that folds away; open state is the browser's own (<details>). */
function Section({ title, children, action, defaultOpen = true }: { title: string; children: ReactNode; action?: ReactNode; defaultOpen?: boolean }) {
  return (
    <details open={defaultOpen} className="group/section">
      <summary className="flex list-none items-center gap-1 rounded px-2 py-1 text-xs font-semibold text-ink-3 hover:text-ink [&::-webkit-details-marker]:hidden">
        <ChevronRight size={12} aria-hidden className="transition-transform group-open/section:rotate-90" />
        <span className="flex-1">{title}</span>
        {action}
      </summary>
      <div className="mt-0.5 space-y-px">{children}</div>
    </details>
  );
}

function TagTree({ q }: { q: string }) {
  const tags = useQuery(tagsQuery);
  const navigate = useNavigate();
  const [filter, setFilter] = useState('');
  const [closed, setClosed] = useState<Set<number>>(new Set());
  const pick = (nodes: TagNode[], keep: (n: TagNode) => boolean): TagNode[] => nodes.filter(keep).map((n) => ({ ...n, children: pick(n.children, keep) }));
  const approved = pick(tags.data ?? [], (n) => n.status !== 'proposed' && n.status !== 'rejected');
  // Nothing approved yet: the proposed tree stands in (marked as such), so a freshly organised library can be browsed before the review.
  const proposedOnly = approved.length === 0;
  const tree = proposedOnly ? pick(tags.data ?? [], (n) => n.status !== 'rejected') : approved;
  const token = (path: string) => `#${quoteValue(path)}`;

  const node = (n: TagNode, path: string, depth: number): ReactNode => {
    const open = !closed.has(n.id);
    return (
      <div key={n.id}>
        <Row label={n.name} count={n.count} depth={depth} active={q === token(path)} onClick={() => goQuery(token(path))} title={n.description ?? path} icon={<span aria-hidden className="h-2 w-2 shrink-0 rounded-full" style={{ background: n.color ?? 'var(--ink-3)' }} />}>
          {n.children.length > 0 ? (
            <button aria-label={`${open ? 'Collapse' : 'Expand'} ${n.name}`} aria-expanded={open} className="ml-1 grid h-5 w-4 shrink-0 place-items-center text-ink-3" onClick={() => setClosed((c) => (c.has(n.id) ? new Set([...c].filter((id) => id !== n.id)) : new Set([...c, n.id])))}>
              <ChevronRight size={12} className={open ? 'rotate-90' : ''} />
            </button>
          ) : (
            <span className="ml-1 w-4 shrink-0" />
          )}
        </Row>
        {open && n.children.map((c) => node(c, `${path}/${c.name}`, depth + 1))}
      </div>
    );
  };
  const matches = filter ? flattenTags(tree).filter((t) => t.path.toLowerCase().includes(filter.toLowerCase())) : [];

  return (
    <Section
      title="Tags"
      action={
        <button className="rounded px-1 text-xs font-medium text-accent hover:underline" onClick={(e) => (e.preventDefault(), void navigate({ to: '/tags' }))}>
          Manage
        </button>
      }
    >
      {tags.isPending ? (
        <div className="skeleton mx-2 h-16" />
      ) : tree.length === 0 ? (
        <p className="px-2 py-1 text-sm text-ink-3">
          No tags yet. Press <kbd className="kbd">t</kbd> on an item to add one.
        </p>
      ) : (
        <div className={proposedOnly ? 'italic' : ''}>
          {proposedOnly && <p className="px-2 pb-1 text-xs not-italic text-ink-3">Proposed tags, not approved yet. Review them above.</p>}
          {flattenTags(tree).length > 8 && <input aria-label="Filter tags" placeholder="Filter tags" value={filter} onChange={(e) => setFilter(e.target.value)} className="field mx-2 mb-1 h-7 w-[calc(100%-1rem)]" />}
          {filter ? matches.length ? matches.map((t) => <Row key={t.id} label={t.path} count={t.node.count} active={q === token(t.path)} onClick={() => goQuery(token(t.path))} />) : <p className="px-2 text-sm text-ink-3">No tag matches.</p> : tree.map((n) => node(n, n.name, 0))}
        </div>
      )}
    </Section>
  );
}

/** One row of `GET /api/sync`: the last run of a sync. `state` is that run's summary (`status`, `pages`, `items_new`, `spent_usd`, `last_run_at`). */
export interface SyncRow {
  source: string;
  state: Record<string, unknown> | null;
  updated_at: string;
  running: boolean;
}
export const syncQuery = { queryKey: ['sync'], queryFn: () => api<SyncRow[]>('/api/sync'), refetchInterval: SYNC_POLL_MS };
export const xApiSync = (rows: SyncRow[] | undefined) => rows?.find((r) => r.source === X_API_SYNC_SOURCE);

/** Sidebar footer line: when the extension last delivered, and the X API sync's state when the server has one. */
function SyncStatus({ lastCapture }: { lastCapture: string | null }) {
  const caps = useCapabilities();
  const sync = xApiSync(useQuery({ ...syncQuery, enabled: caps.sync }).data);
  const running = !!sync?.running;
  const lastSync = sync?.updated_at;
  const failed = sync?.state?.status === 'parse_error';
  return (
    <p role="status" className="flex items-center gap-1.5 border-t border-line px-3 py-1.5 text-xs text-ink-3" title={lastCapture ? `Last capture from the extension: ${fullDate(lastCapture)}` : undefined}>
      <RefreshCw size={11} aria-hidden className={running ? 'animate-spin' : ''} />
      <span className="min-w-0 flex-1 truncate">
        {running ? 'Syncing from the X API…' : failed ? 'The last API sync could not be read' : lastCapture ? `Last capture ${ago(lastCapture)}` : 'Nothing captured yet'}
        {!running && !failed && lastSync && ` · API sync ${ago(lastSync)}`}
      </span>
    </p>
  );
}

function Sidebar({ rail }: { rail: boolean }) {
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const search = validateLibrarySearch(useSearch({ strict: false }) as Record<string, unknown>);
  const q = location.pathname === '/' ? (search.q ?? '') : null;
  const stats = useQuery(statsQuery).data ?? {};
  const facets = useQuery(facetsQuery).data;
  const collections = useQuery(collectionsQuery).data ?? [];
  const allTags = useQuery(tagsQuery).data;
  const caps = useCapabilities();
  const views = useSavedViews();
  const lists = useLists();
  const hint = useHint();
  const [folderFilter, setFolderFilter] = useState('');
  const me = useQuery(meQuery).data;
  const n = (key: string) => (typeof stats[key] === 'number' ? (stats[key] as number) : null);
  const go = (to: string) => () => void navigate({ to });

  const main: { label: string; icon: ReactNode; count: number | null; active: boolean; action: string }[] = [
    { label: 'All', icon: <Inbox size={15} />, count: n('items'), active: q === '', action: 'go.all' },
    { label: 'Unread', icon: <CircleDot size={15} />, count: n('unread'), active: q === 'is:unread', action: 'go.unread' },
    { label: 'Starred', icon: <Star size={15} />, count: n('starred'), active: q === 'is:starred', action: 'go.starred' },
    { label: 'Untagged', icon: <Tags size={15} />, count: n('untagged'), active: q === 'is:untagged', action: 'go.untagged' },
    { label: 'Proposed tags', icon: <Sparkles size={15} />, count: allTags ? flattenTags(allTags).filter((t) => t.node.status === 'proposed').length : null, active: location.pathname === '/proposed', action: 'go.proposed' },
    { label: 'Archive', icon: <Archive size={15} />, count: n('archived'), active: location.pathname === '/archive', action: 'go.archive' },
    { label: 'Ask', icon: <MessageCircleQuestion size={15} />, count: null, active: location.pathname === '/ask', action: 'go.ask' },
    { label: 'Pipeline console', icon: <Terminal size={15} />, count: null, active: location.pathname === '/console', action: 'go.console' },
  ];

  // A counted view with nothing in it is left out (it comes back when it has items or is the one open). All and Untagged always show.
  const shown = (m: (typeof main)[number]) => m.count !== 0 || m.active || m.label === 'All' || m.label === 'Untagged';

  async function signOut() {
    try {
      await api('/api/auth/logout', { method: 'POST' });
    } catch (error) {
      // Still signed in on the server: stay here, so the screen does not claim otherwise. Sign out can be pressed again.
      return toastError(error);
    }
    setMe(null);
    client.clear();
    // The router decides what a visitor sees at `/`: the landing page or the sign-in form.
    void navigate({ to: '/' });
  }

  if (rail)
    return (
      <nav aria-label="Views" className="flex h-full flex-col items-center gap-1 py-2">
        <button className="btn btn-ghost h-9 w-9 justify-center px-0" aria-label="Expand the sidebar" title="Expand the sidebar" onClick={() => setSidebarOpen(true)}>
          <PanelLeftOpen size={17} />
        </button>
        {main.map((m) => (
          <button key={m.label} className={`btn btn-ghost h-9 w-9 justify-center px-0 ${m.active ? 'bg-accent-soft text-accent' : ''}`} aria-label={m.label} title={hint(m.label, m.action)} onClick={() => runAction(m.action)}>
            {m.icon}
          </button>
        ))}
        <span className="flex-1" />
        <button className="btn btn-ghost h-9 w-9 justify-center px-0" aria-label="Settings" title="Settings" onClick={go('/settings')}>
          <Settings size={16} />
        </button>
      </nav>
    );

  const shownFolders = collections.filter((c) => c.name.toLowerCase().includes(folderFilter.toLowerCase()));
  return (
    <nav aria-label="Library" className="flex h-full flex-col">
      <div className="flex items-center gap-2 px-3 pb-1 pt-3">
        <span aria-hidden className="grid h-6 w-6 place-items-center rounded-md bg-accent">
          <span className="h-3.5 w-2 bg-mark [clip-path:polygon(0_0,100%_0,100%_100%,50%_72%,0_100%)]" />
        </span>
        <span className="flex-1 text-lg font-semibold tracking-tight">{APP_NAME}</span>
        <button className="btn btn-ghost h-8 w-8 justify-center px-0 max-lg:hidden" aria-label="Collapse the sidebar" title="Collapse the sidebar" onClick={() => setSidebarOpen(false)}>
          <PanelLeftClose size={16} />
        </button>
        <button className="btn btn-ghost h-8 w-8 justify-center px-0 lg:hidden" aria-label="Close the sidebar" onClick={() => setState({ mobileNav: false })}>
          <X size={16} />
        </button>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto px-2 py-2">
        <button onClick={() => runAction('search.focus')} className="flex h-8 w-full items-center gap-2 rounded-md border border-line bg-surface px-2 text-sm text-ink-3 hover:border-ink-3">
          <Search size={14} aria-hidden />
          <span className="flex-1 text-left">Search</span>
          <kbd className="kbd">/</kbd>
        </button>
        <div className="space-y-px">
          {main.filter(shown).map((m) => (
            <Row key={m.label} label={m.label} icon={m.icon} count={m.count} active={m.active} title={hint(m.label, m.action)} onClick={() => runAction(m.action)} />
          ))}
        </div>
        <TagTree q={q ?? ''} />
        <Section title="Saved views">
          {views.length === 0 && <p className="px-2 py-1 text-sm text-ink-3">Save a search to keep it here.</p>}
          {views.map((v) => (
            <Row key={v.id} label={v.name} title={v.query} active={q === v.query && q !== ''} onClick={() => void navigate({ to: '/', search: { q: v.query || undefined, ...(v.params ?? {}) } })}>
              <button
                aria-label={`Delete the view ${v.name}`}
                className="order-last mr-1 grid h-5 w-5 place-items-center rounded text-ink-3 opacity-0 hover:text-danger focus-visible:opacity-100 group-hover/row:opacity-100"
                onClick={async () => {
                  await api(`/api/saved-views/${v.id}`, { method: 'DELETE' }).catch(toastError);
                  void client.invalidateQueries({ queryKey: ['saved-views'] });
                }}
              >
                <X size={12} />
              </button>
            </Row>
          ))}
        </Section>
        <Section title="Sources">
          {facets?.sources.filter((f) => f.count > 0).map((f) => (
            <Row key={f.value} label={f.value === 'x' ? 'X' : f.value} count={f.count} active={q === `source:${f.value}`} onClick={() => goQuery(`source:${f.value}`)} />
          ))}
        </Section>
        <Section title="Years">
          {facets?.years.filter((f) => f.count > 0).map((f) => (
            <Row key={f.value} label={f.value} count={f.count} active={q === `saved:${f.value}`} onClick={() => goQuery(`saved:${f.value}`)} />
          ))}
        </Section>
        <Section title="Folders" defaultOpen={collections.length <= 12}>
          {collections.length > 8 && <input aria-label="Filter folders" placeholder="Filter folders" value={folderFilter} onChange={(e) => setFolderFilter(e.target.value)} className="field mx-2 mb-1 h-7 w-[calc(100%-1rem)]" />}
          {collections.length === 0 && <p className="px-2 py-1 text-sm text-ink-3">No folders from your sources.</p>}
          {shownFolders.filter((c) => c.count > 0).map((c) => (
            <Row key={c.id} label={c.name} count={c.count} active={q === `folder:${quoteValue(c.name)}`} onClick={() => goQuery(`folder:${quoteValue(c.name)}`)} />
          ))}
        </Section>
        <Section title="Lists">
          {lists.length === 0 && (
            <p className="px-2 py-1 text-sm text-ink-3">
              No lists yet. Press <kbd className="kbd">l</kbd> on an item.
            </p>
          )}
          {lists.map((l) => (
            <Row key={l.id} label={l.name} count={l.count} active={q === `list:${quoteValue(l.name)}`} onClick={() => goQuery(`list:${quoteValue(l.name)}`)}>
              {caps.listEdit && (
                <span className="order-last mr-1 flex opacity-0 focus-within:opacity-100 group-hover/row:opacity-100">
                  <button aria-label={`Rename the list ${l.name}`} title="Rename" className="grid h-5 w-5 place-items-center rounded text-ink-3 hover:text-ink" onClick={() => openOverlay({ kind: 'renameList', id: l.id, name: l.name })}>
                    <Pencil size={11} />
                  </button>
                  <button
                    aria-label={`Delete the list ${l.name}`}
                    title="Delete"
                    className="grid h-5 w-5 place-items-center rounded text-ink-3 hover:text-danger"
                    onClick={() =>
                      openOverlay({
                        kind: 'confirm',
                        danger: true,
                        title: `Delete the list ${l.name}?`,
                        body: 'The list goes; its items stay in your library.',
                        action: 'Delete list',
                        run: () => api(`/api/lists/${l.id}`, { method: 'DELETE' }).then(() => (toast(`Deleted the list ${l.name}`), invalidateLibrary()), toastError),
                      })
                    }
                  >
                    <X size={12} />
                  </button>
                </span>
              )}
            </Row>
          ))}
        </Section>
      </div>
      <SyncStatus lastCapture={typeof stats.last_capture_at === 'string' ? stats.last_capture_at : null} />
      <div className="flex items-center gap-1 border-t border-line px-2 py-1.5">
        <button className="btn btn-ghost px-2" onClick={go('/settings')} aria-current={location.pathname === '/settings' ? 'page' : undefined}>
          <Settings size={15} /> Settings
        </button>
        <button className="btn btn-ghost px-2" title={hint('Shortcuts', 'help')} aria-label="Keyboard shortcuts" onClick={() => runAction('help')}>
          <HelpCircle size={15} />
        </button>
        <span className="flex-1" />
        <button className="btn btn-ghost px-2" title={`Sign out ${me?.email ?? ''}`} aria-label="Sign out" onClick={signOut}>
          <LogOut size={15} />
        </button>
      </div>
    </nav>
  );
}

/** Tailwind's `lg`: from here up the sidebar is a fixed column; below it is a drawer. */
const DESKTOP = window.matchMedia('(min-width: 64rem)');

export function Shell() {
  const sidebarOpen = useStore((s) => s.sidebarOpen);
  const mobileNav = useStore((s) => s.mobileNav);
  const pending = useStore((s) => s.pendingKeys);
  const desktop = useSyncExternalStore(
    (notify) => (DESKTOP.addEventListener('change', notify), () => DESKTOP.removeEventListener('change', notify)),
    () => DESKTOP.matches,
  );
  const location = useLocation();
  const search = validateLibrarySearch(useSearch({ strict: false }) as Record<string, unknown>);
  const demo = useIsDemo();
  const drawerItem = hasDrawer(location.pathname) ? search.item : undefined;

  // A phone's sidebar drawer closes when a view is chosen.
  useEffect(() => setState({ mobileNav: false }), [location.href]);

  return (
    <div className="min-h-dvh">
      <a href="#page" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2">
        Skip to the content
      </a>
      {/* Desktop: fixed column (full or a rail). Phone: a drawer over the page. */}
      <aside className={`fixed inset-y-0 left-0 z-40 border-r border-line bg-bg max-lg:w-72 max-lg:shadow-pop max-lg:transition-transform ${mobileNav ? '' : 'max-lg:-translate-x-full'} ${sidebarOpen ? 'lg:w-64' : 'lg:w-12'}`}>
        <Sidebar rail={desktop && !sidebarOpen} />
      </aside>
      {mobileNav && <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={() => setState({ mobileNav: false })} />}
      {/* On a wide screen the page makes room for the drawer, so the reading column recentres without changing width (nothing reflows). */}
      <div id="page" className={`${sidebarOpen ? 'lg:pl-64' : 'lg:pl-12'} ${drawerItem != null ? 'min-[1400px]:pr-(--drawer-w)' : ''}`}>
        {demo && (
          <p role="note" className="flex flex-wrap items-center justify-center gap-x-2 border-b border-line bg-accent-soft px-3 py-1 text-center text-xs">
            {DEMO_BANNER}
            <a className="font-semibold text-accent hover:underline" href={REPO_URL}>
              Run your own
            </a>
          </p>
        )}
        <Outlet />
      </div>
      {drawerItem != null && <Drawer id={drawerItem} onClose={() => setSearch({ item: undefined })} />}
      <Overlays />
      <Toasts />
      <KeyboardLayer />
      {pending && (
        <div aria-live="polite" className="fixed bottom-3 right-3 z-50 rounded-md border border-line bg-surface px-2 py-1 text-sm shadow-pop">
          <kbd className="kbd">{pending}</kbd> <span className="text-ink-3">…</span>
        </div>
      )}
    </div>
  );
}
