/** The search bar: runs as you type, suggests qualifiers with counts, and shows the active ones as chips. */
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Bookmark, Clock, Search, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api, ApiError, useCapabilities, useSavedViews, type Suggest } from './api';
import { MODES, RECENT_SEARCHES_MAX, SEARCH_DEBOUNCE_MS, STORAGE_KEYS, type Mode } from './constants';
import { setSearch } from './nav';
import { applySuggestion, chipsOf, removeToken } from './query';
import { useHint } from './ui';

function recentSearches(): string[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEYS.recent) ?? '[]');
  } catch {
    return [];
  }
}
function rememberSearch(q: string) {
  if (!q.trim()) return;
  const next = [q.trim(), ...recentSearches().filter((r) => r !== q.trim())].slice(0, RECENT_SEARCHES_MAX);
  try {
    localStorage.setItem(STORAGE_KEYS.recent, JSON.stringify(next));
  } catch {
    /* private mode: recents just do not persist */
  }
}

interface Option {
  kind: string;
  label: string;
  count?: number | null;
  /** Replaces the whole bar (recent searches, saved views) or a range of it (suggestions). */
  apply: () => void;
}

/** Where the server said the query is wrong: the text with the offending part marked. */
function ErrorPosition({ text, error }: { text: string; error: ApiError }) {
  const at = Math.min(error.position ?? text.length, text.length);
  const end = text.indexOf(' ', at) < 0 ? text.length : text.indexOf(' ', at);
  return (
    <p role="alert" className="mt-1.5 text-sm text-danger">
      {error.message}
      {error.position != null && (
        <code className="ml-2 rounded bg-sunken px-1.5 py-0.5 text-ink-2">
          {text.slice(0, at)}
          <mark className="rounded-sm bg-danger/20 text-danger underline decoration-wavy">{text.slice(at, end) || ' '}</mark>
          {text.slice(end)}
        </code>
      )}
    </p>
  );
}

