/** Tag management (tree, rename, describe, move, merge, colour, delete) and the Proposed tags review. */
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Check, ChevronLeft, ChevronRight, Menu, X } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { focusItem, setPageHandlers } from './actions';
import { api, flattenTags, invalidateLibrary, tagsQuery, type FlatTag, type ItemPage, type TagNode } from './api';
import { DOUBTFUL_BELOW, TAG_COLORS } from './constants';
import { shortDate } from './format';
import { AgentBadge } from './ItemCard';
import { goQuery } from './nav';
import { quoteValue } from './query';
import { getState, openOverlay, setState, toast, toastError, useStore } from './store';
import { ErrorNote, Keys, useBindings } from './ui';

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="mb-4 flex items-center gap-2">
      <button className="btn btn-ghost h-9 px-2 lg:hidden" aria-label="Open the sidebar" onClick={() => setState({ mobileNav: true })}>
        <Menu size={18} />
      </button>
      <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
      <span className="flex-1" />
      {children}
    </header>
  );
}

/** Runs a tag change, then refreshes everything that shows tags. */
async function change(work: () => Promise<unknown>, done: string) {
  try {
    await work();
    toast(done);
  } catch (error) {
    toastError(error);
  }
  invalidateLibrary();
}

function TagEditor({ tag, all }: { tag: FlatTag; all: FlatTag[] }) {
  const [name, setName] = useState(tag.node.name);
  const [description, setDescription] = useState(tag.node.description ?? '');
  // A tag cannot move under itself or its own descendants, nor merge into itself.
  const outside = all.filter((t) => t.id !== tag.id && !t.path.startsWith(tag.path + '/'));
  const patch = (body: Record<string, unknown>, done: string) => change(() => api(`/api/tags/${tag.id}`, { method: 'PATCH', body }), done);
  const row = 'grid gap-1.5 text-sm font-medium';

  return (
    <section aria-label={`Edit ${tag.path}`} className="space-y-4 rounded-lg border border-line bg-surface p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="min-w-0 truncate font-semibold">{tag.path.split('/').join(' › ')}</h2>
        <button className="shrink-0 text-sm text-accent hover:underline" onClick={() => goQuery(`#${quoteValue(tag.path)}`)}>
          Show its {tag.node.count.toLocaleString()} items
        </button>
      </div>
      <form className={row} onSubmit={(e) => (e.preventDefault(), name.trim() && name !== tag.node.name && void patch({ name: name.trim() }, 'Renamed'))}>
        <label htmlFor="tag-name">Name</label>
        <div className="flex gap-2">
          <input id="tag-name" className="field flex-1" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn" disabled={!name.trim() || name === tag.node.name}>
            Rename
          </button>
        </div>
      </form>
      <form className={row} onSubmit={(e) => (e.preventDefault(), void patch({ description: description.trim() || null }, 'Description saved'))}>
        <label htmlFor="tag-description">Description</label>
        <textarea id="tag-description" rows={2} className="field" placeholder="What belongs under this tag. Jev checks items against this definition." value={description} onChange={(e) => setDescription(e.target.value)} />
        <button className="btn justify-self-start" disabled={description.trim() === (tag.node.description ?? '')}>
          Save description
        </button>
      </form>
      <div className={row}>
        <span id="tag-colour">Colour</span>
        <div role="radiogroup" aria-labelledby="tag-colour" className="flex gap-2">
          {TAG_COLORS.map((c) => (
            <button key={c} role="radio" aria-checked={tag.node.color === c} aria-label={c} onClick={() => void patch({ color: c }, 'Colour changed')} className={`h-6 w-6 rounded-full ${tag.node.color === c ? 'ring-2 ring-ink ring-offset-2 ring-offset-surface' : ''}`} style={{ background: c }} />
          ))}
        </div>
      </div>
      <label className={row}>
        Move under
        <select
          className="field"
          value={tag.node.parent_id ?? ''}
          onChange={(e) => void change(() => api(`/api/tags/${tag.id}/move`, { method: 'POST', body: { parent_id: e.target.value ? Number(e.target.value) : null } }), 'Moved')}
        >
          <option value="">Top level</option>
          {outside.map((t) => (
            <option key={t.id} value={t.id}>
              {t.path}
            </option>
          ))}
        </select>
      </label>
      <label className={row}>
        Merge into
        <select
          className="field"
          value=""
          onChange={(e) => {
            const into = outside.find((t) => t.id === Number(e.target.value));
            if (into) openOverlay({ kind: 'confirm', title: `Merge #${tag.path} into #${into.path}?`, body: `Every item tagged #${tag.path} (${tag.node.count}) gets #${into.path} instead, and #${tag.path} is removed.`, action: 'Merge', run: () => change(() => api(`/api/tags/${tag.id}/merge`, { method: 'POST', body: { into_id: into.id } }), 'Merged') });
          }}
        >
          <option value="">Choose a tag…</option>
          {outside.map((t) => (
            <option key={t.id} value={t.id}>
              {t.path}
            </option>
          ))}
        </select>
      </label>
      <button
        className="btn btn-danger"
        onClick={() =>
          openOverlay({
            kind: 'confirm',
            danger: true,
            title: `Delete #${tag.path}?`,
            body: `${tag.node.children.length ? 'Its child tags are deleted too. ' : ''}Items keep everything else; they only lose this tag (${tag.node.count} tagged now).`,
            action: 'Delete tag',
            run: () => change(() => api(`/api/tags/${tag.id}?recursive=${tag.node.children.length > 0}`, { method: 'DELETE' }), 'Deleted'),
          })
        }
      >
        Delete tag
      </button>
    </section>
  );
}

