// Backfill: the pacing and stop rules (BackfillTracker), the saved progress
// (BackfillState in chrome.storage.local), and which page the backfill should
// be on. No DOM here; src/x/content.ts does the scrolling.

import { BACKFILL_PACING, STORAGE, X } from '../config.ts';
import { between, intBetween } from '../util.ts';
import type { Rng } from '../util.ts';
import { folderIdFromPath } from './match.ts';
import type { XFolder } from './match.ts';

export interface Step {
  /** scroll / scroll_up: wait, then scroll. rest / backoff: only wait. stop: this timeline is finished. */
  kind: 'scroll' | 'scroll_up' | 'rest' | 'backoff' | 'stop';
  waitMs: number;
  reason?: string;
}

/** Decides the next step of one timeline's backfill from what X has answered so far. */
export class BackfillTracker {
  pages = 0;
  posts = 0;
  private emptyResponses = 0;
  private idleScrolls = 0;
  private rateLimited = false;
  private nextRestAt: number;
  private rng: Rng;
  private pacing: typeof BACKFILL_PACING;

  constructor(rng: Rng = Math.random, pacing = BACKFILL_PACING) {
    this.rng = rng;
    this.pacing = pacing;
    this.nextRestAt = intBetween(pacing.restEveryPages, rng);
  }

  /** Record one bookmark timeline response. */
  onResponse(status: number, postCount: number): void {
    if (status === 429) {
      this.rateLimited = true;
      return;
    }
    if (status !== 200) return;
    this.pages += 1;
    this.posts += postCount;
    if (postCount === 0) {
      this.emptyResponses += 1;
    } else {
      this.emptyResponses = 0;
      this.idleScrolls = 0;
    }
  }

  next(): Step {
    const p = this.pacing;
    if (this.rateLimited) {
      this.rateLimited = false;
      return { kind: 'backoff', waitMs: between(p.rateLimitBackoffMs, this.rng), reason: 'X answered "too many requests"' };
    }
    if (this.emptyResponses >= p.stopAfterEmptyResponses) {
      return { kind: 'stop', waitMs: 0, reason: `${p.stopAfterEmptyResponses} responses with no posts` };
    }
    if (this.idleScrolls >= p.stopAfterIdleScrolls) {
      return { kind: 'stop', waitMs: 0, reason: `${p.stopAfterIdleScrolls} scrolls with nothing new` };
    }
    if (this.pages >= this.nextRestAt) {
      this.nextRestAt = this.pages + intBetween(p.restEveryPages, this.rng);
      return { kind: 'rest', waitMs: between(p.restMs, this.rng) };
    }
    const waitMs = between(p.scrollGapMs, this.rng);
    if (this.rng() < p.scrollUpChance) return { kind: 'scroll_up', waitMs };
    this.idleScrolls += 1; // only downward scrolls count towards "nothing new"
    return { kind: 'scroll', waitMs };
  }
}

export interface BackfillState {
  status: 'idle' | 'running' | 'paused' | 'stopped' | 'done' | 'error';
  /** The tab doing the scrolling; other x.com tabs only capture passively. */
  tabId: number | null;
  phase: 'main' | 'folders';
  folders: XFolder[];
  folderIndex: number;
  pages: number;
  posts: number;
  /** Target the tab was last sent to, so a page X refuses is skipped and not reloaded forever. */
  navigatedFor: string | null;
  note: string;
}

export const IDLE_BACKFILL: BackfillState = {
  status: 'idle',
  tabId: null,
  phase: 'main',
  folders: [],
  folderIndex: 0,
  pages: 0,
  posts: 0,
  navigatedFor: null,
  note: '',
};

export interface BackfillTarget {
  /** 'main', or the folder id. */
  key: string;
  url: string;
  folder: XFolder | null;
}

/** The page the backfill should be scrolling now; null when every folder is done. */
export function backfillTarget(state: BackfillState): BackfillTarget | null {
  if (state.phase === 'main') return { key: 'main', url: X.bookmarksUrl, folder: null };
  const folder = state.folders[state.folderIndex];
  return folder ? { key: folder.id, url: X.folderUrl(folder.id), folder } : null;
}

export function isOnTarget(pathname: string, target: BackfillTarget): boolean {
  return target.folder ? folderIdFromPath(pathname) === target.folder.id : X.bookmarksRootPath.test(pathname);
}

export async function readBackfill(): Promise<BackfillState> {
  const stored = (await chrome.storage.local.get(STORAGE.backfill))[STORAGE.backfill];
  return { ...IDLE_BACKFILL, ...(stored as Partial<BackfillState> | undefined) };
}

export async function patchBackfill(patch: Partial<BackfillState>): Promise<BackfillState> {
  const next = { ...(await readBackfill()), ...patch };
  await chrome.storage.local.set({ [STORAGE.backfill]: next });
  return next;
}

/** Fresh progress for a new run in the given tab. The caller then loads the bookmarks page in it. */
export function startBackfill(tabId: number): Promise<BackfillState> {
  return patchBackfill({ ...IDLE_BACKFILL, status: 'running', tabId, navigatedFor: 'main', note: 'Starting' });
}
