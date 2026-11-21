/** Reader drawer: one item in full (or its thread), with note, tags, media text and metadata. The list stays visible behind it. */
import { useQuery } from '@tanstack/react-query';
import { Archive, ArchiveRestore, ChevronDown, ChevronUp, CircleCheck, ExternalLink, Star, Tag, X } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { serialSaver } from './edits';
import { bulk, runAction, setArchived } from './actions';
import { api, invalidateLibrary, useCapabilities, useWhyUnavailable, type Item } from './api';
import { fullDate } from './format';
import { Engagement, ItemFull, SourceIcon, TagRow } from './ItemCard';
import { setSearch } from './nav';
import { getState, openOverlay, toastError, useStore } from './store';
import { ErrorNote, useHint } from './ui';

/** The last `n` press the note field answered (see `noteFocus` in the store). */
let noteFocusHandled = 0;

function NoteEditor({ item }: { item: Item }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState(item.note ?? '');
  const noteFocus = useStore((s) => s.noteFocus);
  const hint = useHint();
  // `n` asks for the caret here, once per press: moving on to another item must not pull the caret back in.
  useEffect(() => {
    if (noteFocus === noteFocusHandled) return;
    noteFocusHandled = noteFocus;
    ref.current?.focus();
  }, [noteFocus]);

  // Compared with the last note sent, and sent in order: a change made back while a save is still on its way is saved too.
  const [saver] = useState(() =>
    serialSaver(item.note ?? '', (note) => api(`/api/items/${item.id}`, { method: 'PATCH', body: { note: note || null } }).then(invalidateLibrary, toastError)),
  );
  const save = () => void saver(text.trim());
  return (
    <textarea
      ref={ref}
      aria-label="Note"
      title={hint('Edit note', 'item.note')}
      placeholder="Add a note. It is saved when you leave the field."
      rows={3}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={save}
      onKeyDown={(e) => (e.metaKey || e.ctrlKey) && e.key === 'Enter' && e.currentTarget.blur()}
      className="field w-full resize-y"
    />
  );
}

function Heading({ children }: { children: ReactNode }) {
  return <h3 className="mb-1.5 text-sm font-semibold text-ink-2">{children}</h3>;
}

