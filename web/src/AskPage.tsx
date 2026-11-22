/** Ask (`g k`): a question answered from the library, with the cited items as normal cards and how the search went. */
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react';
import { focusItem } from './actions';
import { parseAnswer, type Inline } from './answer';
import { api, ApiError, useCapabilities, type Item, type ItemPage } from './api';
import { CSRF_HEADER_VALUE } from './constants';
import { usd } from './format';
import { ItemCard } from './ItemCard';
import { COLUMN } from './Library';
import { goQuery, type LibrarySearch } from './nav';
import { setState } from './store';
import { PageHeader } from './TagsPage';
import { ErrorNote, Unavailable } from './ui';

/** One read-only pipeline Ask ran: how many items it ended with, and each stage's in→out (or the error). */
interface Step {
  pipeline: string;
  n: number | null;
  note?: string | null;
}
interface AskReply {
  answer: string;
  items: number[];
  steps: Step[];
  spent?: { llm_usd: number; jev_usd: number } | null;
  took_ms?: number | null;
  model?: string | null;
}

/**
 * `POST /api/ask/stream`: server-sent events, one `step` per pipeline as it
 * finishes, then `done` with the whole reply (or `error`). Read with fetch,
 * because EventSource cannot send a POST.
 */
async function askStream(question: string, onStep: (step: Step) => void): Promise<AskReply> {
  const response = await fetch('/api/ask/stream', { method: 'POST', credentials: 'same-origin', headers: { 'X-Requested-With': CSRF_HEADER_VALUE, 'content-type': 'application/json' }, body: JSON.stringify({ question }) });
  // Anything but a stream: the plain endpoint gives the same answer, or the proper error.
  if (!response.ok || !response.body) return api<AskReply>('/api/ask', { method: 'POST', body: { question } });
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) throw new ApiError(502, 'The answer stopped before it was finished');
    buffer += value;
    let end: number;
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const event = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const name = /^event: (.*)$/m.exec(event)?.[1];
      const data = JSON.parse(/^data: (.*)$/m.exec(event)?.[1] ?? 'null');
      if (name === 'step') onStep(data);
      else if (name === 'done') return data;
      else if (name === 'error') throw new ApiError(503, data?.message ?? 'Ask failed');
    }
  }
}

function Steps({ steps }: { steps: Step[] }) {
  return (
    <ol className="divide-y divide-line">
      {steps.map((step, i) => (
        <li key={i} className="space-y-0.5 px-3 py-1.5">
          <div className="flex items-baseline gap-3">
            <code className="min-w-0 flex-1 break-words font-mono text-[0.85em]">{step.pipeline}</code>
            {step.n != null && <span className="shrink-0 tabular-nums text-ink-2">{step.n.toLocaleString()}</span>}
          </div>
          {step.note && <div className={`break-words text-xs ${step.note.startsWith('error') ? 'text-danger' : 'text-ink-3'}`}>{step.note}</div>}
        </li>
      ))}
    </ol>
  );
}

function InlineText({ parts }: { parts: Inline[] }) {
  return (
    <>
      {parts.map((p, i): ReactNode => {
        if (p.kind === 'bold') return <strong key={i}>{p.text}</strong>;
        if (p.kind === 'code') return <code key={i} className="rounded bg-sunken px-1 text-[0.9em]">{p.text}</code>;
        if (p.kind === 'link')
          return (
            <a key={i} href={p.href} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
              {p.text}
            </a>
          );
        if (p.kind === 'cite')
          return (
            // A citation moves the focus ribbon to the cited card below.
            <button key={i} className="mx-0.5 rounded bg-accent-soft px-1 text-xs font-semibold tabular-nums text-accent hover:underline" title="Show the cited item" onClick={() => focusItem(p.id)}>
              {p.id}
            </button>
          );
        return p.text;
      })}
    </>
  );
}

const listItems = (items: Inline[][]) =>
  items.map((item, j) => (
    <li key={j}>
      <InlineText parts={item} />
    </li>
  ));

/** The answer text. Built from parsed pieces as React text nodes: nothing in it is ever inserted as HTML. */
export function Answer({ text, cited }: { text: string; cited: number[] }) {
  return (
    <div className="space-y-3 text-[1.067rem] leading-relaxed">
      {parseAnswer(text, cited).map((b, i) =>
        b.kind === 'pre' ? (
          <pre key={i} className="overflow-x-auto rounded-md bg-sunken p-3 font-mono text-sm">
            {b.text}
          </pre>
        ) : 'items' in b ? (
          b.kind === 'ul' ? (
            <ul key={i} className="list-disc space-y-1 pl-5">
              {listItems(b.items)}
            </ul>
          ) : (
            <ol key={i} className="list-decimal space-y-1 pl-5">
              {listItems(b.items)}
            </ol>
          )
        ) : b.kind === 'h' ? (
          <h3 key={i} className="pt-1 text-base font-semibold">
            <InlineText parts={b.inline} />
          </h3>
        ) : (
          <p key={i} className="whitespace-pre-wrap">
            <InlineText parts={b.inline} />
          </p>
        ),
      )}
    </div>
  );
}

