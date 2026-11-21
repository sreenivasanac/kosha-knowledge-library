// "Remove from sources": takes the pending removals from Kosha, removes each
// at its source through the functions handed in, and reports every result.
// Everything outside (network, tabs, clock) is injected so the order, pacing
// and stop rules can be tested with fakes.

import { REMOVAL } from '../config.ts';
import { between } from '../util.ts';
import type { Rng } from '../util.ts';

export interface PendingRemoval {
  item_id: number | string;
  source: 'x' | 'linkedin';
  source_id: string;
  url: string;
}

export interface RemovalOutcome {
  status: 'removed' | 'already_gone' | 'failed';
  detail: string;
  /** Something unexpected is on screen (sign-in wall, captcha, unknown dialog): end the whole run. */
  abort?: boolean;
}

export interface RemovalProgress {
  status: 'running' | 'finished' | 'stopped' | 'aborted' | 'error';
  total: number;
  done: number;
  removed: number;
  already_gone: number;
  failed: number;
  /** LinkedIn items left for a later run because of the per-run cap. */
  deferred: number;
  /** Results Kosha did not accept; those items stay pending there. */
  unreported: number;
  note: string;
}

export interface RemovalDeps {
  fetchPending(): Promise<PendingRemoval[]>;
  remove(item: PendingRemoval): Promise<RemovalOutcome>;
  report(item: PendingRemoval, outcome: RemovalOutcome): Promise<void>;
  sleep(ms: number): Promise<void>;
  shouldStop(): boolean;
  onProgress(progress: RemovalProgress): void;
  rng?: Rng;
}

export async function runRemovals(deps: RemovalDeps, limits = REMOVAL): Promise<RemovalProgress> {
  const pending = await deps.fetchPending();
  const xItems = pending.filter((p) => p.source === 'x');
  const linkedinAll = pending.filter((p) => p.source === 'linkedin');
  const linkedinItems = linkedinAll.slice(0, limits.linkedin.maxPerRun);
  // X first: it is the lower-risk source, and a LinkedIn abort then costs nothing on X.
  const queue = [...xItems, ...linkedinItems];

  const progress: RemovalProgress = {
    status: 'running',
    total: queue.length,
    done: 0,
    removed: 0,
    already_gone: 0,
    failed: 0,
    deferred: linkedinAll.length - linkedinItems.length,
    unreported: 0,
    note: '',
  };
  deps.onProgress({ ...progress });

  for (let i = 0; i < queue.length; i++) {
    if (deps.shouldStop()) {
      progress.status = 'stopped';
      progress.note = 'Stopped by you';
      break;
    }
    const item = queue[i];
    let outcome: RemovalOutcome;
    try {
      outcome = await deps.remove(item);
    } catch (error) {
      outcome = { status: 'failed', detail: String(error) };
    }
    progress.done += 1;
    progress[outcome.status] += 1;
    try {
      await deps.report(item, outcome);
    } catch {
      progress.unreported += 1;
    }
    if (outcome.abort) {
      progress.status = 'aborted';
      progress.note = `Stopped at once: ${outcome.detail}`;
      break;
    }
    deps.onProgress({ ...progress });

    // Pause only between two items of the same source.
    const next = queue[i + 1];
    if (next && next.source === item.source) {
      await deps.sleep(between(limits[item.source].gapMs, deps.rng));
    }
  }

  if (progress.status === 'running') progress.status = 'finished';
  deps.onProgress({ ...progress });
  return progress;
}

/**
 * Removal may start only from the extension's own popup, or from a page of
 * the configured Kosha server. A content script (it has a tab) can not.
 */
export function isRemovalTriggerAllowed(
  sender: { id?: string; origin?: string; tab?: unknown },
  extension: { id: string; origin: string },
  serverOrigin: string | null,
  external: boolean,
): boolean {
  if (external) return serverOrigin !== null && sender.origin === serverOrigin;
  return sender.id === extension.id && sender.origin === extension.origin && !sender.tab;
}
