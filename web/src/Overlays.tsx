/** Everything that opens over the page: one at a time, chosen by the store's `overlay`. Plus the toasts. */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { undo } from './actions';
import { api, invalidateLibrary, useCapabilities, useLists, type Media } from './api';
import { JEV_DEFAULT_THRESHOLD, JEV_EXAMPLE_QUESTION, MEDIA_TEXT_CAREFUL_USD } from './constants';
import { usd } from './format';
import { ACTIONS, type Area } from './keymap';
import { currentSearch, goQuery, pathname, setSearch, withDefaults } from './nav';
import { quoteValue } from './query';
import { Palette } from './Palette';
import { closeOverlay, toast, toastError, useStore, type Overlay } from './store';
import { TagPicker } from './TagPicker';
import { ErrorNote, Keys, Modal, Unavailable, useBindings, useHint } from './ui';

function Frame({ title, children, footer }: { title: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <>
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <h2 className="font-semibold">{title}</h2>
        <button type="button" className="btn btn-ghost h-7 w-7 justify-center px-0" aria-label="Close" onClick={closeOverlay}>
          <X size={15} />
        </button>
      </div>
      <div className="max-h-[72vh] space-y-3 overflow-y-auto px-4 py-3">{children}</div>
      {footer && <div className="flex justify-end gap-2 border-t border-line px-4 py-2.5">{footer}</div>}
    </>
  );
}

