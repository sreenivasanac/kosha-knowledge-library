// The popup: settings, connection test, capture status, and the buttons that
// start a backfill or a removal run. Plain DOM.

import { BRAND, LINKEDIN, LOCAL_SERVER_HOSTS, STORAGE, X } from '../config.ts';
import { getSettings, saveSettings } from '../settings.ts';
import { originOf } from '../util.ts';
import { backfillTarget, patchBackfill, readBackfill, startBackfill } from '../x/backfill.ts';
import type { RemovalProgress } from '../removal/queue.ts';

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const text = (id: string, value: string): void => {
  $(id).textContent = value;
};
const show = (id: string, visible: boolean): void => {
  $(id).hidden = !visible;
};

document.title = BRAND.extensionName;
text('title', BRAND.extensionName);

// ---- settings --------------------------------------------------------------

async function loadSettings(): Promise<void> {
  const settings = await getSettings();
  $<HTMLInputElement>('server').value = settings.serverUrl;
  $<HTMLInputElement>('token').value = settings.token;
}

async function onSave(): Promise<void> {
  const serverUrl = $<HTMLInputElement>('server').value.trim();
  const origin = originOf(serverUrl);
  if (!origin) return text('connection', 'Enter a full URL, for example http://localhost:8000');
  // A server that is not on this computer needs the browser's permission, asked here on your click.
  const pattern = `${origin.replace(/:\d+$/, '')}/*`;
  if (!LOCAL_SERVER_HOSTS.includes(pattern) && !(await chrome.permissions.request({ origins: [pattern] }))) {
    return text('connection', 'Not saved: the browser did not give access to that server');
  }
  await saveSettings({ serverUrl: origin, token: $<HTMLInputElement>('token').value.trim() });
  $<HTMLInputElement>('server').value = origin;
  text('connection', 'Saved');
}

async function onTest(): Promise<void> {
  text('connection', 'Testing…');
  const result = await chrome.runtime.sendMessage({ type: 'test' });
  text('connection', result?.detail ?? 'No answer from the extension');
}

// ---- backfill --------------------------------------------------------------

/**
 * The tab the backfill will scroll: the current tab when it is on X, else a
 * new one. A new tab is opened in the background, because a tab opening in
 * front closes this popup before the progress has been saved.
 */
async function backfillTabId(): Promise<number> {
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (active?.id != null && originOf(active.url ?? '') === X.origin) return active.id;
  return (await chrome.tabs.create({ url: 'about:blank', active: false })).id!;
}

async function onBackfillStart(): Promise<void> {
  const tabId = await backfillTabId();
  await startBackfill(tabId);
  await chrome.tabs.update(tabId, { url: X.bookmarksUrl, active: true });
}

/** Resume from the popup: also covers a closed tab or a restarted browser, by loading the page the backfill was on. */
async function onBackfillResume(): Promise<void> {
  const state = await readBackfill();
  const target = backfillTarget(state);
  if (!target) return void (await patchBackfill({ status: 'done' }));
  const tabStillOpen = state.tabId != null && (await chrome.tabs.get(state.tabId).then(() => true, () => false));
  if (tabStillOpen) return void (await patchBackfill({ status: 'running' }));
  const tabId = await backfillTabId();
  await patchBackfill({ status: 'running', tabId, navigatedFor: target.key });
  await chrome.tabs.update(tabId, { url: target.url, active: true });
}

async function renderBackfill(): Promise<void> {
  const state = await readBackfill();
  const active = state.status === 'running' || state.status === 'paused';
  text('backfill', state.status === 'idle' ? 'No backfill yet' : `${state.status}: ${state.pages} pages, ${state.posts} posts. ${state.note}`);
  show('backfill-start', !active);
  show('backfill-pause', state.status === 'running');
  show('backfill-resume', state.status === 'paused');
  show('backfill-stop', active);
}

// ---- status and removal ----------------------------------------------------

function describeRemoval(run: RemovalProgress | null): string {
  if (!run) return 'Un-bookmarks and unsaves the items you archived in Kosha. It cannot be undone from here.';
  const counts = `${run.done} of ${run.total} done: ${run.removed} removed, ${run.already_gone} already gone, ${run.failed} failed`;
  const extras = [run.deferred ? `${run.deferred} LinkedIn left for the next run` : '', run.unreported ? `${run.unreported} not reported to Kosha` : '', run.note];
  return [`${run.status}. ${counts}`, ...extras.filter(Boolean)].join('. ');
}

async function renderStatus(): Promise<void> {
  const status = await chrome.runtime.sendMessage({ type: 'status' }).catch(() => null);
  if (!status) return;
  const last = status.lastCapture;
  text('last', last ? `${new Date(last.at).toLocaleString()} · ${last.source} ${last.kind} · ${last.ok ? 'sent' : 'not sent'} · ${last.detail}` : 'Nothing captured yet');
  const { waiting, rejected } = status.outbox;
  text('outbox', `${waiting} waiting to be sent` + (rejected ? `, ${rejected} refused by the server (kept)` : ''));
  const run: RemovalProgress | null = status.removal;
  text('removal', describeRemoval(run));
  const running = run?.status === 'running';
  show('removal-check', !running);
  show('removal-stop', running);
  if (running) show('removal-confirm', false);
}

/** First click: ask Kosha how many items are pending and show the second, confirming button. */
async function onRemovalCheck(): Promise<void> {
  const preview = await chrome.runtime.sendMessage({ type: 'removal:preview' });
  if (!preview?.ok) return text('removal', preview?.detail ?? 'No answer from the extension');
  if (preview.x + preview.linkedin === 0) return text('removal', 'Nothing is waiting to be removed');
  $('removal-confirm').textContent = `Confirm: remove ${preview.x} on X and ${preview.linkedin} on LinkedIn`;
  show('removal-confirm', true);
}

async function onRemovalConfirm(): Promise<void> {
  show('removal-confirm', false);
  const result = await chrome.runtime.sendMessage({ type: 'removal:start' });
  if (!result?.ok) text('removal', result?.detail ?? 'Could not start');
}

// ---- wiring ----------------------------------------------------------------

$('save').addEventListener('click', () => void onSave());
$('test').addEventListener('click', () => void onTest());
$('backfill-start').addEventListener('click', () => void onBackfillStart());
$('backfill-pause').addEventListener('click', () => void patchBackfill({ status: 'paused', note: 'Paused' }));
$('backfill-resume').addEventListener('click', () => void onBackfillResume());
$('backfill-stop').addEventListener('click', () => void patchBackfill({ status: 'stopped', note: 'Stopped' }));
$('linkedin-open').addEventListener('click', () => void chrome.tabs.create({ url: LINKEDIN.savedPostsUrl }));
$('removal-check').addEventListener('click', () => void onRemovalCheck());
$('removal-confirm').addEventListener('click', () => void onRemovalConfirm());
$('removal-stop').addEventListener('click', () => void chrome.runtime.sendMessage({ type: 'removal:stop' }));

chrome.storage.onChanged.addListener((changes) => {
  if (changes[STORAGE.backfill]) void renderBackfill();
  if (changes[STORAGE.lastCapture] || changes[STORAGE.removalRun]) void renderStatus();
});

void loadSettings();
void renderBackfill();
void renderStatus();
