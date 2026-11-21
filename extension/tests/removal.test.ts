import { describe, expect, it } from 'vitest';
import { REMOVAL } from '../src/config.ts';
import { isRemovalTriggerAllowed, runRemovals } from '../src/removal/queue.ts';
import type { PendingRemoval, RemovalOutcome, RemovalProgress } from '../src/removal/queue.ts';

const item = (n: number, source: 'x' | 'linkedin'): PendingRemoval => ({ item_id: n, source, source_id: String(n), url: `https://example.test/${n}` });
const removed: RemovalOutcome = { status: 'removed', detail: 'ok' };

/** Fakes for everything outside the queue; `log` records the order of what happened. */
function fakes(pending: PendingRemoval[], outcome: (i: PendingRemoval) => RemovalOutcome | Promise<RemovalOutcome> = () => removed) {
  const log: string[] = [];
  const sleeps: { after: number | string; ms: number }[] = [];
  const reports: { item_id: number | string; status: string }[] = [];
  const progress: RemovalProgress[] = [];
  let last: number | string = '';
  let stop = false;
  return {
    log,
    sleeps,
    reports,
    progress,
    requestStop: () => (stop = true),
    deps: {
      fetchPending: async () => pending,
      remove: async (i: PendingRemoval) => {
        log.push(`remove ${i.source} ${i.item_id}`);
        last = i.item_id;
        return outcome(i);
      },
      report: async (i: PendingRemoval, o: RemovalOutcome) => {
        log.push(`report ${i.item_id} ${o.status}`);
        reports.push({ item_id: i.item_id, status: o.status });
      },
      sleep: async (ms: number) => void sleeps.push({ after: last, ms }),
      shouldStop: () => stop,
      onProgress: (p: RemovalProgress) => void progress.push(p),
    },
  };
}