function Help() {
  const bindings = useBindings();
  const areas = [...new Set(ACTIONS.map((a) => a.area))] as Area[];
  return (
    <Modal label="Keyboard shortcuts" className="mt-[5vh]! max-h-[90vh]! w-[min(72rem,calc(100vw-1.5rem))]">
      <Frame title="Keyboard shortcuts">
        <div className="gap-x-8 sm:columns-2 lg:columns-3">
          {areas.map((area) => (
            <section key={area} className="mb-4 break-inside-avoid">
              <h3 className="mb-1 text-xs font-semibold text-ink-3">{area}</h3>
              <dl>
                {ACTIONS.filter((a) => a.area === area).map((a) => (
                  <div key={a.id} className="flex items-center justify-between gap-3 py-0.5 text-sm">
                    <dt>{a.label}</dt>
                    <dd>
                      <Keys keys={bindings[a.id]} />
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
        <p className="text-sm text-ink-2">Actions apply to the selection when there is one. Keys are ignored while typing in a field, and can be changed in Settings.</p>
      </Frame>
    </Modal>
  );
}

function Confirm({ o }: { o: Extract<Overlay, { kind: 'confirm' }> }) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal label={o.title} className="w-[min(30rem,calc(100vw-1.5rem))]">
      <Frame
        title={o.title}
        footer={
          <>
            <button className="btn" onClick={closeOverlay}>
              Cancel
            </button>
            <button
              data-autofocus
              className={`btn ${o.danger ? 'btn-danger' : 'btn-primary'}`}
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                await o.run();
                closeOverlay();
              }}
            >
              {busy ? 'Working…' : o.action}
            </button>
          </>
        }
      >
        <p className="text-sm text-ink-2">{o.body}</p>
      </Frame>
    </Modal>
  );
}

function Lists({ ids }: { ids: number[] }) {
  const caps = useCapabilities();
  const lists = useLists();
  const [text, setText] = useState('');
  const [active, setActive] = useState(0);
  const shown = lists.filter((l) => l.name.toLowerCase().includes(text.trim().toLowerCase()));
  const canCreate = text.trim() && !lists.some((l) => l.name.toLowerCase() === text.trim().toLowerCase());
  const rows = [...shown.map((l) => ({ id: l.id as number | null, name: l.name, count: l.count })), ...(canCreate ? [{ id: null, name: text.trim(), count: undefined }] : [])];
  const at = Math.min(active, Math.max(rows.length - 1, 0));

  async function add(row: (typeof rows)[number] | undefined) {
    if (!row) return;
    try {
      const id = row.id ?? (await api<{ id: number }>('/api/lists', { method: 'POST', body: { name: row.name } })).id;
      await api(`/api/lists/${id}/items`, { method: 'POST', body: { item_ids: ids } });
      toast(`Added ${ids.length} item${ids.length === 1 ? '' : 's'} to ${row.name}`);
      invalidateLibrary();
      closeOverlay();
    } catch (error) {
      toastError(error);
    }
  }
  return (
    <Modal label="Add to list" className="w-[min(30rem,calc(100vw-1.5rem))]">
      <Frame title={`Add ${ids.length === 1 ? 'this item' : `${ids.length} items`} to a list`}>
        {!caps.lists ? (
          <Unavailable what="Lists" />
        ) : (
          <>
            <input
              data-autofocus
              aria-label="Find or name a list"
              placeholder="Find a list, or type a new name"
              value={text}
              onChange={(e) => (setText(e.target.value), setActive(0))}
              onKeyDown={(e) => {
                if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  setActive((at + 1) % Math.max(rows.length, 1));
                }
                else if (e.key === 'ArrowUp') {
                  e.preventDefault();
                  setActive((at - 1 + rows.length) % Math.max(rows.length, 1));
                }
                else if (e.key === 'Enter') void add(rows[at]);
              }}
              className="field w-full"
            />
            <ul role="listbox" aria-label="Lists">
              {rows.length === 0 && <li className="py-2 text-sm text-ink-3">No lists yet. Type a name to make the first one.</li>}
              {rows.map((r, i) => (
                <li key={r.id ?? 'new'} role="option" aria-selected={i === at} onMouseMove={() => i !== at && setActive(i)} onClick={() => void add(r)} className={`flex cursor-pointer items-center justify-between rounded px-2 py-1.5 text-sm ${i === at ? 'bg-accent-soft' : ''}`}>
                  <span>{r.id == null ? `Create the list “${r.name}”` : r.name}</span>
                  <span className="text-xs tabular-nums text-ink-3">{r.count?.toLocaleString()}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </Frame>
    </Modal>
  );
}

/** Jev check: a yes/no question asked of every item in the current result. Shows the cost first, then filters. */
function Jev({ question: initial = '' }: { question?: string }) {
  const caps = useCapabilities();
  const s = withDefaults(currentSearch());
  const [question, setQuestion] = useState(initial);
  const [threshold, setThreshold] = useState(JEV_DEFAULT_THRESHOLD);
  const [estimate, setEstimate] = useState<{ estimate_usd: number; n: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const body = (dry_run: boolean) => ({ q: s.q, mode: s.mode, question: question.trim(), threshold, dry_run });

  async function call(dryRun: boolean) {
    setBusy(true);
    setError(null);
    try {
      if (dryRun) setEstimate(await api('/api/jev/check', { method: 'POST', body: body(true) }));
      else {
        const result = await api<{ snapshot_id: string | number; kept: number; spent_usd: number }>('/api/jev/check', { method: 'POST', body: body(false) });
        setSearch({ snap: String(result.snapshot_id), jq: question.trim(), page: undefined, item: undefined });
        toast(`Jev kept ${result.kept.toLocaleString()} item${result.kept === 1 ? '' : 's'} (${usd(result.spent_usd)} spent)`);
        closeOverlay();
      }
    } catch (e) {
      setError(e);
    }
    setBusy(false);
  }
  return (
    <Modal label="Jev check" className="w-[min(34rem,calc(100vw-1.5rem))]">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void call(!estimate);
        }}
      >
        <Frame
          title="Jev check on this result"
          footer={
            <>
              <button type="button" className="btn" onClick={closeOverlay}>
                Cancel
              </button>
              <button className="btn btn-primary" disabled={busy || !question.trim() || !caps.jev}>
                {busy ? 'Working…' : estimate ? `Run for ${usd(estimate.estimate_usd)}` : 'Estimate the cost'}
              </button>
            </>
          }
        >
          {!caps.jev && <Unavailable what="Jev check" />}
          <p className="text-sm text-ink-2">Asks a yes/no question of each item in {s.q ? <code className="rounded bg-sunken px-1">{s.q}</code> : 'the whole library'} and keeps the ones that pass.</p>
          <label className="block text-sm font-medium">
            Question
            <input data-autofocus className="field mt-1 w-full" placeholder={JEV_EXAMPLE_QUESTION} value={question} onChange={(e) => (setQuestion(e.target.value), setEstimate(null))} aria-describedby="jev-hint" />
          </label>
          <p id="jev-hint" className="text-sm text-ink-3">
            Jev reads literally. Write a full sentence that says what counts, for example “{JEV_EXAMPLE_QUESTION}” A fragment such as “about RAG?” leaves most items undecided.
          </p>
          <label className="block text-sm font-medium">
            Keep items scoring at least {threshold.toFixed(2)}
            <input type="range" min={0.05} max={0.95} step={0.05} value={threshold} onChange={(e) => (setThreshold(Number(e.target.value)), setEstimate(null))} className="mt-1 block w-full accent-(--accent)" />
          </label>
          {estimate && (
            <p role="status" className="rounded-md bg-sunken px-3 py-2 text-sm">
              {estimate.n.toLocaleString()} item{estimate.n === 1 ? '' : 's'} to check, estimated <strong>{usd(estimate.estimate_usd)}</strong>. Answers already cached cost nothing.
            </p>
          )}
          {!!error && <ErrorNote error={error} what="Jev check" />}
        </Frame>
      </form>
    </Modal>
  );
}

/** `POST /api/media-text/estimate`: files without text yet, of which `images`; `minutes` of video and audio. */
interface MediaEstimate {
  n_media: number;
  minutes: number;
  images: number;
  estimate_usd: number;
}

/** Media text: shows how many files, how many minutes and the estimated cost before anything is spent. */
function MediaText({ ids }: { ids: number[] | 'all' }) {
  const caps = useCapabilities();
  const client = useQueryClient();
  const target = ids === 'all' ? { all: true } : { item_ids: ids };
  const estimate = useQuery({ queryKey: ['media-text-estimate', ids], queryFn: () => api<MediaEstimate>('/api/media-text/estimate', { method: 'POST', body: target }), enabled: caps.mediaText, staleTime: 0 });
  const [busy, setBusy] = useState(false);
  async function start() {
    setBusy(true);
    try {
      const { queued } = await api<MediaEstimate & { queued: number }>('/api/media-text/start', {
        method: 'POST',
        // The server refuses large runs unless the request carries the estimate the user just saw.
        body: { ...target, confirm_usd: estimate.data?.estimate_usd },
      });
      toast(`Getting text for ${queued.toLocaleString()} media file${queued === 1 ? '' : 's'}; it appears on the items when done`);
      void client.invalidateQueries({ queryKey: ['media-text-status'] });
      invalidateLibrary();
      closeOverlay();
    } catch (error) {
      toastError(error);
    }
    setBusy(false);
  }
  const e = estimate.data;
  // A large run must be chosen on purpose: Enter lands on Cancel, not on Start.
  const careful = !e || e.estimate_usd > MEDIA_TEXT_CAREFUL_USD;
  const startButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!careful) startButton.current?.focus();
  }, [careful]);
  return (
    <Modal label="Get media text" className="w-[min(30rem,calc(100vw-1.5rem))]">
      <Frame
        title="Get media text"
        footer={
          <>
            <button data-autofocus className="btn" onClick={closeOverlay}>
              Cancel
            </button>
            <button ref={startButton} className={`btn ${careful ? 'btn-danger' : 'btn-primary'}`} disabled={busy || !e || e.n_media === 0} onClick={start}>
              {busy ? 'Starting…' : e ? `Start ${e.n_media.toLocaleString()} file${e.n_media === 1 ? '' : 's'} for about ${usd(e.estimate_usd)}` : 'Start'}
            </button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          Transcribes video and audio and describes images for {ids === 'all' ? <strong className="text-ink">every media file in the library that has no text yet</strong> : `${ids.length.toLocaleString()} item${ids.length === 1 ? '' : 's'}`}, so search can find what is said and shown.
        </p>
        {!caps.mediaText ? (
          <Unavailable what="Media text" />
        ) : estimate.isPending ? (
          <div className="skeleton h-16" aria-busy="true" />
        ) : estimate.error ? (
          <ErrorNote error={estimate.error} retry={() => void estimate.refetch()} what="Media text" />
        ) : (
          <>
            <dl className="grid grid-cols-3 gap-2 text-center">
              {[
                ['Files', e!.n_media.toLocaleString(), `${e!.images.toLocaleString()} image${e!.images === 1 ? '' : 's'}, ${(e!.n_media - e!.images).toLocaleString()} video or audio`],
                ['Minutes to transcribe', e!.minutes.toLocaleString(undefined, { maximumFractionDigits: 1 }), ''],
                ['Estimated cost', usd(e!.estimate_usd), ''],
              ].map(([label, value, detail]) => (
                <div key={label} className="rounded-md bg-sunken px-2 py-3">
                  <dd className="text-2xl font-semibold tabular-nums">{value}</dd>
                  <dt className="text-xs text-ink-3">{label}</dt>
                  {detail && <dd className="mt-0.5 text-xs text-ink-3">{detail}</dd>}
                </div>
              ))}
            </dl>
            {e!.n_media === 0 && <p className="text-sm text-ink-2">Nothing to do: {ids === 'all' ? 'every media file has' : 'the media of these items already have'} text, or there is no media.</p>}
            {careful && e!.n_media > 0 && <p className="text-sm text-ink-2">This is a large run. It works in the background, and the server refuses it when it would pass your monthly LLM cap.</p>}
          </>
        )}
      </Frame>
    </Modal>
  );
}

function SaveView({ name: initial = '' }: { name?: string }) {
  const client = useQueryClient();
  const s = withDefaults(currentSearch());
  const [name, setName] = useState(initial);
  return (
    <Modal label="Save view" className="w-[min(30rem,calc(100vw-1.5rem))]">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await api('/api/saved-views', { method: 'POST', body: { name: name.trim(), query: s.q, params: { mode: s.mode, sort: s.sort, view: s.view } } });
            void client.invalidateQueries({ queryKey: ['saved-views'] });
            toast(`Saved the view ${name.trim()}`);
            closeOverlay();
          } catch (error) {
            toastError(error);
          }
        }}
      >
        <Frame
          title="Save this search as a view"
          footer={
            <>
              <button type="button" className="btn" onClick={closeOverlay}>
                Cancel
              </button>
              <button className="btn btn-primary" disabled={!name.trim()}>
                Save view
              </button>
            </>
          }
        >
          <p className="text-sm text-ink-2">
            {s.q ? <code className="rounded bg-sunken px-1">{s.q}</code> : 'All items'} · {s.mode} · {s.view} layout{pathname() === '/archive' ? ' (the Archive itself is not part of a view)' : ''}
          </p>
          <label className="block text-sm font-medium">
            Name
            <input data-autofocus className="field mt-1 w-full" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
        </Frame>
      </form>
    </Modal>
  );
}

