/**
 * Pipeline console (`g q`): the query language of docs/MCP.md, typed directly.
 * It posts to the same endpoint the MCP tool uses, so the server's safety rules
 * apply unchanged: a write over 25 items comes back as a preview with a confirm
 * token, and nothing is written until that token is sent back.
 */
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { undo } from './actions';
import { api, ApiError, invalidateLibrary, useCapabilities } from './api';
import { PIPELINE_HISTORY_MAX, STORAGE_KEYS } from './constants';
import { usd } from './format';
import { pushHistory, stepHistory } from './history';
import { PageHeader } from './TagsPage';
import { IS_MAC, Unavailable } from './ui';

interface QueryReply {
  text: string;
  n?: number | null;
  rows?: Record<string, unknown>[] | null;
  spent_usd?: number | null;
  /** Set on a write preview: send the same pipeline again with it to apply. */
  confirm?: string | null;
  /** Set when a write was applied: the undo batch. */
  batch_id?: number | null;
}
interface Entry {
  id: number;
  pipeline: string;
  reply?: QueryReply;
  error?: { message: string; position?: number };
  /** The preview was confirmed (or replaced by a newer run), so its button is gone. */
  settled?: boolean;
}

const EXAMPLES = ['help', 'all | count by source', 'where is:untagged | count by author top 10', 'where from:simonw | head 5 | show id date text:60'];

function loadHistory(): string[] {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(STORAGE_KEYS.pipelineHistory) ?? '[]');
    return Array.isArray(stored) ? stored.filter((h): h is string => typeof h === 'string') : [];
  } catch {
    return [];
  }
}

