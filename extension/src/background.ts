// Service worker. Receives captures from the content scripts, keeps them in
// the outbox until Kosha has them, and runs "Remove from sources".

import { API, BRAND, EXTERNAL_MESSAGES, LINKEDIN, OUTBOX, REMOVAL, STORAGE, X } from './config.ts';
import { buildLinkedInEnvelope, buildXEnvelope } from './envelope.ts';
import type { Envelope } from './envelope.ts';
import { Outbox } from './outbox.ts';
import type { PostResult } from './outbox.ts';
import { isRemovalTriggerAllowed, runRemovals } from './removal/queue.ts';
import type { PendingRemoval, RemovalOutcome, RemovalProgress } from './removal/queue.ts';
import { apiFetch, getSettings } from './settings.ts';
import { originOf, sleep } from './util.ts';
import { parseFolderList } from './x/match.ts';
import type { XFolder } from './x/match.ts';

const FLUSH_ALARM = 'outbox-flush';

// ---- captures --------------------------------------------------------------

async function postEnvelope(envelope: Envelope): Promise<PostResult> {
  let result: PostResult;
  let detail: string;
  try {
    const response = await apiFetch(API.captures, { method: 'POST', body: envelope });
    if (response.ok) {
      const body = await response.json().catch(() => ({}));
      result = { ok: true };
      detail = `${body.items_new ?? '?'} new, ${body.items_updated ?? '?'} updated, ${body.items_unchanged ?? '?'} unchanged`;
    } else {
      detail = response.status === 401 || response.status === 403 ? `HTTP ${response.status}: check the API token` : `HTTP ${response.status}`;
      result = { ok: false, status: response.status, error: detail };
    }
  } catch (error) {
    detail = `Server not reachable (${String(error)})`;
    result = { ok: false, status: null, error: detail };
  }
  // Shown in the popup as the status of the last capture.
  await chrome.storage.local.set({
    [STORAGE.lastCapture]: { at: new Date().toISOString(), source: envelope.source, kind: envelope.kind, ok: result.ok, detail },
  });
  return result;
}

const outbox = new Outbox(postEnvelope);

async function enqueue(envelope: Envelope): Promise<void> {
  await outbox.add(envelope);
  void outbox.flush();
}

// The alarm retries what is waiting even when no page is open. Creating it again replaces it.
chrome.alarms.create(FLUSH_ALARM, { periodInMinutes: OUTBOX.flushAlarmMinutes });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === FLUSH_ALARM) void outbox.flush();
});

async function testConnection(): Promise<{ ok: boolean; detail: string }> {
  try {
    const response = await apiFetch(API.health);
    if (response.ok) return { ok: true, detail: 'Connected' };
    if (response.status === 401 || response.status === 403) return { ok: false, detail: 'Server reached, but it refused the API token' };
    return { ok: false, detail: `Server answered HTTP ${response.status}` };
  } catch (error) {
    return { ok: false, detail: `Server not reachable (${String(error)})` };
  }
}

// ---- removal at source -----------------------------------------------------

let removalRunning = false;
let removalStopRequested = false;

function waitForTabLoad(tabId: number, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    const finish = (ok: boolean) => {
      chrome.tabs.onUpdated.removeListener(onUpdated);
      clearTimeout(timer);
      resolve(ok);
    };
    const onUpdated = (id: number, info: { status?: string }) => {
      if (id === tabId && info.status === 'complete') finish(true);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    chrome.tabs.onUpdated.addListener(onUpdated);
    chrome.tabs.get(tabId).then((tab) => tab.status === 'complete' && finish(true), () => finish(false));
  });
}

/** Asks a tab's content script to act. The script may not be listening in the first moments after load. */
async function askTab(tabId: number, message: unknown): Promise<RemovalOutcome> {
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      const outcome = (await chrome.tabs.sendMessage(tabId, message)) as RemovalOutcome | undefined;
      if (outcome) return outcome;
    } catch {
      /* not listening yet */
    }
    await sleep(1000);
  }
  return { status: 'failed', detail: 'the page did not answer the extension' };
}

/** X: open the post in a background tab, let its content script press the bookmark button, close the tab. */
async function removeFromX(item: PendingRemoval): Promise<RemovalOutcome> {
  if (!/^\d+$/.test(item.source_id)) return { status: 'failed', detail: 'source_id is not a post id' };
  // Only an x.com address is ever opened, whatever the server sent.
  const url = originOf(item.url) === X.origin ? item.url : `${X.origin}/i/status/${item.source_id}`;
  const tab = await chrome.tabs.create({ url, active: false });
  try {
    if (!(await waitForTabLoad(tab.id!, REMOVAL.x.renderTimeoutMs))) return { status: 'failed', detail: 'the post page did not load' };
    return await askTab(tab.id!, { type: 'x:unbookmark', postId: item.source_id });
  } finally {
    await chrome.tabs.remove(tab.id!).catch(() => undefined);
  }
}

/** LinkedIn: use the saved-posts tab (opening one if needed) and let its content script unsave the post. */
async function removeFromLinkedIn(item: PendingRemoval): Promise<RemovalOutcome> {
  let [tab] = await chrome.tabs.query({ url: `${LINKEDIN.savedPostsUrl}*` });
  if (!tab) {
    tab = await chrome.tabs.create({ url: LINKEDIN.savedPostsUrl, active: REMOVAL.linkedin.openTabActive });
    if (!(await waitForTabLoad(tab.id!, 30_000))) return { status: 'failed', detail: 'the saved-posts page did not load', abort: true };
    await sleep(3000); // let the list render
  }
  return askTab(tab.id!, { type: 'li:unsave', urn: item.source_id });
}