export function TagsPage() {
  const query = useQuery(tagsQuery);
  const all = flattenTags(query.data ?? []).filter((t) => t.node.status === 'approved');
  const [selected, setSelected] = useState<number | null>(null);
  const [filter, setFilter] = useState('');
  const [newName, setNewName] = useState('');
  const [newParent, setNewParent] = useState('');
  const tag = all.find((t) => t.id === selected);
  const shown = all.filter((t) => t.path.toLowerCase().includes(filter.toLowerCase()));

  return (
    <div className="px-3 py-4 sm:px-5">
      <PageHeader title="Tags" />
      <form
        className="mb-4 flex max-w-3xl flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void change(() => api('/api/tags', { method: 'POST', body: { name: newName.trim(), parent_id: newParent ? Number(newParent) : null } }), `Created #${newName.trim()}`);
          setNewName('');
        }}
      >
        <label className="grid gap-1 text-sm font-medium">
          New tag
          <input className="field w-56" placeholder="Name" value={newName} onChange={(e) => setNewName(e.target.value)} />
        </label>
        <label className="grid gap-1 text-sm font-medium">
          Under
          <select className="field max-w-56" value={newParent} onChange={(e) => setNewParent(e.target.value)}>
            <option value="">Top level</option>
            {all.map((t) => (
              <option key={t.id} value={t.id}>
                {t.path}
              </option>
            ))}
          </select>
        </label>
        <button className="btn btn-primary" disabled={!newName.trim()}>
          Create tag
        </button>
      </form>

      {query.isPending ? (
        <div className="skeleton h-64 max-w-md" aria-busy="true" />
      ) : query.error ? (
        <ErrorNote error={query.error} retry={() => void query.refetch()} />
      ) : all.length === 0 ? (
        <p className="max-w-md rounded-lg border border-dashed border-line px-6 py-10 text-center text-sm text-ink-2">No tags yet. Create one above, or press t on an item in the library.</p>
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,26rem)_minmax(0,30rem)]">
          <div className="rounded-lg border border-line bg-surface">
            <input aria-label="Filter tags" placeholder="Filter tags" className="field m-2 w-[calc(100%-1rem)]" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <ul aria-label="Tag tree" className="max-h-[70vh] overflow-y-auto pb-2">
              {shown.map((t) => (
                <li key={t.id}>
                  <button aria-current={t.id === selected || undefined} onClick={() => setSelected(t.id)} title={t.node.description ?? undefined} className={`flex w-full items-center gap-2 py-1 pr-3 text-left text-sm ${t.id === selected ? 'bg-accent-soft font-semibold text-accent' : 'hover:bg-sunken'}`} style={{ paddingLeft: 12 + (filter ? 0 : t.depth * 18) }}>
                    <span aria-hidden className="h-2 w-2 shrink-0 rounded-full" style={{ background: t.node.color ?? 'var(--ink-3)' }} />
                    <span className="min-w-0 flex-1 truncate">{filter ? t.path : t.node.name}</span>
                    <span className="text-xs tabular-nums text-ink-3">{t.node.count.toLocaleString()}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
          {tag ? <TagEditor key={`${tag.id}:${tag.node.name}:${tag.node.description ?? ''}`} tag={tag} all={all} /> : <p className="rounded-lg border border-dashed border-line px-6 py-10 text-center text-sm text-ink-2">Choose a tag to rename, describe, move, merge, colour or delete it.</p>}
        </div>
      )}
    </div>
  );
}