describe('runRemovals', () => {
  it('removes X items first, reports each result, and counts them', async () => {
    const f = fakes([item(1, 'linkedin'), item(2, 'x'), item(3, 'x')], (i) =>
      i.item_id === 3 ? { status: 'already_gone', detail: '' } : removed,
    );
    const result = await runRemovals(f.deps);
    expect(f.log).toEqual(['remove x 2', 'report 2 removed', 'remove x 3', 'report 3 already_gone', 'remove linkedin 1', 'report 1 removed']);
    expect(result).toMatchObject({ status: 'finished', total: 3, done: 3, removed: 2, already_gone: 1, failed: 0 });
    expect(f.progress.at(-1)).toEqual(result);
  });

  it('paces X a few seconds apart and LinkedIn 8 to 20 seconds apart, with no wait after the last', async () => {
    const f = fakes([item(1, 'x'), item(2, 'x'), item(3, 'x'), item(4, 'linkedin'), item(5, 'linkedin'), item(6, 'linkedin')]);
    await runRemovals(f.deps);
    expect(f.sleeps.map((s) => s.after)).toEqual([1, 2, 4, 5]); // none after 3 (source changes) or 6 (end)
    for (const s of f.sleeps.slice(0, 2)) {
      expect(s.ms).toBeGreaterThanOrEqual(REMOVAL.x.gapMs[0]);
      expect(s.ms).toBeLessThanOrEqual(REMOVAL.x.gapMs[1]);
    }
    for (const s of f.sleeps.slice(2)) {
      expect(s.ms).toBeGreaterThanOrEqual(8000);
      expect(s.ms).toBeLessThanOrEqual(20_000);
    }
  });

  it('varies the gaps', async () => {
    const f = fakes(Array.from({ length: 12 }, (_, n) => item(n, 'linkedin')));
    await runRemovals(f.deps);
    expect(new Set(f.sleeps.map((s) => Math.round(s.ms))).size).toBeGreaterThan(1);
  });

  it('does at most 40 LinkedIn removals in a run and says how many are left', async () => {
    const f = fakes(Array.from({ length: 55 }, (_, n) => item(n, 'linkedin')));
    const result = await runRemovals(f.deps);
    expect(REMOVAL.linkedin.maxPerRun).toBe(40);
    expect(result).toMatchObject({ total: 40, done: 40, deferred: 15 });
    expect(f.reports).toHaveLength(40);
  });

  it('stops at once on an unexpected dialog: the item is reported failed and nothing after it is touched', async () => {
    const f = fakes([item(1, 'linkedin'), item(2, 'linkedin'), item(3, 'linkedin')], (i) =>
      i.item_id === 2 ? { status: 'failed', detail: 'unexpected dialog', abort: true } : removed,
    );
    const result = await runRemovals(f.deps);
    expect(f.log).toEqual(['remove linkedin 1', 'report 1 removed', 'remove linkedin 2', 'report 2 failed']);
    expect(result).toMatchObject({ status: 'aborted', done: 2, removed: 1, failed: 1 });
    expect(f.sleeps).toHaveLength(1); // no wait after the abort
  });

  it('stops between items when asked', async () => {
    const f = fakes([item(1, 'x'), item(2, 'x'), item(3, 'x')]);
    f.deps.sleep = async () => void f.requestStop();
    const result = await runRemovals(f.deps);
    expect(f.reports).toEqual([{ item_id: 1, status: 'removed' }]);
    expect(result.status).toBe('stopped');
  });

  it('reports a removal that throws as failed and carries on', async () => {
    const f = fakes([item(1, 'x'), item(2, 'x')], (i) => {
      if (i.item_id === 1) throw new Error('tab closed');
      return removed;
    });
    const result = await runRemovals(f.deps);
    expect(f.reports).toEqual([{ item_id: 1, status: 'failed' }, { item_id: 2, status: 'removed' }]);
    expect(result.status).toBe('finished');
  });

  it('counts results Kosha did not accept and still finishes', async () => {
    const f = fakes([item(1, 'x'), item(2, 'x')]);
    f.deps.report = async () => {
      throw new Error('HTTP 500');
    };
    expect(await runRemovals(f.deps)).toMatchObject({ status: 'finished', done: 2, unreported: 2 });
  });

  it('does nothing when nothing is pending', async () => {
    const f = fakes([]);
    expect(await runRemovals(f.deps)).toMatchObject({ status: 'finished', total: 0, done: 0 });
    expect(f.log).toEqual([]);
  });
});

describe('who may start a removal', () => {
  const extension = { id: 'abc', origin: 'chrome-extension://abc' };
  const server = 'http://localhost:8000';

  it('allows the popup and refuses content scripts and other extensions', () => {
    expect(isRemovalTriggerAllowed({ id: 'abc', origin: 'chrome-extension://abc' }, extension, server, false)).toBe(true);
    expect(isRemovalTriggerAllowed({ id: 'abc', origin: 'https://x.com', tab: { id: 1 } }, extension, server, false)).toBe(false);
    expect(isRemovalTriggerAllowed({ id: 'abc', origin: 'https://www.linkedin.com', tab: { id: 2 } }, extension, server, false)).toBe(false);
    expect(isRemovalTriggerAllowed({ id: 'other', origin: 'chrome-extension://other' }, extension, server, false)).toBe(false);
  });

  it('allows a web page only from the configured Kosha server origin', () => {
    expect(isRemovalTriggerAllowed({ origin: 'http://localhost:8000', tab: {} }, extension, server, true)).toBe(true);
    expect(isRemovalTriggerAllowed({ origin: 'http://localhost:3000', tab: {} }, extension, server, true)).toBe(false);
    expect(isRemovalTriggerAllowed({ origin: 'http://127.0.0.1:8000', tab: {} }, extension, server, true)).toBe(false);
    expect(isRemovalTriggerAllowed({ origin: 'http://localhost:8000', tab: {} }, extension, null, true)).toBe(false);
  });
});
