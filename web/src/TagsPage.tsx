/** Tag management (tree, rename, describe, move, merge, colour, delete) and the Proposed tags review. */
import { useQuery } from '@tanstack/react-query';
import { Check, Menu, X } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { focusItem, setPageHandlers } from './actions';
import { api, flattenTags, invalidateLibrary, tagsQuery, type FlatTag, type ItemPage, type TagNode } from './api';
import { TAG_COLORS } from './constants';
import { goQuery } from './nav';
import { quoteValue } from './query';
import { openOverlay, setState, toast, toastError, useStore } from './store';
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
  useEffect(() => {
    setName(tag.node.name);
    setDescription(tag.node.description ?? '');
  }, [tag.id, tag.node.name, tag.node.description]);
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
          {tag ? <TagEditor tag={tag} all={all} /> : <p className="rounded-lg border border-dashed border-line px-6 py-10 text-center text-sm text-ink-2">Choose a tag to rename, describe, move, merge, colour or delete it.</p>}
        </div>
      )}
    </div>
  );
}

/** Proposed tags, one per row with its count and a few sample items; `a` approves, `r` rejects, j/k move. */
export function ProposedPage() {
  // Always asked again on opening: proposals come from organising runs outside this app.
  const query = useQuery({ queryKey: ['proposed'], queryFn: () => api<TagNode[]>('/api/tags?status=proposed'), staleTime: 0 });
  const proposed = flattenTags(query.data ?? []).filter((t) => t.node.status === 'proposed');
  const focusId = useStore((s) => s.focusId);
  const bindings = useBindings();
  const ids = proposed.map((t) => t.id).join(',');
  const focused = proposed.find((t) => t.id === focusId);
  const samples = useQuery({ queryKey: ['items', 'sample', focused?.id], queryFn: () => api<ItemPage>(`/api/items?tag_id=${focused!.id}&page_size=25`), enabled: !!focused });

  // The same j/k as the library walk these rows.
  useEffect(() => {
    const list = ids ? ids.split(',').map(Number) : [];
    setState({ pageIds: list, pageItems: new Map(), total: list.length, focusId: list.includes(focusId ?? -1) ? focusId : (list[0] ?? null) });
  }, [ids]);
  useEffect(() => () => setState({ pageIds: [], focusId: null, total: 0 }), []);

  function decide(verb: 'approve' | 'reject', tag: FlatTag | undefined) {
    if (!tag) return;
    const at = proposed.indexOf(tag);
    focusItem((proposed[at + 1] ?? proposed[at - 1])?.id ?? null);
    void change(() => api(`/api/tags/${tag.id}/${verb}`, { method: 'POST' }), `${verb === 'approve' ? 'Approved' : 'Rejected'} #${tag.path}`);
  }
  // Re-registered every render so the keys always act on the row focused now.
  useEffect(() => {
    setPageHandlers({ 'proposed.approve': () => decide('approve', focused), 'proposed.reject': () => decide('reject', focused) });
    return () => setPageHandlers({});
  });

  return (
    <div className="px-3 py-4 sm:px-5">
      <PageHeader title="Proposed tags" />
      <p className="mb-4 max-w-2xl text-sm text-ink-2">
        Tags an organising run suggested. Approving makes a tag part of your tree; rejecting keeps a record so it is not proposed again. <Keys keys={bindings['move.next']} /> <Keys keys={bindings['move.prev']} /> to move, <Keys keys={bindings['proposed.approve']} /> to approve, <Keys keys={bindings['proposed.reject']} /> to reject.
      </p>
      {query.isPending ? (
        <div className="skeleton h-48 max-w-2xl" aria-busy="true" />
      ) : query.error ? (
        <ErrorNote error={query.error} retry={() => void query.refetch()} />
      ) : proposed.length === 0 ? (
        <p className="max-w-2xl rounded-lg border border-dashed border-line px-6 py-10 text-center text-sm text-ink-2">Nothing to review. Proposed tags appear here after an organising run.</p>
      ) : (
        <ul className="max-w-2xl overflow-hidden rounded-lg border border-line bg-surface">
          {proposed.map((t) => {
            const on = t.id === focusId;
            return (
              <li key={t.id} data-focus-id={t.id} aria-current={on || undefined} onClick={() => setState({ focusId: t.id })} className={`scroll-my-24 border-b border-line px-4 py-3 last:border-b-0 ${on ? 'focused' : ''}`}>
                <div className="flex items-center gap-3">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-semibold italic">{t.path.split('/').join(' › ')}</span>
                    {t.node.description && <span className="block text-sm text-ink-2">{t.node.description}</span>}
                  </span>
                  <span className="shrink-0 text-sm tabular-nums text-ink-3">
                    {t.node.count.toLocaleString()} item{t.node.count === 1 ? '' : 's'}
                  </span>
                  <button className="btn h-8 px-2" aria-label={`Approve ${t.path}`} onClick={(e) => (e.stopPropagation(), decide('approve', t))}>
                    <Check size={15} className="text-ok" /> Approve
                  </button>
                  <button className="btn h-8 px-2" aria-label={`Reject ${t.path}`} onClick={(e) => (e.stopPropagation(), decide('reject', t))}>
                    <X size={15} className="text-danger" /> Reject
                  </button>
                </div>
                {on && (
                  <ul aria-label="Sample items" className="mt-2 space-y-1 border-l-2 border-line pl-3 text-sm text-ink-2">
                    {samples.isPending && <li className="skeleton h-12" />}
                    {samples.data?.items.slice(0, 3).map((i) => (
                      <li key={i.id} className="truncate">
                        <span className="font-medium text-ink">{i.author.name ?? i.author.handle}</span> {(i.title ?? i.body ?? '').replace(/\s+/g, ' ')}
                      </li>
                    ))}
                    {samples.data?.items.length === 0 && <li>No items carry it yet.</li>}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
