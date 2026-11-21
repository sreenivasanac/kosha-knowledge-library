/**
 * Tag picker (`t`, and `Shift+T` to remove). Type to search full paths; Enter
 * adds the highlighted tag; a path that does not exist is offered as "Create".
 * The item's current tags are listed first with their roles: with the box
 * empty, `1` / `2` make the highlighted tag primary / secondary and Backspace
 * removes it. (While there is text in the box, digits are just text, so tag
 * names may contain them.)
 */
import { useQuery } from '@tanstack/react-query';
import { Plus, X } from 'lucide-react';
import { useState } from 'react';
import { bulk, ensureTagPath } from './actions';
import { api, flattenTags, invalidateLibrary, tagsQuery, type Item, type ItemTag } from './api';
import { closeOverlay, toastError, useStore } from './store';
import { Modal } from './ui';

type RowKind = 'current' | 'tag' | 'create';
interface PickerRow {
  kind: RowKind;
  tagId?: number;
  path: string;
  role?: ItemTag['role'];
  proposed?: boolean;
  count?: number;
  /** How many of the target items carry it (current rows, when several items are targeted). */
  on?: number;
}

export function TagPicker({ mode, ids }: { mode: 'add' | 'remove'; ids: number[] }) {
  const tags = flattenTags(useQuery(tagsQuery).data ?? []).filter((t) => t.node.status !== 'rejected');
  const pageItems = useStore((s) => s.pageItems);
  // An item opened in the drawer may not be on the page; a single target is always read from the server.
  const detail = useQuery({ queryKey: ['item', ids[0]], queryFn: () => api<Item>(`/api/items/${ids[0]}`), enabled: ids.length === 1 }).data;
  const items = (ids.length === 1 ? [detail ?? pageItems.get(ids[0])] : ids.map((id) => pageItems.get(id))).filter((i): i is Item => !!i);
  const [text, setText] = useState('');
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(false);
  // The tag just added stays highlighted among the current tags, so `1` / `2` can follow Enter directly.
  const [justAdded, setJustAdded] = useState<number | null>(null);

  const current = new Map<number, PickerRow>();
  for (const item of items)
    for (const t of item.tags) {
      const row = current.get(t.tag_id) ?? { kind: 'current' as const, tagId: t.tag_id, path: t.path.join('/'), role: t.role, proposed: t.status === 'proposed', on: 0 };
      row.on = (row.on ?? 0) + 1;
      current.set(t.tag_id, row);
    }
  const needle = text.trim().toLowerCase();
  const matches = (path: string) => needle.split(/\s+/).every((word) => path.toLowerCase().includes(word));
  const currentRows = [...current.values()].filter((r) => matches(r.path));
  const tagRows: PickerRow[] = mode === 'remove' ? [] : tags.filter((t) => !current.has(t.id) && matches(t.path)).slice(0, 50).map((t) => ({ kind: 'tag', tagId: t.id, path: t.path, proposed: t.node.status === 'proposed', count: t.node.count }));
  const exists = tags.some((t) => t.path.toLowerCase() === needle);
  const rows: PickerRow[] = [...(needle ? [] : currentRows), ...tagRows, ...(needle ? currentRows : []), ...(mode === 'add' && needle && !exists ? [{ kind: 'create' as const, path: text.trim().replace(/\s*\/\s*/g, '/') }] : [])];
  const added = justAdded == null ? -1 : rows.findIndex((r) => r.kind === 'current' && r.tagId === justAdded);
  const at = added >= 0 ? added : Math.min(active, Math.max(rows.length - 1, 0));
  const moveTo = (i: number) => (setJustAdded(null), setActive(i));
  const row = rows[at];
  const n = ids.length;
  const many = n === 1 ? '' : ` ${n} items`;

  /** Runs one change, then keeps the picker open and ready for the next. */
  async function run(work: () => Promise<unknown>) {
    setBusy(true);
    try {
      await work();
      setText('');
      setActive(0);
    } catch (error) {
      toastError(error);
    }
    invalidateLibrary();
    setBusy(false);
  }
  const add = (r: PickerRow) =>
    run(async () => {
      const tagId = r.kind === 'create' ? await ensureTagPath(r.path) : r.tagId!;
      await bulk(ids, { add_tag_ids: [tagId] }, `Tagged${many || ' item'} #${r.path}`);
      setJustAdded(tagId);
    });
  const remove = (r: PickerRow) => run(() => bulk(ids, { remove_tag_ids: [r.tagId!] }, `Removed #${r.path}${many && ` from${many}`}`));
  // The role endpoint works per item; pressing the key again on the same role turns it back to "other".
  const setRole = (r: PickerRow, role: 'primary' | 'secondary') =>
    run(async () => {
      const tagId = r.kind === 'create' ? await ensureTagPath(r.path) : r.tagId!;
      for (const id of ids) await api(`/api/items/${id}/tags/${tagId}`, { method: 'PUT', body: { role: r.role === role ? 'other' : role } });
    });

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown' || (e.ctrlKey && e.key === 'n')) (e.preventDefault(), moveTo((at + 1) % Math.max(rows.length, 1)));
    else if (e.key === 'ArrowUp' || (e.ctrlKey && e.key === 'p')) (e.preventDefault(), moveTo((at - 1 + rows.length) % Math.max(rows.length, 1)));
    else if (!row || busy) return;
    else if (e.key === 'Enter') (e.preventDefault(), void (mode === 'remove' || (row.kind === 'current' && e.shiftKey) ? remove(row) : row.kind === 'current' ? closeOverlay() : add(row)));
    else if (!text && (e.key === '1' || e.key === '2')) (e.preventDefault(), void setRole(row, e.key === '1' ? 'primary' : 'secondary'));
    else if (!text && e.key === 'Backspace' && row.kind === 'current') (e.preventDefault(), void remove(row));
  }

  return (
    <Modal label={mode === 'add' ? 'Add a tag' : 'Remove a tag'}>
      <div className="border-b border-line p-3">
        <label htmlFor="tag-input" className="mb-1.5 block text-sm font-semibold">
          {mode === 'add' ? `Tag${many || ' this item'}` : `Remove a tag from${many || ' this item'}`}
        </label>
        <input id="tag-input" data-autofocus autoComplete="off" role="combobox" aria-expanded aria-controls="tag-options" aria-activedescendant={row ? `tag-option-${at}` : undefined} placeholder={mode === 'add' ? 'Type a tag, or a new path like ai/agents' : 'Type to find one of its tags'} value={text} onChange={(e) => (setText(e.target.value), moveTo(0))} onKeyDown={onKeyDown} className="field h-9 w-full" />
      </div>
      <ul id="tag-options" role="listbox" aria-label="Tags" className="max-h-[46vh] overflow-y-auto py-1">
        {rows.length === 0 && <li className="px-3 py-3 text-sm text-ink-3">{mode === 'remove' ? 'No tag on the selection matches.' : 'No tags yet. Type a name to create the first one.'}</li>}
        {rows.map((r, i) => (
          <li key={`${r.kind}-${r.tagId ?? r.path}`} id={`tag-option-${i}`} role="option" aria-selected={i === at} onMouseMove={() => i !== at && moveTo(i)} onClick={() => !busy && void (r.kind === 'current' ? (mode === 'remove' ? remove(r) : undefined) : add(r))} className={`flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm ${i === at ? 'bg-accent-soft' : ''}`}>
            {r.kind === 'create' ? (
              <>
                <Plus size={14} aria-hidden className="text-accent" />
                <span>
                  Create <strong className="font-semibold">#{r.path}</strong>
                  {r.path.includes('/') && <span className="text-ink-3"> (with any missing levels)</span>}
                </span>
              </>
            ) : (
              <>
                <span className={`min-w-0 flex-1 truncate ${r.proposed ? 'italic text-ink-2' : ''}`}>
                  {r.path.split('/').join(' › ')}
                  {r.proposed && ' (proposed)'}
                </span>
                {r.kind === 'current' ? (
                  <>
                    {n > 1 && <span className="text-xs text-ink-3">on {r.on} of {n}</span>}
                    <span className={`rounded px-1.5 py-px text-xs font-medium ${r.role === 'primary' ? 'bg-accent text-surface' : r.role === 'secondary' ? 'border border-accent text-accent' : 'bg-sunken text-ink-2'}`}>{n > 1 && r.role === 'other' ? 'on' : r.role}</span>
                    <button aria-label={`Remove ${r.path}`} title="Remove (Backspace)" disabled={busy} className="grid h-6 w-6 place-items-center rounded text-ink-3 hover:bg-sunken hover:text-danger" onClick={(e) => (e.stopPropagation(), void remove(r))}>
                      <X size={13} />
                    </button>
                  </>
                ) : (
                  <span className="text-xs tabular-nums text-ink-3">{r.count?.toLocaleString()}</span>
                )}
              </>
            )}
          </li>
        ))}
      </ul>
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-3 py-2 text-xs text-ink-3">
        <span>
          <kbd className="kbd">Enter</kbd> {mode === 'add' ? 'add' : 'remove'}
        </span>
        <span>
          <kbd className="kbd">1</kbd> primary
        </span>
        <span>
          <kbd className="kbd">2</kbd> secondary
        </span>
        <span>
          <kbd className="kbd">⌫</kbd> remove (box empty)
        </span>
        <span>
          <kbd className="kbd">Esc</kbd> done
        </span>
      </p>
    </Modal>
  );
}
