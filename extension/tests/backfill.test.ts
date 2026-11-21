import { describe, expect, it } from 'vitest';
import { BACKFILL_PACING as P } from '../src/config.ts';
import { BackfillTracker, IDLE_BACKFILL, backfillTarget, isOnTarget } from '../src/x/backfill.ts';
import type { BackfillState } from '../src/x/backfill.ts';

/** rng that never picks a scroll-up (1 is above every chance) and takes the top of each range. */
const high = () => 0.999;
/** rng that takes the bottom of each range, and so always picks a scroll-up. */
const low = () => 0;

describe('BackfillTracker pacing', () => {
  it('waits between 2.5 and 6 seconds before each scroll', () => {
    for (const rng of [high, () => 0.5, Math.random]) {
      const tracker = new BackfillTracker(rng);
      for (let i = 0; i < 20; i++) {
        const step = tracker.next();
        if (step.kind === 'stop') break;
        expect(step.waitMs).toBeGreaterThanOrEqual(P.scrollGapMs[0]);
        expect(step.waitMs).toBeLessThanOrEqual(P.scrollGapMs[1]);
        tracker.onResponse(200, 20);
      }
    }
  });

  it('sometimes scrolls up a little, and a scroll-up does not count as an idle scroll', () => {
    const tracker = new BackfillTracker(low);
    for (let i = 0; i < P.stopAfterIdleScrolls + 5; i++) expect(tracker.next().kind).toBe('scroll_up');
  });

  it('rests 20 to 60 seconds once every 20 to 35 pages', () => {
    const tracker = new BackfillTracker(high); // rest falls due at the top of the range: 35 pages
    const restsAt: number[] = [];
    for (let page = 1; page <= 80; page++) {
      tracker.onResponse(200, 20);
      const step = tracker.next();
      if (step.kind === 'rest') {
        restsAt.push(page);
        expect(step.waitMs).toBeGreaterThanOrEqual(P.restMs[0]);
        expect(step.waitMs).toBeLessThanOrEqual(P.restMs[1]);
      }
    }
    expect(restsAt).toEqual([35, 70]);

    const early = new BackfillTracker(low); // bottom of the range: 20 pages
    for (let page = 1; page < 20; page++) early.onResponse(200, 20);
    expect(early.next().kind).not.toBe('rest');
    early.onResponse(200, 20);
    expect(early.next().kind).toBe('rest');
  });

  it('backs off 6 to 12 minutes after HTTP 429, once, then carries on', () => {
    const tracker = new BackfillTracker(high);
    tracker.onResponse(429, 0);
    const step = tracker.next();
    expect(step.kind).toBe('backoff');
    expect(step.waitMs).toBeGreaterThanOrEqual(6 * 60_000);
    expect(step.waitMs).toBeLessThanOrEqual(12 * 60_000);
    expect(tracker.pages).toBe(0); // a 429 is not a page
    expect(tracker.next().kind).toBe('scroll');
  });
});

describe('BackfillTracker stop rules', () => {
  it('stops after two responses in a row with no posts', () => {
    const tracker = new BackfillTracker(high);
    tracker.onResponse(200, 20);
    tracker.onResponse(200, 0);
    expect(tracker.next().kind).toBe('scroll');
    tracker.onResponse(200, 0);
    expect(tracker.next().kind).toBe('stop');
  });

  it('starts the count again when posts arrive in between', () => {
    const tracker = new BackfillTracker(high);
    tracker.onResponse(200, 0);
    tracker.onResponse(200, 3);
    tracker.onResponse(200, 0);
    expect(tracker.next().kind).toBe('scroll');
  });

  it('stops after 30 scrolls with nothing new, and new posts reset that count', () => {
    const tracker = new BackfillTracker(high);
    for (let i = 0; i < P.stopAfterIdleScrolls - 1; i++) expect(tracker.next().kind).toBe('scroll');
    tracker.onResponse(200, 20);
    for (let i = 0; i < P.stopAfterIdleScrolls; i++) expect(tracker.next().kind).toBe('scroll');
    expect(tracker.next().kind).toBe('stop');
  });

  it('ignores failed responses other than 429', () => {
    const tracker = new BackfillTracker(high);
    tracker.onResponse(500, 0);
    tracker.onResponse(500, 0);
    expect(tracker.pages).toBe(0);
    expect(tracker.next().kind).toBe('scroll');
  });
});

describe('where the backfill should be', () => {
  const folders = [{ id: '11', name: 'AI' }, { id: '12', name: 'Design' }];
  const state = (patch: Partial<BackfillState>): BackfillState => ({ ...IDLE_BACKFILL, ...patch });

  it('starts on the bookmarks page, under either address X uses', () => {
    const target = backfillTarget(state({ phase: 'main' }))!;
    expect(target.key).toBe('main');
    expect(isOnTarget('/i/bookmarks', target)).toBe(true);
    expect(isOnTarget('/i/history', target)).toBe(true);
    expect(isOnTarget('/i/bookmarks/11', target)).toBe(false);
    expect(isOnTarget('/home', target)).toBe(false);
  });

  it('then walks the folders in order and ends after the last', () => {
    const second = backfillTarget(state({ phase: 'folders', folders, folderIndex: 1 }))!;
    expect(second.url).toBe('https://x.com/i/bookmarks/12');
    expect(isOnTarget('/i/bookmarks/12', second)).toBe(true);
    expect(isOnTarget('/i/bookmarks/11', second)).toBe(false);
    expect(backfillTarget(state({ phase: 'folders', folders, folderIndex: 2 }))).toBeNull();
    expect(backfillTarget(state({ phase: 'folders', folders: [], folderIndex: 0 }))).toBeNull();
  });
});
