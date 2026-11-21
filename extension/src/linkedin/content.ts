// Content script on linkedin.com. On the saved-posts page it shows a panel
// with a Capture button; capture reads the posts the page has rendered and
// scrolls slowly for more. It also unsaves one post when the service worker
// asks during "Remove from sources" (see unsave.ts).

import { BRAND, LINKEDIN } from '../config.ts';
import { createPanel } from '../panel.ts';
import { between, sleep } from '../util.ts';
import { detectBlocker, expandSeeMore, extractPost, findCards, first } from './extract.ts';
import type { LinkedInPost } from './extract.ts';
import { SELECTORS } from './selectors.ts';
import { unsavePost } from './unsave.ts';

const panel = createPanel(`${BRAND.name} · LinkedIn saved posts`);
let capturing = false;
let stopRequested = false;
let note = 'Not started';
/** Posts already sent, by urn, so each is sent once per capture. */
const seen = new Set<string>();
/** Cards that could not be read; a number above zero means the selectors need attention. */
let unreadable = new WeakSet<Element>();
let unreadableCount = 0;

const onSavedPage = () => location.pathname.startsWith(LINKEDIN.savedPostsPath);

/** Next batch: LinkedIn's own "show more" button when there is one, else a scroll to the end of the list. */
function loadMore(): void {
  const button = first(document, SELECTORS.showMoreResults);
  if (button) button.click();
  else window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
}

async function capture(): Promise<void> {
  if (capturing) return;
  capturing = true;
  stopRequested = false;
  seen.clear();
  unreadable = new WeakSet();
  unreadableCount = 0;
  let idleScrolls = 0;
  note = 'Capturing';
  render();

  try {
    while (!stopRequested && onSavedPage()) {
      const blocker = detectBlocker(document, location.href);
      if (blocker) {
        note = `Stopped: ${blocker}`;
        return;
      }

      const cards = findCards(document);
      if (cards.map(expandSeeMore).some(Boolean)) await sleep(400); // let the full text appear
      const fresh: LinkedInPost[] = [];
      for (const card of cards) {
        const post = extractPost(card, location.href);
        if (!post) {
          if (!unreadable.has(card)) {
            unreadable.add(card);
            unreadableCount += 1;
          }
        } else if (!seen.has(post.urn!) && !fresh.some((p) => p.urn === post.urn)) {
          fresh.push(post);
        }
      }

      if (fresh.length) {
        idleScrolls = 0;
        const stored = await chrome.runtime.sendMessage({ type: 'li:posts', posts: fresh, pageUrl: location.href }).catch(() => undefined);
        // Not acknowledged (the outbox could not write): these posts are not captured, so stop and say so.
        if (!stored?.ok) {
          note = 'Stopped: the extension could not store the posts; reload the page and capture again';
          return;
        }
        for (const post of fresh) seen.add(post.urn!);
      } else if (++idleScrolls >= LINKEDIN.capture.stopAfterIdleScrolls) {
        note = seen.size ? 'Finished: no more posts' : 'Finished: no posts recognised (selectors may be out of date)';
        return;
      }
      render();

      await sleep(between(LINKEDIN.capture.scrollGapMs));
      if (!stopRequested) loadMore();
    }
    note = stopRequested ? 'Stopped' : 'Stopped: left the saved-posts page';
  } finally {
    capturing = false;
    render();
  }
}

function render(): void {
  panel.show(onSavedPage());
  const skipped = unreadableCount ? [`${unreadableCount} posts could not be read`] : [];
  panel.setLines([`${seen.size} posts captured`, ...skipped, note]);
  panel.setButtons(
    capturing
      ? [{ label: 'Stop', onClick: () => void (stopRequested = true) }]
      : [{ label: 'Capture', onClick: () => void capture() }],
  );
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== 'li:unsave') return;
  if (!onSavedPage()) {
    sendResponse({ status: 'failed', detail: 'not on the saved-posts page', abort: true });
    return;
  }
  unsavePost(document, String(message.urn), { sleep, loadMore, href: () => location.href }).then(sendResponse, (error) =>
    sendResponse({ status: 'failed', detail: String(error) }),
  );
  return true; // the answer comes later
});

// LinkedIn is a single-page app: the address changes without a load.
setInterval(render, 1000);
render();
