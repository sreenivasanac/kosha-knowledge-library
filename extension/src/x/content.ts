// Content script on x.com (isolated world). Three jobs:
//  1. bridge: pass the responses recorded by inpage.ts to the service worker (always on);
//  2. backfill: scroll the bookmarks page, then each folder, at the pace BackfillTracker sets;
//  3. removal: un-bookmark one post when the service worker asks (see unbookmark.ts).

import { BACKFILL_PACING, BRAND, STORAGE, X } from '../config.ts';
import { createPanel } from '../panel.ts';
import { between, sleep } from '../util.ts';
import { BackfillTracker, IDLE_BACKFILL, backfillTarget, isOnTarget, patchBackfill, readBackfill, startBackfill } from './backfill.ts';
import type { BackfillState } from './backfill.ts';
import { INPAGE_SOURCE, countPosts, folderIdFromPath, kindForOperation, matchOperation } from './match.ts';
import type { InpageMessage, XFolder } from './match.ts';
import { unbookmark } from './unbookmark.ts';

const panel = createPanel(`${BRAND.name} · X bookmarks`);
let myTabId: number | null = null;
let state: BackfillState = IDLE_BACKFILL;
let tracker = new BackfillTracker();
let driving = false;
/** Responses passed on since this page loaded, shown when no backfill is running. */
let passive = { pages: 0, posts: 0 };

/** Message to the service worker. Resolves to undefined when the extension was reloaded under this page. */
function send(message: unknown): Promise<any> {
  return chrome.runtime.sendMessage(message).catch(() => undefined);
}

const isDriver = () => myTabId !== null && state.tabId === myTabId;
const isRunningHere = () => isDriver() && state.status === 'running';
const onBookmarksPage = () => X.bookmarksRootPath.test(location.pathname) || folderIdFromPath(location.pathname) !== null;

// ---- 1. bridge -------------------------------------------------------------

window.addEventListener('message', (event: MessageEvent) => {
  const data = event.data as InpageMessage | undefined;
  if (event.source !== window || event.origin !== location.origin || data?.source !== INPAGE_SOURCE) return;
  const operation = matchOperation(String(data.url));
  if (!operation) return;
  const kind = kindForOperation(operation);
  const isTimeline = kind !== 'folder_list';

  let payload: unknown = null;
  if (data.status === 200) {
    try {
      payload = JSON.parse(data.body);
    } catch {
      return; // not a JSON body, so not the response Kosha wants
    }
    void send({ type: 'x:response', url: data.url, payload, pageUrl: location.href });
  }

  if (!isTimeline) return;
  const posts = payload ? countPosts(payload) : 0;
  if (data.status === 200) passive = { pages: passive.pages + 1, posts: passive.posts + posts };
  if (isRunningHere()) {
    tracker.onResponse(data.status, posts);
    if (data.status === 200) {
      // Counted here first, so two responses arriving together are both counted.
      state = { ...state, pages: state.pages + 1, posts: state.posts + posts };
      void patchBackfill({ pages: state.pages, posts: state.posts });
    }
  }
  render();
});

// ---- 2. backfill -----------------------------------------------------------

/** Waits, but returns false as soon as the backfill is paused, stopped or moved to another tab. */
async function sleepWhileRunning(ms: number): Promise<boolean> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (!isRunningHere()) return false;
    await sleep(Math.min(500, end - Date.now()));
  }
  return isRunningHere();
}

/** The current timeline is finished: go from the main list to the folders, or to the next folder. */
async function advance(reason: string): Promise<void> {
  tracker = new BackfillTracker();
  if (state.phase === 'main') {
    const folders = ((await chrome.storage.local.get(STORAGE.xFolders))[STORAGE.xFolders] as XFolder[] | undefined) ?? [];
    state = await patchBackfill({ phase: 'folders', folders, folderIndex: 0, navigatedFor: null, note: `All bookmarks done (${reason})` });
  } else {
    state = await patchBackfill({ folderIndex: state.folderIndex + 1, navigatedFor: null, note: `Folder done (${reason})` });
  }
}

