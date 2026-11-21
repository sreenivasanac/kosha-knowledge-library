import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it } from 'vitest';
import { OUTBOX } from '../src/config.ts';
import type { Envelope } from '../src/envelope.ts';
import { Outbox, backoffMs, isRetryable } from '../src/outbox.ts';
import type { PostResult } from '../src/outbox.ts';

const envelope = (n: number): Envelope => ({
  source: 'x',
  kind: 'bookmarks_page',
  meta: { url: `u${n}`, operation: 'Bookmarks', folder_id: null, folder_name: null, captured_at: '', client: 'test' },
  payload: { n },
});

let dbCount = 0;
/** An outbox on its own database, with a clock the test moves and a server the test scripts. */
function setup(answer: (e: Envelope) => PostResult) {
  const clock = { now: 1_000_000 };
  const sent: string[] = [];
  const server = { answer };
  const outbox = new Outbox(
    async (e) => {
      sent.push(e.meta.url);
      return server.answer(e);
    },
    () => clock.now,
    () => 0.5, // jitter factor exactly 1
    { ...OUTBOX, dbName: `test-outbox-${dbCount++}`, batchSize: 2 },
  );
  return { outbox, clock, sent, server };
}

const down: PostResult = { ok: false, status: null, error: 'offline' };

describe('backoff', () => {
  it('doubles per attempt and stops at the cap', () => {
    const mid = () => 0.5;
    expect(backoffMs(1, mid)).toBe(OUTBOX.backoffBaseMs);
    expect(backoffMs(2, mid)).toBe(OUTBOX.backoffBaseMs * 2);
    expect(backoffMs(3, mid)).toBe(OUTBOX.backoffBaseMs * 4);
    expect(backoffMs(50, mid)).toBe(OUTBOX.backoffMaxMs);
  });

  it('retries outages and auth problems but not envelopes the server refuses', () => {
    for (const status of [null, 500, 503, 429, 408, 401, 403]) expect(isRetryable(status)).toBe(true);
    for (const status of [400, 404, 413, 422]) expect(isRetryable(status)).toBe(false);
  });
});

describe('Outbox', () => {
  beforeEach(() => undefined);

  it('sends everything in order, across batches, and empties itself', async () => {
    const { outbox, sent } = setup(() => ({ ok: true }));
    for (let n = 1; n <= 5; n++) await outbox.add(envelope(n));
    await outbox.flush();
    expect(sent).toEqual(['u1', 'u2', 'u3', 'u4', 'u5']);
    expect(await outbox.counts()).toEqual({ waiting: 0, rejected: 0 });
  });

  it('keeps an envelope when the server is down and retries only after the backoff', async () => {
    const { outbox, clock, sent, server } = setup(() => down);
    await outbox.add(envelope(1));
    await outbox.flush();
    expect(sent).toEqual(['u1']);
    expect(await outbox.counts()).toEqual({ waiting: 1, rejected: 0 });

    await outbox.flush(); // too early: nothing is due
    expect(sent).toEqual(['u1']);

    clock.now += OUTBOX.backoffBaseMs; // first backoff has passed, the server is still down
    await outbox.flush();
    expect(sent).toEqual(['u1', 'u1']);

    clock.now += OUTBOX.backoffBaseMs; // second backoff is twice as long
    await outbox.flush();
    expect(sent).toEqual(['u1', 'u1']);

    clock.now += OUTBOX.backoffBaseMs;
    server.answer = () => ({ ok: true });
    await outbox.flush();
    expect(sent).toEqual(['u1', 'u1', 'u1']);
    expect(await outbox.counts()).toEqual({ waiting: 0, rejected: 0 });
  });

  it('stops the pass at the first outage instead of trying every envelope', async () => {
    const { outbox, sent } = setup(() => down);
    await outbox.add(envelope(1));
    await outbox.add(envelope(2));
    await outbox.flush();
    expect(sent).toEqual(['u1']);
    expect(await outbox.counts()).toEqual({ waiting: 2, rejected: 0 });
  });

  it('sets a refused envelope aside, keeps it, and carries on with the rest', async () => {
    const { outbox, sent } = setup((e) => (e.meta.url === 'u1' ? { ok: false, status: 422, error: 'bad' } : { ok: true }));
    await outbox.add(envelope(1));
    await outbox.add(envelope(2));
    await outbox.flush();
    await outbox.flush();
    expect(sent).toEqual(['u1', 'u2']); // u1 is not sent again
    expect(await outbox.counts()).toEqual({ waiting: 0, rejected: 1 });
  });

  it('survives a restart: a new Outbox on the same database sends what was left', async () => {
    const first = setup(() => down);
    await first.outbox.add(envelope(7));
    await first.outbox.flush();
    const sent: string[] = [];
    const second = new Outbox(async (e) => (sent.push(e.meta.url), { ok: true }), () => first.clock.now + OUTBOX.backoffMaxMs, () => 0.5, {
      ...OUTBOX,
      dbName: `test-outbox-${dbCount - 1}`,
    });
    await second.flush();
    expect(sent).toEqual(['u7']);
  });

  it('shares one flush between overlapping calls', async () => {
    const { outbox, sent } = setup(() => ({ ok: true }));
    await outbox.add(envelope(1));
    await Promise.all([outbox.flush(), outbox.flush(), outbox.flush()]);
    expect(sent).toEqual(['u1']);
  });
});
