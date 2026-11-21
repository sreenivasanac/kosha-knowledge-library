/** Archive page extras: "Remove from sources" through the browser extension, and permanent delete. */
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api, invalidateLibrary, useCapabilities } from './api';
import { REMOVAL_POLL_MS, STORAGE_KEYS } from './constants';
import { openOverlay, setState, toast, toastError } from './store';
import { Unavailable } from './ui';

/** Messages the extension accepts from this page (extension/src/config.ts, EXTERNAL_MESSAGES). */
const MESSAGES = { ping: 'kosha:ping', remove: 'kosha:remove_from_sources', status: 'kosha:removal_status' };

interface RemovalProgress {
  status: 'running' | 'done' | 'error' | string;
  total: number;
  done: number;
  removed: number;
  already_gone: number;
  failed: number;
  deferred?: number;
  unreported?: number;
  note?: string;
}
interface ExtensionReply {
  ok: boolean;
  detail?: string;
  version?: string;
  removal?: RemovalProgress | null;
}

export const getExtensionId = () => {
  try {
    return localStorage.getItem(STORAGE_KEYS.extensionId) ?? '';
  } catch {
    return '';
  }
};

/** One message to the extension; resolves to null when it is not installed, not reachable or the id is wrong. */
export function askExtension(type: string): Promise<ExtensionReply | null> {
  const runtime = (window as unknown as { chrome?: { runtime?: { sendMessage?: (id: string, msg: unknown, cb: (r: ExtensionReply) => void) => void; lastError?: unknown } } }).chrome?.runtime;
  const id = getExtensionId();
  if (!runtime?.sendMessage || !id) return Promise.resolve(null);
  return new Promise((resolve) => {
    try {
      runtime.sendMessage!(id, { type }, (reply) => resolve(runtime.lastError ? null : (reply ?? null)));
    } catch {
      resolve(null);
    }
  });
}

/** Deletes for good, after an explicit confirmation. */
export function deleteForever(ids: number[]) {
  if (!ids.length) return;
  openOverlay({
    kind: 'confirm',
    danger: true,
    title: `Delete ${ids.length} item${ids.length === 1 ? '' : 's'} permanently?`,
    body: 'This removes them and their stored files from Kosha. A later sync will not bring them back. It cannot be undone.',
    action: 'Delete permanently',
    run: async () => {
      try {
        for (const id of ids) await api(`/api/items/${id}`, { method: 'DELETE' });
        toast(`Deleted ${ids.length} item${ids.length === 1 ? '' : 's'}`);
      } catch (error) {
        toastError(error);
      }
      setState({ selection: new Set() });
      invalidateLibrary();
    },
  });
}

export function ArchiveTools() {
  const caps = useCapabilities();
  const pending = useQuery({ queryKey: ['removals-pending'], queryFn: () => api<unknown>('/api/removals/pending'), enabled: caps.removals });
  const ping = useQuery({ queryKey: ['extension-ping'], queryFn: () => askExtension(MESSAGES.ping), staleTime: 0 });
  const [progress, setProgress] = useState<RemovalProgress | null>(null);
  const running = progress?.status === 'running';

  // While a run is going, ask the extension how far it is.
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(async () => {
      const reply = await askExtension(MESSAGES.status);
      if (reply?.removal) setProgress(reply.removal);
      if (reply?.removal && reply.removal.status !== 'running') invalidateLibrary();
    }, REMOVAL_POLL_MS);
    return () => clearInterval(timer);
  }, [running]);

  // The shape is the backend's to decide: a list, {items: [...]} or {count}.
  const p = pending.data as { length?: number; items?: unknown[]; count?: number } | undefined;
  const waiting = Array.isArray(p) ? p.length : (p?.items?.length ?? p?.count);
  const connected = !!ping.data?.ok;

  async function start() {
    const reply = await askExtension(MESSAGES.remove);
    if (!reply?.ok) return toast(reply?.detail ?? 'The extension did not answer', { error: true });
    setProgress({ status: 'running', total: waiting ?? 0, done: 0, removed: 0, already_gone: 0, failed: 0 });
  }

  return (
    <section aria-label="Remove from sources" className="mb-4 max-w-[680px] space-y-2 rounded-lg border border-line bg-surface p-4">
      <h2 className="font-semibold">Remove from sources</h2>
      <p className="text-sm text-ink-2">Un-bookmarks the archived items at X and LinkedIn through the browser extension, a few seconds apart. Kosha deletes its own copy only for items the extension confirms.</p>
      {!caps.removals && <Unavailable what="Removal at the source" />}
      <div className="flex flex-wrap items-center gap-3">
        <button
          className="btn btn-danger"
          disabled={!connected || !caps.removals || running || waiting === 0}
          onClick={() => openOverlay({ kind: 'confirm', danger: true, title: 'Remove archived items from their sources?', body: `${waiting ?? 'The'} archived item${waiting === 1 ? '' : 's'} will be un-bookmarked at the source, then deleted from Kosha once confirmed.`, action: 'Remove from sources', run: start })}
        >
          Remove from sources
        </button>
        <span className="text-sm text-ink-2" role="status">
          {connected ? `Extension connected (version ${ping.data?.version ?? '?'})` : `Extension not connected${getExtensionId() ? '' : ': set its id in Settings'}`}
          {waiting != null && ` · ${waiting} waiting`}
        </span>
      </div>
      {progress && (
        <div className="space-y-1.5 text-sm" role="status" aria-live="polite">
          <progress className="h-2 w-full accent-(--accent)" max={Math.max(progress.total, 1)} value={progress.done} aria-label="Removal progress" />
          <p>
            {running ? `Removing ${progress.done} of ${progress.total}…` : progress.status === 'error' ? `Stopped: ${progress.note ?? 'error'}` : 'Finished.'} {progress.removed} removed, {progress.already_gone} already gone, {progress.failed} failed
            {progress.deferred ? `, ${progress.deferred} left for the next run` : ''}
            {progress.unreported ? `, ${progress.unreported} not reported to the server` : ''}.
          </p>
        </div>
      )}
    </section>
  );
}
