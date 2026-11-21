/** Command history of the pipeline console: newest last, as a shell keeps it. Pure, so it can be tested. */

/** Adds an entry; a repeat moves to the end instead of appearing twice, and the oldest fall off past `max`. */
export function pushHistory(history: string[], entry: string, max: number): string[] {
  const text = entry.trim();
  if (!text) return history;
  return [...history.filter((h) => h !== text), text].slice(-max);
}

/**
 * Where ArrowUp (`-1`) or ArrowDown (`+1`) lands. `at` is an index into the
 * history, or null while the input holds the draft being typed. Up from the
 * draft goes to the newest entry; Down past the newest returns to the draft.
 */
export function stepHistory(length: number, at: number | null, delta: -1 | 1): number | null {
  if (!length) return null;
  if (at == null) return delta < 0 ? length - 1 : null;
  const next = at + delta;
  if (next >= length) return null;
  return Math.max(0, next);
}