/** The part of the tree under review: proposed tags, plus the approved tags above them as context. */
function reviewTree(nodes: TagNode[]): TagNode[] {
  return nodes.flatMap((n) => {
    const children = reviewTree(n.children);
    return n.status === 'proposed' || children.length ? [{ ...n, children }] : [];
  });
}
const proposedIn = (t: FlatTag): FlatTag[] => flattenTags([t.node], t.path.includes('/') ? t.path.slice(0, t.path.lastIndexOf('/') + 1) : '').filter((d) => d.node.status === 'proposed');

const SAMPLE_PAGE_SIZE = 25;

/** Items carrying the focused tag, a page at a time, each with the confidence the agent gave it. */
function Samples({ tag, doubtful, page, setPage }: { tag: FlatTag; doubtful: boolean; page: number; setPage: (p: number) => void }) {
  const q = `#${quoteValue(tag.path)}${doubtful ? ` conf:<${DOUBTFUL_BELOW}` : ''}`;
  const samples = useQuery({ queryKey: ['items', 'review', q, page], queryFn: () => api<ItemPage>(`/api/items?${new URLSearchParams({ q, page: String(page), page_size: String(SAMPLE_PAGE_SIZE) })}`), placeholderData: keepPreviousData });
  const [open, setOpen] = useState<number | null>(null);
  const data = samples.data;
  // `]` can step past the end; come back to the last page there is.
  const lastPage = data ? Math.max(1, data.pages) : null;
  useEffect(() => {
    if (lastPage != null && page > lastPage) setPage(lastPage);
    // `setPage` is a new function each render; the page numbers are what matter.
  }, [lastPage, page]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div>
      <div className="flex items-center gap-2 border-b border-line px-4 py-1.5 text-sm text-ink-2">
        <span className="flex-1 tabular-nums" aria-live="polite">
          {data ? `${data.total.toLocaleString()} ${doubtful ? `doubtful item${data.total === 1 ? '' : 's'} (confidence below ${DOUBTFUL_BELOW})` : `item${data.total === 1 ? '' : 's'}`}` : ' '}
        </span>
        {data && data.pages > 1 && (
          <>
            <button className="btn h-7 px-1.5" aria-label="Previous samples" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              <ChevronLeft size={14} />
            </button>
            <span className="tabular-nums">
              {page} / {data.pages}
            </span>
            <button className="btn h-7 px-1.5" aria-label="More samples" disabled={page >= data.pages} onClick={() => setPage(page + 1)}>
              <ChevronRight size={14} />
            </button>
          </>
        )}
      </div>
      {samples.error ? (
        <div className="p-3">
          <ErrorNote error={samples.error} retry={() => void samples.refetch()} />
        </div>
      ) : !data ? (
        <div className="skeleton m-3 h-40" aria-busy="true" />
      ) : data.items.length === 0 ? (
        <p className="px-4 py-6 text-sm text-ink-2">{doubtful ? 'No doubtful items: every item here was attached with high confidence, or by hand.' : 'No items carry this tag yet.'}</p>
      ) : (
        <ul aria-label="Sample items" className={samples.isPlaceholderData ? 'opacity-60' : ''}>
          {data.items.map((i) => {
            const own = i.tags.find((t) => t.tag_id === tag.id);
            return (
              <li key={i.id} className="border-b border-line last:border-b-0">
                {/* A click opens the whole text in place; the reader is one more click away. */}
                <button className="block w-full px-4 py-2 text-left hover:bg-sunken/60" aria-expanded={open === i.id} onClick={() => setOpen(open === i.id ? null : i.id)}>
                  <span className="flex items-center gap-2 text-sm">
                    <span className="truncate font-medium">{i.author.name ?? i.author.handle ?? 'Unknown author'}</span>
                    <span className="shrink-0 text-ink-3">{shortDate(i.posted_at)}</span>
                    <span className="flex-1" />
                    {own && own.attached_by !== 'human' && own.attached_by !== 'import' ? <AgentBadge confidence={own.confidence} /> : own ? <span className="text-xs text-ink-3">by hand</span> : null}
                  </span>
                  {/* Closed: two lines of running text (blank lines would waste them). Open: the text as written. */}
                  <span className={`mt-0.5 break-words text-sm text-ink-2 ${open === i.id ? 'block whitespace-pre-wrap' : 'line-clamp-2'}`}>{open === i.id ? `${i.title ? `${i.title}\n` : ''}${i.body ?? i.summary ?? ''}` : `${i.title ?? ''} ${i.body ?? i.summary ?? ''}`.replace(/\s+/g, ' ').trim()}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * Proposed tags review. Left: the proposed tree with counts and definitions,
 * walked with j/k. Right: the focused tag, its samples and the decisions.
 * `a` / `r` decide one tag, Shift+A / Shift+X a whole subtree.
 */
export function ProposedPage() {
  // Asked again on opening: proposals come from organising runs outside this app.
  const query = useQuery({ ...tagsQuery, staleTime: 0 });
  const rows = flattenTags(reviewTree(query.data ?? []));
  const proposedCount = rows.filter((t) => t.node.status === 'proposed').length;
  const focusId = useStore((s) => s.focusId);
  const bindings = useBindings();
  const ids = rows.map((t) => t.id).join(',');
  const focused = rows.find((t) => t.id === focusId);
  const [doubtful, setDoubtful] = useState(false);
  // The sample page belongs to one tag and filter; another tag starts at page 1 again.
  const pageKey = `${focusId}:${doubtful}`;
  const [paging, setPaging] = useState({ key: pageKey, page: 1 });
  const page = paging.key === pageKey ? paging.page : 1;
  const setPage = (p: number) => setPaging({ key: pageKey, page: p });

  // The same j/k as the library walk these rows.
  useEffect(() => {
    const list = ids ? ids.split(',').map(Number) : [];
    const current = getState().focusId;
    setState({ pageIds: list, pageItems: new Map(), total: list.length, focusId: list.includes(current ?? -1) ? current : (list[0] ?? null) });
  }, [ids]);
  useEffect(() => () => setState({ pageIds: [], focusId: null, total: 0 }), []);

  /** Moves the ribbon off rows that are about to leave the tree. */
  function focusAfter(leaving: Set<number>) {
    const at = rows.findIndex((t) => t.id === focusId);
    const next = rows.slice(at + 1).find((t) => !leaving.has(t.id)) ?? rows.slice(0, Math.max(at, 0)).reverse().find((t) => !leaving.has(t.id));
    focusItem(next?.id ?? null);
  }
  function decide(verb: 'approve' | 'reject', tag: FlatTag | undefined) {
    if (!tag) return;
    if (tag.node.status !== 'proposed') return toast(`#${tag.path} is already approved. Use “all under” to decide the proposed tags beneath it.`);
    // An approved tag with proposed children stays in the tree as their context.
    if (verb === 'reject' || !tag.node.children.length) focusAfter(new Set(flattenTags([tag.node]).map((t) => t.id)));
    void change(() => api(`/api/tags/${tag.id}/${verb}`, { method: 'POST' }), `${verb === 'approve' ? 'Approved' : 'Rejected'} #${tag.path}`);
  }
  /** One confirmation for the whole subtree, however many tags and items it holds. */
  function decideTree(verb: 'approve' | 'reject', tag: FlatTag | undefined) {
    if (!tag) return;
    const targets = proposedIn(tag);
    if (!targets.length) return;
    if (targets.length === 1) return decide(verb, targets[0]);
    openOverlay({
      kind: 'confirm',
      danger: verb === 'reject',
      title: `${verb === 'approve' ? 'Approve' : 'Reject'} ${targets.length} proposed tags under #${tag.path}?`,
      body: `${verb === 'approve' ? 'They become part of your tag tree' : 'They are marked rejected, so they are not proposed again'}: ${targets.slice(0, 6).map((t) => t.node.name).join(', ')}${targets.length > 6 ? ` and ${targets.length - 6} more` : ''}. Together they are on ${tag.node.count.toLocaleString()} items.`,
      action: `${verb === 'approve' ? 'Approve' : 'Reject'} ${targets.length} tags`,
      run: () => {
        focusAfter(new Set(flattenTags([tag.node]).map((t) => t.id)));
        return change(async () => {
          // ponytail: one request per tag; add a subtree endpoint if trees reach hundreds of proposed tags.
          for (const t of targets) await api(`/api/tags/${t.id}/${verb}`, { method: 'POST' });
        }, `${verb === 'approve' ? 'Approved' : 'Rejected'} ${targets.length} tags under #${tag.path}`);
      },
    });
  }
  // Re-registered every render so the keys always act on the row focused now.
  useEffect(() => {
    setPageHandlers({
      'proposed.approve': () => decide('approve', focused),
      'proposed.reject': () => decide('reject', focused),
      'proposed.approveTree': () => decideTree('approve', focused),
      'proposed.rejectTree': () => decideTree('reject', focused),
      'proposed.doubtful': () => setDoubtful((d) => !d),
      'page.next': () => setPage(page + 1),
      'page.prev': () => setPage(Math.max(1, page - 1)),
    });
    return () => setPageHandlers({});
  });

  const under = focused ? proposedIn(focused).length : 0;
  return (
    <div className="px-3 py-4 sm:px-5">
      <PageHeader title="Proposed tags">
        {proposedCount > 0 && <span className="text-sm tabular-nums text-ink-2">{proposedCount.toLocaleString()} to review</span>}
      </PageHeader>
      <p className="mb-4 max-w-3xl text-sm text-ink-2">
        Tags an organising run suggested, with the items it put under each. <Keys keys={bindings['move.next']} /> <Keys keys={bindings['move.prev']} /> move, <Keys keys={bindings['proposed.approve']} /> approves, <Keys keys={bindings['proposed.reject']} /> rejects, <Keys keys={bindings['proposed.approveTree']} /> / <Keys keys={bindings['proposed.rejectTree']} /> decide a tag with everything under it, <Keys keys={bindings['proposed.doubtful']} /> shows only doubtful items, <Keys keys={bindings['page.prev']} /> <Keys keys={bindings['page.next']} /> page the samples.
      </p>
      {query.isPending ? (
        <div className="skeleton h-48 max-w-2xl" aria-busy="true" />
      ) : query.error ? (
        <ErrorNote error={query.error} retry={() => void query.refetch()} />
      ) : rows.length === 0 ? (
        <p className="max-w-2xl rounded-lg border border-dashed border-line px-6 py-10 text-center text-sm text-ink-2">Nothing to review. Proposed tags appear here after an organising run.</p>
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
          <ul aria-label="Proposed tag tree" data-own-scroll className="overflow-y-auto rounded-lg border border-line bg-surface lg:sticky lg:top-[calc(1rem+var(--banner-h,0px))] lg:max-h-[calc(100dvh-11rem-var(--banner-h,0px))]">
            {rows.map((t) => {
              const on = t.id === focusId;
              const proposed = t.node.status === 'proposed';
              return (
                <li key={t.id} data-focus-id={t.id} aria-current={on || undefined} onClick={() => setState({ focusId: t.id })} className={`group flex cursor-pointer items-center gap-2 border-b border-line py-1.5 pr-2 last:border-b-0 ${on ? 'focused' : 'hover:bg-sunken/60'}`} style={{ paddingLeft: 12 + t.depth * 16 }}>
                  <span className="min-w-0 flex-1">
                    <span className={`block truncate text-sm ${proposed ? 'font-semibold italic' : 'text-ink-3'}`}>
                      {t.node.name}
                      {!proposed && <span className="ml-1.5 text-xs not-italic">approved</span>}
                    </span>
                    {t.node.description && <span className="block truncate text-xs text-ink-3">{t.node.description}</span>}
                  </span>
                  <span className="shrink-0 text-xs tabular-nums text-ink-3">{t.node.count.toLocaleString()}</span>
                  {proposed && (
                    <span className={`flex shrink-0 ${on ? '' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'}`}>
                      <button className="grid h-6 w-6 place-items-center rounded hover:bg-sunken" aria-label={`Approve ${t.path}`} onClick={(e) => (e.stopPropagation(), decide('approve', t))}>
                        <Check size={14} className="text-ok" />
                      </button>
                      <button className="grid h-6 w-6 place-items-center rounded hover:bg-sunken" aria-label={`Reject ${t.path}`} onClick={(e) => (e.stopPropagation(), decide('reject', t))}>
                        <X size={14} className="text-danger" />
                      </button>
                    </span>
                  )}
                </li>
              );
            })}
          </ul>

          {focused && (
            <section aria-label={`Review ${focused.path}`} className="overflow-hidden rounded-lg border border-line bg-surface">
              <div className="space-y-2 border-b border-line px-4 py-3">
                <h2 className="font-semibold">{focused.path.split('/').join(' › ')}</h2>
                <p className="text-sm text-ink-2">{focused.node.description ?? 'No definition was written for this tag.'}</p>
                <div className="flex flex-wrap items-center gap-2">
                  {focused.node.status === 'proposed' && (
                    <>
                      <button className="btn" onClick={() => decide('approve', focused)}>
                        <Check size={15} className="text-ok" /> Approve <Keys keys={bindings['proposed.approve']} />
                      </button>
                      <button className="btn" onClick={() => decide('reject', focused)}>
                        <X size={15} className="text-danger" /> Reject <Keys keys={bindings['proposed.reject']} />
                      </button>
                    </>
                  )}
                  {under > 1 && (
                    <>
                      <button className="btn" onClick={() => decideTree('approve', focused)}>
                        Approve all {under} under it <Keys keys={bindings['proposed.approveTree']} />
                      </button>
                      <button className="btn" onClick={() => decideTree('reject', focused)}>
                        Reject all <Keys keys={bindings['proposed.rejectTree']} />
                      </button>
                    </>
                  )}
                  <span className="flex-1" />
                  <button className="btn" aria-pressed={doubtful} onClick={() => setDoubtful(!doubtful)} title={`Only items an agent attached with confidence below ${DOUBTFUL_BELOW}`}>
                    <span className={doubtful ? 'text-accent' : ''}>Doubtful only</span> <Keys keys={bindings['proposed.doubtful']} />
                  </button>
                  <button className="text-sm text-accent hover:underline" onClick={() => goQuery(`#${quoteValue(focused.path)}`)}>
                    Open in the library
                  </button>
                </div>
              </div>
              <Samples key={`${focused.id}:${doubtful}`} tag={focused} doubtful={doubtful} page={page} setPage={setPage} />
            </section>
          )}
        </div>
      )}
    </div>
  );
}