function RenameList({ id, name: initial }: { id: number; name: string }) {
  const [name, setName] = useState(initial);
  return (
    <Modal label="Rename list" className="w-[min(30rem,calc(100vw-1.5rem))]">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await api(`/api/lists/${id}`, { method: 'PATCH', body: { name: name.trim() } });
            toast(`Renamed the list to ${name.trim()}`);
            invalidateLibrary();
            // A view of the list is addressed by its name: follow the rename.
            if (pathname() === '/' && currentSearch().q === `list:${quoteValue(initial)}`) goQuery(`list:${quoteValue(name.trim())}`, true);
            closeOverlay();
          } catch (error) {
            toastError(error);
          }
        }}
      >
        <Frame
          title={`Rename the list ${initial}`}
          footer={
            <>
              <button type="button" className="btn" onClick={closeOverlay}>
                Cancel
              </button>
              <button className="btn btn-primary" disabled={!name.trim() || name.trim() === initial}>
                Rename
              </button>
            </>
          }
        >
          <input data-autofocus aria-label="List name" className="field w-full" value={name} onChange={(e) => setName(e.target.value)} />
        </Frame>
      </form>
    </Modal>
  );
}

function Lightbox({ media, index }: { media: Media[]; index: number }) {
  const [at, setAt] = useState(index);
  const step = (d: number) => setAt((at + d + media.length) % media.length);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => (e.key === 'ArrowRight' || e.key === 'j' ? step(1) : e.key === 'ArrowLeft' || e.key === 'k' ? step(-1) : undefined);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });
  const m = media[at];
  const arrow = 'absolute top-1/2 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full bg-black/60 text-white hover:bg-black/80';
  return (
    <Modal label={`Photo ${at + 1} of ${media.length}`} className="mt-[4vh]! max-h-[92vh]! w-fit! max-w-[96vw] border-0! bg-transparent! shadow-none!">
      <div className="relative">
        <img src={m.url} alt={m.alt_text ?? ''} className="max-h-[92vh] max-w-[96vw] rounded-lg object-contain" />
        <button className="absolute right-2 top-2 grid h-9 w-9 place-items-center rounded-full bg-black/60 text-white hover:bg-black/80" aria-label="Close" onClick={closeOverlay}>
          <X size={18} />
        </button>
        {media.length > 1 && (
          <>
            <button className={`${arrow} left-2`} aria-label="Previous photo" onClick={() => step(-1)}>
              <ChevronLeft size={20} />
            </button>
            <button className={`${arrow} right-2`} aria-label="Next photo" onClick={() => step(1)}>
              <ChevronRight size={20} />
            </button>
            <span className="absolute bottom-2 left-1/2 -translate-x-1/2 rounded bg-black/60 px-2 py-0.5 text-xs text-white">
              {at + 1} / {media.length}
            </span>
          </>
        )}
      </div>
    </Modal>
  );
}