export function SearchBar({ q, mode, error, leftover }: { q: string; mode: Mode; error: unknown; leftover: string[] }) {
  const caps = useCapabilities();
  const hint = useHint();
  const views = useSavedViews();
  const input = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(q);
  const [caret, setCaret] = useState(q.length);
  const [focused, setFocused] = useState(false);
  const [active, setActive] = useState(-1);
  const pushed = useRef(q);

  // The URL changed from outside the bar (sidebar, chips, Back): show that query.
  useEffect(() => {
    if (q !== pushed.current) {
      pushed.current = q;
      setText(q);
    }
  }, [q]);

  // Search as you type: the URL (and so the result) follows after a short pause.
  useEffect(() => {
    const next = text.trim();
    if (next === pushed.current) return;
    const timer = setTimeout(() => {
      pushed.current = next;
      setSearch({ q: next, page: undefined, item: undefined, snap: undefined, jq: undefined }, true);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [text]);

  const suggest = useQuery({
    queryKey: ['suggest', text, caret],
    queryFn: () => api<Suggest>(`/api/search/suggest?${new URLSearchParams({ q: text, cursor: String(caret) })}`),
    enabled: focused && caps.suggest && text.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 10_000,
  });

  const replaceAll = (value: string) => () => {
    setText(value);
    setCaret(value.length);
  };
  let options: Option[] = [];
  if (focused && !text) {
    // An empty bar offers what was searched before and the saved views.
    options = [
      ...recentSearches().map((r) => ({ kind: 'recent', label: r, apply: replaceAll(r) })),
      ...views.map((v) => ({ kind: 'view', label: v.name, apply: replaceAll(v.query) })),
    ];
  } else if (focused && suggest.data) {
    const range = suggest.data.replace_range;
    options = suggest.data.suggestions.map((s) => ({
      kind: s.kind,
      label: s.label,
      count: s.count,
      apply: () => {
        const next = applySuggestion(text, range, s.insert);
        setText(next.text);
        setCaret(next.caret);
        requestAnimationFrame(() => input.current?.setSelectionRange(next.caret, next.caret));
      },
    }));
  }
  const open = focused && options.length > 0;
  // Suggestions still shown from the text or caret before the last keystroke carry that text's range: they wait for their own answer.
  const outdated = !!text && suggest.isPlaceholderData;
  const accept = (i: number) => {
    if (outdated) return;
    options[i]?.apply();
    setActive(-1);
  };

  const syntaxError = error instanceof ApiError && error.status === 422 ? error : null;

  return (
    <div className="min-w-0 flex-1">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search size={16} aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3" />
          <input
            id="search-input"
            ref={input}
            role="combobox"
            aria-expanded={open}
            aria-controls="search-options"
            aria-activedescendant={active >= 0 ? `search-option-${active}` : undefined}
            aria-invalid={!!syntaxError}
            aria-label="Search your library"
            title={hint('Search', 'search.focus')}
            placeholder="Search, or filter with from: #tag is:unread has:video …"
            autoComplete="off"
            spellCheck={false}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setCaret(e.target.selectionStart ?? e.target.value.length);
              setActive(-1);
            }}
            onSelect={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
            onFocus={() => setFocused(true)}
            onBlur={() => {
              setFocused(false);
              rememberSearch(text);
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown' && open) {
                e.preventDefault();
                setActive((active + 1) % options.length);
              }
              else if (e.key === 'ArrowUp' && open) {
                e.preventDefault();
                setActive((active - 1 + options.length) % options.length);
              }
              else if (e.key === 'Tab' && open && !e.shiftKey) {
                e.preventDefault();
                accept(Math.max(active, 0));
              }
              else if (e.key === 'Enter') {
                if (open && active >= 0) accept(active);
                else {
                  rememberSearch(text);
                  input.current?.blur();
                }
              }
            }}
            className={`field h-9 w-full pl-8 pr-8 text-[0.933rem] ${syntaxError ? 'border-danger' : ''}`}
          />
          {text && (
            <button aria-label="Clear the search" className="absolute right-1.5 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded text-ink-3 hover:bg-sunken" onClick={replaceAll('')}>
              <X size={14} />
            </button>
          )}
          {open && (
            <ul id="search-options" role="listbox" aria-busy={outdated} className={`absolute left-0 right-0 top-full z-30 mt-1 max-h-80 overflow-auto rounded-lg border border-line bg-surface py-1 shadow-pop ${outdated ? 'opacity-60' : ''}`}>
              {options.map((o, i) => (
                <li
                  key={i}
                  id={`search-option-${i}`}
                  role="option"
                  aria-selected={i === active}
                  // mousedown, not click: the input must not lose focus before the choice applies.
                  onMouseDown={(e) => (e.preventDefault(), accept(i))}
                  className={`flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm ${i === active ? 'bg-accent-soft' : ''}`}
                >
                  {o.kind === 'recent' ? <Clock size={13} aria-hidden className="text-ink-3" /> : o.kind === 'view' ? <Bookmark size={13} aria-hidden className="text-ink-3" /> : <span className="w-14 shrink-0 text-xs text-ink-3">{o.kind}</span>}
                  <span className="min-w-0 flex-1 truncate">{o.label}</span>
                  {o.count != null && <span className="text-xs tabular-nums text-ink-3">{o.count.toLocaleString()}</span>}
                </li>
              ))}
              <li aria-hidden className="border-t border-line px-3 pt-1.5 text-xs text-ink-3">
                <kbd className="kbd">Tab</kbd> or <kbd className="kbd">Enter</kbd> to accept, <kbd className="kbd">↑</kbd> <kbd className="kbd">↓</kbd> to move
              </li>
            </ul>
          )}
        </div>
        {/* A phone has no room for three buttons beside the input; the same choice as a native menu. */}
        <select aria-label="Search mode" className="field h-9 shrink-0 px-1.5 text-xs capitalize md:hidden" value={mode} onChange={(e) => setSearch({ mode: e.target.value as Mode, page: undefined })}>
          {MODES.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <div role="radiogroup" aria-label="Search mode" className="flex shrink-0 rounded-md border border-line bg-surface p-0.5 max-md:hidden">
          {MODES.map((m) => (
            <button key={m} role="radio" aria-checked={mode === m} onClick={() => setSearch({ mode: m, page: undefined })} className={`rounded px-2 py-1 text-xs font-medium capitalize ${mode === m ? 'bg-accent-soft text-accent' : 'text-ink-2 hover:text-ink'}`}>
              {m}
            </button>
          ))}
        </div>
      </div>
      {syntaxError && <ErrorPosition text={text} error={syntaxError} />}
      {focused && text && !caps.suggest && <p className="mt-1.5 text-xs text-ink-3">Suggestions are not available on this server yet.</p>}
      {leftover.length > 0 && (
        <p role="status" className="mt-1.5 text-sm text-ink-2">
          This server does not have search yet, so <code className="rounded bg-sunken px-1">{leftover.join(' ')}</code> is not applied. Filters that the sidebar offers still work.
        </p>
      )}
    </div>
  );
}

/** The active qualifiers as removable chips. They sit in the toolbar row, so adding one never moves the list. */
export function FilterChips({ q, jevQuestion }: { q: string; jevQuestion?: string }) {
  const chips = chipsOf(q);
  const reset = { page: undefined, item: undefined };
  return (
    <ul aria-label="Active filters" className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto py-1 max-sm:order-last max-sm:basis-full max-sm:empty:hidden">
      {chips.map((chip) => (
        <li key={chip.start} className="inline-flex shrink-0 items-center rounded-md bg-accent-soft pl-2 text-sm text-accent">
          {chip.raw}
          <button aria-label={`Remove ${chip.raw}`} className="ml-0.5 grid h-6 w-6 place-items-center rounded-md hover:bg-accent/15" onClick={() => setSearch({ q: removeToken(q, chip), ...reset })}>
            <X size={12} />
          </button>
        </li>
      ))}
      {jevQuestion && (
        <li className="inline-flex shrink-0 items-center rounded-md border border-accent/40 pl-2 text-sm text-accent">
          Jev: {jevQuestion}
          <button aria-label="Remove the Jev check" className="ml-0.5 grid h-6 w-6 place-items-center rounded-md hover:bg-accent/15" onClick={() => setSearch({ snap: undefined, jq: undefined, ...reset })}>
            <X size={12} />
          </button>
        </li>
      )}
    </ul>
  );
}
