// Persistent outbox: every envelope is written to IndexedDB first and deleted
// only after the Kosha server accepts it, so a stopped server, a restarted
// service worker or a closed browser loses nothing.

import { OUTBOX } from './config.ts';
import type { Envelope } from './envelope.ts';
import type { Rng } from './util.ts';

const STORE = 'envelopes';

export interface OutboxRecord {
  id: number;
  envelope: Envelope;
  attempts: number;
  /** Epoch ms before which the record is not retried. */
  nextAttemptAt: number;
  /** The server refused this envelope itself; it is kept but no longer sent. */
  rejected: boolean;
  lastError: string | null;
}

export type PostResult = { ok: true } | { ok: false; status: number | null; error: string };

export interface OutboxCounts {
  waiting: number;
  rejected: number;
}

/** Delay before the next try: doubles per attempt up to a cap, with a little jitter. */
export function backoffMs(attempts: number, rng: Rng = Math.random, cfg = OUTBOX): number {
  const base = Math.min(cfg.backoffMaxMs, cfg.backoffBaseMs * 2 ** Math.max(0, attempts - 1));
  return Math.round(base * (0.8 + 0.4 * rng()));
}

/**
 * Worth sending again later? Yes for no connection, server errors, rate limits
 * and a wrong token (fixed in the popup). Other 4xx answers mean the server
 * will never take this envelope.
 */
export function isRetryable(status: number | null): boolean {
  return status === null || status >= 500 || [401, 403, 408, 429].includes(status);
}

/** The request's result once its transaction has committed: a request can succeed and the transaction still abort. */
function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const tx = req.transaction!;
    tx.oncomplete = () => resolve(req.result);
    tx.onabort = tx.onerror = () => reject(tx.error ?? req.error);
  });
}

export class Outbox {
  private db: Promise<IDBDatabase> | null = null;
  private flushing: Promise<void> | null = null;
  private post: (envelope: Envelope) => Promise<PostResult>;
  private now: () => number;
  private rng: Rng;
  private cfg: typeof OUTBOX;

  constructor(post: (envelope: Envelope) => Promise<PostResult>, now: () => number = Date.now, rng: Rng = Math.random, cfg = OUTBOX) {
    this.post = post;
    this.now = now;
    this.rng = rng;
    this.cfg = cfg;
  }

  private open(): Promise<IDBDatabase> {
    this.db ??= new Promise((resolve, reject) => {
      const req = indexedDB.open(this.cfg.dbName, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return this.db;
  }

  private async store(mode: IDBTransactionMode): Promise<IDBObjectStore> {
    return (await this.open()).transaction(STORE, mode).objectStore(STORE);
  }

  async add(envelope: Envelope): Promise<void> {
    const record: Omit<OutboxRecord, 'id'> = { envelope, attempts: 0, nextAttemptAt: 0, rejected: false, lastError: null };
    await request((await this.store('readwrite')).add(record));
  }

  /** Oldest records that are due, at most one batch. Read with a cursor so a large backlog is never loaded whole. */
  private async dueBatch(): Promise<OutboxRecord[]> {
    const store = await this.store('readonly');
    const now = this.now();
    return new Promise((resolve, reject) => {
      const due: OutboxRecord[] = [];
      const req = store.openCursor();
      req.onerror = () => reject(req.error);
      req.onsuccess = () => {
        const cursor = req.result;
        if (!cursor || due.length >= this.cfg.batchSize) return resolve(due);
        const record = cursor.value as OutboxRecord;
        if (!record.rejected && record.nextAttemptAt <= now) due.push(record);
        cursor.continue();
      };
    });
  }

  /** Send what is due, oldest first. Calls made while a flush runs share that flush. */
  flush(): Promise<void> {
    this.flushing ??= this.flushOnce().finally(() => {
      this.flushing = null;
    });
    return this.flushing;
  }

  private async flushOnce(): Promise<void> {
    for (;;) {
      const batch = await this.dueBatch();
      if (batch.length === 0) return;
      for (const record of batch) {
        const result = await this.post(record.envelope);
        if (result.ok) {
          await request((await this.store('readwrite')).delete(record.id));
          continue;
        }
        record.attempts += 1;
        record.lastError = result.error;
        if (isRetryable(result.status)) {
          record.nextAttemptAt = this.now() + backoffMs(record.attempts, this.rng, this.cfg);
          await request((await this.store('readwrite')).put(record));
          return; // the server is unreachable or refusing: leave the rest for the next flush
        }
        record.rejected = true;
        await request((await this.store('readwrite')).put(record));
      }
    }
  }

  async counts(): Promise<OutboxCounts> {
    // ponytail: reads every record to count; add an index on `rejected` if backlogs grow past a few thousand.
    const all = (await request((await this.store('readonly')).getAll())) as OutboxRecord[];
    const rejected = all.filter((r) => r.rejected).length;
    return { waiting: all.length - rejected, rejected };
  }
}