export function ConsolePage() {
  const caps = useCapabilities();
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [text, setText] = useState('');
  const [history, setHistory] = useState(loadHistory);
  /** Position in the history while walking it with the arrows; null while typing a new line. */
  const [at, setAt] = useState<number | null>(null);
  /** What was being typed before ArrowUp, to come back to. */
  const draft = useRef('');
  const [entries, setEntries] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(false);
  const nextId = useRef(0);
  const pending = entries.find((e) => e.reply?.confirm && !e.settled);

  useEffect(() => input.current?.focus(), []);

  async function run(pipeline: string, confirm?: string) {
    const q = pipeline.trim();
    if (!q || busy) return;
    const next = pushHistory(history, q, PIPELINE_HISTORY_MAX);
    setHistory(next);
    try {
      localStorage.setItem(STORAGE_KEYS.pipelineHistory, JSON.stringify(next));
    } catch {
      /* private mode: history lasts until the tab closes */
    }
    setAt(null);
    setText('');
    setBusy(true);
    const id = ++nextId.current;
    let entry: Entry;
    try {
      const reply = await api<QueryReply>('/api/query', { method: 'POST', body: { q, confirm: confirm ?? null } });
      entry = { id, pipeline: q, reply };
      // A write went through: lists and counts elsewhere are stale.
      if (reply.batch_id != null || confirm) invalidateLibrary();
    } catch (e) {
      entry = { id, pipeline: q, error: { message: e instanceof Error ? e.message : String(e), position: e instanceof ApiError ? e.position : undefined } };
    }
    // Newest first; an older preview can no longer be confirmed once something else ran.
    setEntries((old) => [entry, ...old.map((o) => ({ ...o, settled: true }))]);
    setBusy(false);
    input.current?.focus();
  }

  function walk(delta: -1 | 1) {
    const next = stepHistory(history.length, at, delta);
    if (at == null) draft.current = text;
    setAt(next);
    setText(next == null ? draft.current : history[next]);
  }

  const openRows = (rows: Record<string, unknown>[]) => {
    const ids = rows.map((r) => Number(r.id)).filter((n) => Number.isInteger(n) && n > 0);
    void navigate({ to: '/', search: { q: `id:${ids.join(',')}` } });
  };

  return (
    <div className="mx-auto max-w-[1100px] px-3 py-4 sm:px-5">
      <PageHeader title="Pipeline console">
        <button className="btn" disabled={!caps.query || busy} onClick={() => void run('help')} title="Prints the grammar the server accepts">
          Grammar
        </button>
      </PageHeader>
      {!caps.query && <Unavailable what="The pipeline endpoint" />}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run(text);
        }}
      >
        <div className="flex items-center gap-2 rounded-lg border border-line bg-surface px-3 focus-within:border-accent">
          <span aria-hidden className="font-mono text-ink-3">
            ›
          </span>
          <input
            ref={input}
            aria-label="Pipeline"
            placeholder="where from:simonw | grep ~sqlite | head 20 | show id summary:80"
            autoComplete="off"
            spellCheck={false}
            autoCapitalize="off"
            value={text}
            disabled={!caps.query}
            onChange={(e) => (setText(e.target.value), setAt(null))}
            onKeyDown={(e) => {
              if (e.key === 'ArrowUp') {
                e.preventDefault();
                walk(-1);
              }
              else if (e.key === 'ArrowDown') {
                e.preventDefault();
                walk(1);
              }
              // Confirming a write takes a deliberate chord, never a second plain Enter.
              else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && pending) {
                e.preventDefault();
                void run(pending.pipeline, pending.reply!.confirm!);
              }
            }}
            className="h-11 min-w-0 flex-1 bg-transparent font-mono text-sm outline-none placeholder:text-ink-3"
          />
          <button className="btn btn-primary h-8" disabled={busy || !text.trim() || !caps.query}>
            {busy ? 'Running…' : 'Run'}
          </button>
        </div>
      </form>
      <p className="mt-1.5 text-xs text-ink-3">
        <kbd className="kbd">Enter</kbd> runs, <kbd className="kbd">↑</kbd> <kbd className="kbd">↓</kbd> walk the history ({history.length}). A source, then stages joined by <code className="font-mono">|</code>. Writes over 25 items return a preview first.
      </p>

      {entries.length === 0 ? (
        <ul className="mt-5 space-y-1 text-sm" aria-label="Examples">
          {EXAMPLES.map((example) => (
            <li key={example}>
              <button className="rounded px-1.5 py-0.5 font-mono text-accent hover:bg-accent-soft" onClick={() => (setText(example), input.current?.focus())}>
                {example}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <ol className="mt-4 space-y-3" aria-label="Replies, newest first" aria-live="polite">
          {entries.map((e) => (
            <li key={e.id} className="overflow-hidden rounded-lg border border-line bg-surface">
              <div className="flex items-center gap-2 border-b border-line bg-sunken/50 px-3 py-1.5">
                <button className="min-w-0 flex-1 truncate text-left font-mono text-sm" title="Put this pipeline back in the input" onClick={() => (setText(e.pipeline), input.current?.focus())}>
                  <span className="text-ink-3">› </span>
                  {e.pipeline}
                </button>
                {e.reply?.spent_usd != null && e.reply.spent_usd > 0 && <span className="shrink-0 text-xs tabular-nums text-ink-3">{usd(e.reply.spent_usd)}</span>}
              </div>
              {e.error ? (
                <div role="alert" className="px-3 py-2 text-sm text-danger">
                  {e.error.message}
                  {e.error.position != null && (
                    // The caret sits under the character the server could not read.
                    <pre className="mt-1 overflow-x-auto font-mono text-xs text-ink-2">
                      {e.pipeline}
                      {'\n'}
                      {' '.repeat(e.error.position)}^
                    </pre>
                  )}
                </div>
              ) : (
                <>
                  {/* The reply exactly as the server (and the MCP tool) returns it. */}
                  <pre className="overflow-x-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[0.8rem] leading-snug">{e.reply!.text}</pre>
                  {(e.reply!.confirm || e.reply!.batch_id != null || !!e.reply!.rows?.some((r) => r.id != null)) && (
                    <div className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-2 text-sm">
                      {e.reply!.confirm &&
                        (e.settled ? (
                          <span className="text-ink-3">Preview only; nothing was written by this call.</span>
                        ) : (
                          <>
                            <button className="btn btn-primary" disabled={busy} onClick={() => void run(e.pipeline, e.reply!.confirm!)}>
                              Confirm and write
                            </button>
                            <span className="text-ink-3">
                              Nothing is written yet. <kbd className="kbd">{IS_MAC ? '⌘' : 'Ctrl+'}Enter</kbd> confirms; the token lasts 10 minutes.
                            </span>
                          </>
                        ))}
                      {e.reply!.batch_id != null && (
                        <button className="btn" onClick={() => void undo(e.reply!.batch_id!)}>
                          Undo this write
                        </button>
                      )}
                      {!!e.reply!.rows?.some((r) => r.id != null) && (
                        <button className="btn" onClick={() => openRows(e.reply!.rows!)}>
                          Open these {e.reply!.rows!.length} in the library
                        </button>
                      )}
                    </div>
                  )}
                </>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