export function Drawer({ id, onClose }: { id: number; onClose: () => void }) {
  const caps = useCapabilities();
  const why = useWhyUnavailable();
  const hint = useHint();
  // The list's copy shows at once; the detail (thread, media text) replaces it when it arrives.
  const query = useQuery({ queryKey: ['item', id], queryFn: () => api<Item>(`/api/items/${id}`), placeholderData: () => getState().pageItems.get(id) });
  const item = query.data;
  const thread = (item?.thread_items ?? []).length > 1 ? item!.thread_items! : null;
  // A reply whose parent is held (and is not already part of a shown thread) is read under that parent.
  const parentId = !thread ? (item?.reply_to?.item_id ?? null) : null;
  const parent = useQuery({ queryKey: ['item', parentId], queryFn: () => api<Item>(`/api/items/${parentId}`), enabled: parentId != null }).data;
  const conversation = thread ?? (parent && item ? [parent, item] : null);
  const iconButton = 'grid h-8 w-8 place-items-center rounded-md text-ink-2 hover:bg-sunken hover:text-ink';

  return (
    <aside data-drawer aria-label="Reader" className="fixed inset-y-0 right-0 z-30 flex w-full sm:w-(--drawer-w) flex-col border-l border-line bg-surface shadow-pop">
      <div className="flex items-center gap-0.5 border-b border-line px-2 py-1.5">
        <button className={iconButton} onClick={onClose} title={hint('Close', 'escape')} aria-label="Close the reader">
          <X size={17} />
        </button>
        <button className={iconButton} onClick={() => runAction('move.prev')} title={hint('Previous item', 'move.prev')} aria-label="Previous item">
          <ChevronUp size={17} />
        </button>
        <button className={iconButton} onClick={() => runAction('move.next')} title={hint('Next item', 'move.next')} aria-label="Next item">
          <ChevronDown size={17} />
        </button>
        <span className="flex-1" />
        {item && (
          <>
            <button className={iconButton} aria-pressed={item.starred} title={hint(item.starred ? 'Unstar' : 'Star', 'item.star')} aria-label="Star" onClick={() => void bulk([item.id], { starred: !item.starred }, item.starred ? 'Unstarred' : 'Starred')}>
              <Star size={16} className={item.starred ? 'fill-mark text-mark' : ''} />
            </button>
            <button className={iconButton} aria-pressed={item.read} title={hint(item.read ? 'Mark unread' : 'Mark read', 'item.read')} aria-label="Read" onClick={() => void bulk([item.id], { read: !item.read }, item.read ? 'Marked unread' : 'Marked read')}>
              <CircleCheck size={16} className={item.read ? 'text-ok' : ''} />
            </button>
            <button className={iconButton} title={hint('Add a tag', 'item.tag')} aria-label="Add a tag" onClick={() => openOverlay({ kind: 'picker', mode: 'add', ids: [item.id] })}>
              <Tag size={16} />
            </button>
            <button className={iconButton} title={hint(item.archived ? 'Restore' : 'Move to Archive', 'item.archive')} aria-label={item.archived ? 'Restore' : 'Archive'} onClick={() => setArchived([item.id], !item.archived)}>
              {item.archived ? <ArchiveRestore size={16} /> : <Archive size={16} />}
            </button>
            {item.url && (
              <a className={iconButton} href={item.url} target="_blank" rel="noopener noreferrer" title={hint('Open the original', 'item.openOriginal')} aria-label="Open the original">
                <ExternalLink size={16} />
              </a>
            )}
          </>
        )}
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto px-5 py-4">
        {query.isPending ? (
          <div className="space-y-3" aria-busy="true">
            <div className="skeleton h-10 w-2/3" />
            <div className="skeleton h-40" />
          </div>
        ) : query.error || !item ? (
          <ErrorNote error={query.error ?? 'This item could not be loaded.'} retry={() => void query.refetch()} />
        ) : (
          <>
            {item.reply_to && !conversation?.some((t) => t.id === item.reply_to!.item_id) && (
              <p className="text-sm text-ink-2">
                Replying to {item.reply_to.author_handle ? `@${item.reply_to.author_handle}` : 'a post'}.{' '}
                {item.reply_to.item_id ? (
                  <button className="text-accent hover:underline" onClick={() => setSearch({ item: item.reply_to!.item_id! })}>
                    Show that post
                  </button>
                ) : item.reply_to.target_state === 'unavailable' ? (
                  'That post is unavailable.'
                ) : item.source === 'x' ? (
                  <a className="text-accent hover:underline" href={`https://x.com/i/status/${item.reply_to.source_id}`} target="_blank" rel="noopener noreferrer">
                    Open that post at the source
                  </a>
                ) : null}
              </p>
            )}
            {conversation ? (
              <ol aria-label={thread ? 'Thread' : 'Conversation'} className="space-y-5">
                {conversation.map((t, i) => (
                  <li key={t.id} className={`border-l-2 pl-4 ${t.id === item.id ? 'border-mark' : 'border-line'}`}>
                    <p className="mb-1 flex items-center gap-2 text-xs text-ink-3">
                      <span>
                        {thread ? `${i + 1} of ${thread.length}` : i === 0 ? 'The post replied to' : 'The reply'}
                        {t.id === item.id && ' · shown in this reader'}
                      </span>
                      {/* Keys act on the post the reader shows; this makes another post of the thread that one. */}
                      {t.id !== item.id && (
                        <button className="text-accent hover:underline" onClick={() => setSearch({ item: t.id })}>
                          Open
                        </button>
                      )}
                    </p>
                    <ItemFull item={t} />
                  </li>
                ))}
              </ol>
            ) : (
              <>
                <ItemFull item={item} />
                {item.thread && !query.isPlaceholderData && <p className="text-sm text-ink-3">Only this post of its thread has been captured.</p>}
              </>
            )}
            <div className="flex items-center gap-3">
              <SourceIcon source={item.source} />
              <Engagement item={item} />
            </div>

            <section>
              <Heading>Tags</Heading>
              <div className="flex flex-wrap items-center gap-2">
                <TagRow tags={item.tags} />
                <button className="btn h-7 px-2 text-xs" onClick={() => openOverlay({ kind: 'picker', mode: 'add', ids: [item.id] })}>
                  {item.tags.length ? 'Edit tags' : 'Add a tag'}
                </button>
              </div>
            </section>

            <section>
              <Heading>Note</Heading>
              {/* Keyed: a different item, or a note changed elsewhere (undo), starts from the stored note. */}
              <NoteEditor key={`${item.id}:${item.note ?? ''}`} item={item} />
            </section>

            {item.media.length > 0 && (
              <section>
                <Heading>Media text</Heading>
                <ul className="space-y-2">
                  {item.media.map((m, i) => (
                    <li key={m.id} className="rounded-md border border-line p-2.5 text-sm">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-ink-2">
                          {m.type.replace('_', ' ')} {i + 1}
                          {m.extract_status !== 'none' && ` · ${m.extract_kind ?? 'text'} ${m.extract_status}`}
                        </span>
                        {!m.extract_text && m.extract_status !== 'queued' && (
                          <button className="btn h-7 px-2 text-xs" disabled={!caps.mediaText} title={caps.mediaText ? hint('Get the text of this item’s media', 'item.mediaText') : why('Media text')} onClick={() => openOverlay({ kind: 'mediaText', ids: [item.id] })}>
                            Get text
                          </button>
                        )}
                      </div>
                      {m.extract_text && <p className="mt-2 max-h-60 overflow-y-auto whitespace-pre-wrap text-ink">{m.extract_text}</p>}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section>
              <Heading>Details</Heading>
              <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 text-sm">
                {(
                  [
                    ['Posted', fullDate(item.posted_at)],
                    ['Saved', item.saved_at ? `${item.saved_at_approximate ? 'About ' : ''}${fullDate(item.saved_at)}` : 'Not known'],
                    ['Captured', fullDate(item.captured_at)],
                    ['Source', `${item.source} · ${item.source_id}`],
                    ['Contains', item.content_types.join(', ')],
                    ['Language', item.lang],
                    ['Folders', item.collections.map((c) => c.name).join(', ')],
                    ['Read', item.read ? 'Yes' : 'No'],
                    ['Kept as', item.is_saved ? 'Saved item' : 'Context only (quoted or thread post)'],
                  ] as [string, string | null][]
                )
                  .filter(([, value]) => value)
                  .map(([label, value]) => (
                    <div key={label} className="contents">
                      <dt className="text-ink-3">{label}</dt>
                      <dd className="min-w-0 break-words">{value}</dd>
                    </div>
                  ))}
              </dl>
            </section>
          </>
        )}
      </div>
    </aside>
  );
}