async function drive(): Promise<void> {
  if (driving) return;
  driving = true;
  try {
    // X swaps /i/bookmarks for /i/history after load; judge the address once that has happened.
    if (!(await sleepWhileRunning(X.settleMs))) return;
    while (isRunningHere()) {
      const target = backfillTarget(state);
      if (!target) {
        state = await patchBackfill({ status: 'done', note: 'Backfill finished' });
        return;
      }
      if (!isOnTarget(location.pathname, target)) {
        if (state.navigatedFor !== target.key) {
          state = await patchBackfill({ navigatedFor: target.key });
          location.assign(target.url); // this script starts again on the new page and resumes
          return;
        }
        // The tab was already sent there and X showed something else.
        if (!target.folder) {
          state = await patchBackfill({ status: 'error', note: 'X did not open the bookmarks page' });
          return;
        }
        state = await patchBackfill({
          folderIndex: state.folderIndex + 1,
          navigatedFor: null,
          note: `Skipped folder "${target.folder.name}": X did not open it`,
        });
        continue;
      }

      const step = tracker.next();
      if (step.kind === 'stop') {
        await advance(step.reason ?? '');
        continue;
      }
      const seconds = Math.round(step.waitMs / 1000);
      const where = target.folder ? `Folder "${target.folder.name}"` : 'All bookmarks';
      const note =
        step.kind === 'backoff'
          ? `${step.reason}; waiting ${Math.round(seconds / 60)} min`
          : step.kind === 'rest'
            ? `${where}: resting ${seconds} s`
            : `${where}: scrolling`;
      if (note !== state.note) state = await patchBackfill({ note });

      if (!(await sleepWhileRunning(step.waitMs))) return;
      if (step.kind === 'scroll') {
        // To the end of what is loaded: that is what makes X ask for the next page.
        window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
      } else if (step.kind === 'scroll_up') {
        window.scrollBy({ top: -between(BACKFILL_PACING.scrollUpPx), behavior: 'smooth' });
      }
    }
  } finally {
    driving = false;
    render();
  }
}

async function startHere(): Promise<void> {
  if (myTabId === null) return;
  await startBackfill(myTabId);
  location.assign(X.bookmarksUrl); // a fresh load, so the first page and the folder list are captured too
}

function render(): void {
  panel.show(onBookmarksPage() || (isDriver() && state.status !== 'idle'));
  const active = isDriver() && (state.status === 'running' || state.status === 'paused');
  if (active) {
    const folderLine = state.phase === 'folders' ? [`Folder ${Math.min(state.folderIndex + 1, state.folders.length)} of ${state.folders.length}`] : [];
    panel.setLines([`Backfill ${state.status}`, ...folderLine, `${state.pages} pages, ${state.posts} posts captured`, state.note]);
    panel.setButtons([
      state.status === 'running'
        ? { label: 'Pause', onClick: () => void patchBackfill({ status: 'paused', note: 'Paused' }) }
        : { label: 'Resume', onClick: () => void patchBackfill({ status: 'running' }) },
      { label: 'Stop', onClick: () => void patchBackfill({ status: 'stopped', note: 'Stopped' }) },
    ]);
  } else {
    const last = state.status === 'idle' ? [] : [`Last backfill: ${state.status}, ${state.pages} pages, ${state.posts} posts`, state.note];
    panel.setLines([`Capturing as you scroll: ${passive.pages} pages, ${passive.posts} posts`, ...last]);
    panel.setButtons([{ label: 'Backfill', onClick: () => void startHere() }]);
  }
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local' || !changes[STORAGE.backfill]) return;
  state = { ...IDLE_BACKFILL, ...(changes[STORAGE.backfill].newValue as Partial<BackfillState> | undefined) };
  render();
  if (isRunningHere()) void drive(); // resumed, here or from the popup
});

// ---- 3. removal ------------------------------------------------------------

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== 'x:unbookmark') return;
  unbookmark(document, String(message.postId), sleep).then(sendResponse, (error) =>
    sendResponse({ status: 'failed', detail: String(error) }),
  );
  return true; // the answer comes later
});

// ---- start -----------------------------------------------------------------

async function init(): Promise<void> {
  myTabId = (await send({ type: 'whoami' }))?.tabId ?? null;
  state = await readBackfill();
  // X is a single-page app: the address changes without a load, so the panel is re-checked on a timer.
  setInterval(render, 1000);
  render();
  if (isRunningHere()) void drive();
}
void init();