export function AskPage() {
  const caps = useCapabilities();
  const navigate = useNavigate();
  const question = (useSearch({ strict: false }) as LibrarySearch).q ?? '';

  // Asking costs money, so an answer is kept for the session and never retried or refetched on its own.
  /** The steps of the question being answered, as they arrive. */
  const [live, setLive] = useState<Step[]>([]);
  const ask = useQuery({
    queryKey: ['ask', question],
    queryFn: () => {
      setLive([]);
      return caps.askStream ? askStream(question, (step) => setLive((old) => [...old, step])) : api<AskReply>('/api/ask', { method: 'POST', body: { question } });
    },
    enabled: !!question && caps.ask,
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
    // A failed question is not sent again (and paid for again) just because the page is shown again; "Try again" still asks.
    retryOnMount: false,
  });
  const ids = ask.data?.items ?? [];
  // The cited items through the normal list endpoint, so they render (and act) as cards do everywhere.
  const cited = useQuery({ queryKey: ['items', 'ask', ids], queryFn: () => api<ItemPage>(`/api/items?${new URLSearchParams({ q: `id:${ids.join(',')}`, page_size: '100' })}`), enabled: ids.length > 0 });
  const byId = new Map(cited.data?.items.map((i) => [i.id, i]));
  const items = ids.map((id) => byId.get(id)).filter((i): i is Item => !!i);
  const shownIds = items.map((i) => i.id).join(',');

  // Hand the cards to the keyboard handlers: j/k, Enter, t, s and the rest work as in the library.
  useLayoutEffect(() => {
    setState({ pageIds: items.map((i) => i.id), pageItems: new Map(items.map((i) => [i.id, i])), total: items.length });
    // `items` is rebuilt every render; its ids say when it really changed.
  }, [shownIds, cited.data]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => setState({ pageIds: [], pageItems: new Map(), total: 0, selection: new Set(), focusId: null }), []);

  const missing = !caps.ask || (ask.error instanceof ApiError && ask.error.status === 404);
  const reply = ask.data;
  return (
    <div className="px-3 py-4 sm:px-5">
      <div className={COLUMN}>
        <PageHeader title="Ask" />
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const text = String(new FormData(e.currentTarget).get('question') ?? '').trim();
            if (text) void navigate({ to: '/ask', search: { q: text } });
          }}
        >
          {/* Keyed by the question in the URL, so Back and the palette's Ask show the question that was asked. */}
          <input key={question} name="question" required autoFocus={!question} defaultValue={question} aria-label="Your question" placeholder="What have I saved about evaluating RAG systems?" className="field h-10 flex-1 text-base" />
          <button className="btn btn-primary h-10" disabled={ask.isFetching}>
            Ask
          </button>
        </form>
        <p className="mt-1.5 text-sm text-ink-3">Kosha searches your library in a few steps, then answers from what it found and shows those items.</p>

        <div className="mt-5 space-y-5" aria-live="polite">
          {missing ? (
            <div className="space-y-2">
              <Unavailable what="Ask" />
              {question && (
                <button className="btn" onClick={() => goQuery(question)}>
                  Search for “{question}” instead
                </button>
              )}
            </div>
          ) : ask.isFetching ? (
            <div className="space-y-3" aria-busy="true" aria-label="Searching and answering">
              {live.length > 0 && (
                <div className="rounded-lg border border-line bg-surface text-sm">
                  <Steps steps={live} />
                </div>
              )}
              <div className="skeleton h-5 w-11/12" />
              <div className="skeleton h-5 w-4/5" />
              <div className="skeleton h-5 w-2/3" />
            </div>
          ) : ask.error ? (
            <ErrorNote error={ask.error} retry={() => void ask.refetch()} />
          ) : reply ? (
            <>
              <Answer text={reply.answer} cited={reply.items} />
              <details className="rounded-lg border border-line bg-surface text-sm">
                <summary className="px-3 py-2 font-medium text-ink-2">
                  How it searched: {reply.steps.length} step{reply.steps.length === 1 ? '' : 's'}
                  {reply.took_ms != null && `, ${(reply.took_ms / 1000).toFixed(1)} s`}
                  {reply.spent != null && `, ${usd(reply.spent.llm_usd + reply.spent.jev_usd)}`}
                </summary>
                <div className="border-t border-line">
                  <Steps steps={reply.steps} />
                </div>
              </details>
            </>
          ) : null}
        </div>
      </div>

      {reply && !missing && ids.length > 0 && (
        <section aria-label="Cited items" className="mt-5">
          <h2 className={`${COLUMN} mb-2 text-sm font-semibold text-ink-2`}>
            {ids.length} cited item{ids.length === 1 ? '' : 's'}
          </h2>
          {cited.isPending ? (
            <div className={`${COLUMN} skeleton h-40`} aria-busy="true" />
          ) : cited.error ? (
            <div className={COLUMN}>
              <ErrorNote error={cited.error} retry={() => void cited.refetch()} />
            </div>
          ) : (
            <div className={`${COLUMN} overflow-hidden rounded-lg border border-line bg-surface`}>
              {items.map((item) => (
                <ItemCard key={item.id} item={item} view="list" />
              ))}
              {items.length < ids.length && <p className="px-4 py-2 text-sm text-ink-3">{ids.length - items.length} cited item(s) are archived or no longer in the library.</p>}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