export function Overlays() {
  const o = useStore((s) => s.overlay);
  if (!o) return null;
  switch (o.kind) {
    case 'picker':
      return <TagPicker key={o.mode} mode={o.mode} ids={o.ids} />;
    case 'palette':
      return <Palette key={o.scope ?? 'all'} scope={o.scope} initial={o.initial} />;
    case 'help':
      return <Help />;
    case 'lists':
      return <Lists ids={o.ids} />;
    case 'jev':
      return <Jev question={o.question} />;
    case 'mediaText':
      return <MediaText ids={o.ids} />;
    case 'saveView':
      return <SaveView name={o.name} />;
    case 'renameList':
      return <RenameList id={o.id} name={o.name} />;
    case 'lightbox':
      return <Lightbox media={o.media} index={o.index} />;
    case 'confirm':
      return <Confirm o={o} />;
  }
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  const hint = useHint();
  return (
    <div aria-live="polite" className="pointer-events-none fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 flex-col items-center gap-2 max-sm:bottom-16">
      {toasts.map((t) => (
        <div key={t.id} role="status" className={`pointer-events-auto flex items-center gap-3 rounded-lg border bg-ink px-3 py-2 text-sm text-bg shadow-pop ${t.error ? 'border-danger' : 'border-transparent'}`}>
          <span>{t.text}</span>
          {t.undoBatch != null && (
            <button className="font-semibold underline underline-offset-2" title={hint('Undo', 'undo')} onClick={() => void undo(t.undoBatch)}>
              Undo
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