async function fetchPending(): Promise<PendingRemoval[]> {
  const response = await apiFetch(API.removalsPending);
  if (!response.ok) throw new Error(`Kosha answered HTTP ${response.status} for the pending removals`);
  const list = await response.json();
  if (!Array.isArray(list)) throw new Error('Kosha sent no list of pending removals');
  return list;
}

const saveProgress = (progress: RemovalProgress) => chrome.storage.local.set({ [STORAGE.removalRun]: progress });

async function readRemovalRun(): Promise<RemovalProgress | null> {
  const stored = ((await chrome.storage.local.get(STORAGE.removalRun))[STORAGE.removalRun] as RemovalProgress | undefined) ?? null;
  // "running" in storage with no run in this worker means the worker was stopped in the middle of one.
  if (stored?.status === 'running' && !removalRunning) return { ...stored, status: 'error', note: 'Interrupted; start again to continue' };
  return stored;
}

function startRemovalRun(): { ok: boolean; detail: string } {
  if (removalRunning) return { ok: false, detail: 'A removal run is already in progress' };
  removalRunning = true;
  removalStopRequested = false;
  // Calling an extension API resets the worker's idle timer, so the run is not cut off between items.
  const keepAlive = setInterval(() => void chrome.runtime.getPlatformInfo(), REMOVAL.keepAliveMs);
  runRemovals({
    fetchPending,
    remove: (item) => (item.source === 'x' ? removeFromX(item) : removeFromLinkedIn(item)),
    report: async (item, outcome) => {
      const response = await apiFetch(API.removalsResult, {
        method: 'POST',
        body: { item_id: item.item_id, status: outcome.status, detail: outcome.detail },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    },
    sleep,
    shouldStop: () => removalStopRequested,
    onProgress: (progress) => void saveProgress(progress),
  })
    .catch((error) =>
      saveProgress({ status: 'error', total: 0, done: 0, removed: 0, already_gone: 0, failed: 0, deferred: 0, unreported: 0, note: String(error) }),
    )
    .finally(() => {
      clearInterval(keepAlive);
      removalRunning = false;
    });
  return { ok: true, detail: 'Started' };
}

async function mayTriggerRemoval(sender: chrome.runtime.MessageSender, external: boolean): Promise<boolean> {
  const extension = { id: chrome.runtime.id, origin: `chrome-extension://${chrome.runtime.id}` };
  return isRemovalTriggerAllowed(sender, extension, originOf((await getSettings()).serverUrl), external);
}

// ---- messages --------------------------------------------------------------

async function onInternalMessage(message: any, sender: chrome.runtime.MessageSender): Promise<unknown> {
  switch (message?.type) {
    case 'whoami':
      return { tabId: sender.tab?.id ?? null };

    case 'x:response': {
      if (sender.origin !== X.origin) return { ok: false };
      const folders = ((await chrome.storage.local.get(STORAGE.xFolders))[STORAGE.xFolders] as XFolder[] | undefined) ?? [];
      const envelope = buildXEnvelope({ url: String(message.url), payload: message.payload, pageUrl: String(message.pageUrl), folders });
      if (!envelope) return { ok: false };
      if (envelope.kind === 'folder_list') {
        // Remembered for folder names on later captures and for the folder pass of the backfill.
        const list = parseFolderList(message.payload);
        if (list.length) await chrome.storage.local.set({ [STORAGE.xFolders]: list });
      }
      await enqueue(envelope);
      return { ok: true };
    }

    case 'li:posts': {
      if (sender.origin !== LINKEDIN.origin || !Array.isArray(message.posts)) return { ok: false };
      await enqueue(buildLinkedInEnvelope({ posts: message.posts, pageUrl: String(message.pageUrl) }));
      return { ok: true };
    }

    case 'status':
      void outbox.flush();
      return {
        outbox: await outbox.counts(),
        lastCapture: (await chrome.storage.local.get(STORAGE.lastCapture))[STORAGE.lastCapture] ?? null,
        removal: await readRemovalRun(),
      };

    case 'test':
      return testConnection();

    case 'removal:preview': {
      if (!(await mayTriggerRemoval(sender, false))) return { ok: false, detail: 'Not allowed' };
      try {
        const pending = await fetchPending();
        return { ok: true, x: pending.filter((p) => p.source === 'x').length, linkedin: pending.filter((p) => p.source === 'linkedin').length };
      } catch (error) {
        return { ok: false, detail: String(error) };
      }
    }

    case 'removal:start':
      if (!(await mayTriggerRemoval(sender, false))) return { ok: false, detail: 'Not allowed' };
      return startRemovalRun();

    case 'removal:stop':
      if (!(await mayTriggerRemoval(sender, false))) return { ok: false, detail: 'Not allowed' };
      removalStopRequested = true;
      return { ok: true };
  }
  return undefined;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  onInternalMessage(message, sender).then(sendResponse, (error) => sendResponse({ ok: false, detail: String(error) }));
  return true; // the answer comes later
});

// Messages from the Kosha web app (see EXTERNAL_MESSAGES in config.ts).
chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  (async () => {
    if (!(await mayTriggerRemoval(sender, true))) return { ok: false, detail: 'This page is not the configured Kosha server' };
    switch (message?.type) {
      case EXTERNAL_MESSAGES.ping:
        return { ok: true, version: BRAND.version };
      case EXTERNAL_MESSAGES.removeFromSources:
        return startRemovalRun();
      case EXTERNAL_MESSAGES.removalStatus:
        return { ok: true, removal: await readRemovalRun() };
    }
    return { ok: false, detail: 'Unknown message' };
  })().then(sendResponse, (error) => sendResponse({ ok: false, detail: String(error) }));
  return true;
});
