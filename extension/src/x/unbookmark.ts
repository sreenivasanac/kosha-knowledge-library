// Removes one bookmark on X by pressing the post's own bookmark button, as a
// person would. Runs in the content script of a post page opened by the
// service worker. The button's state is checked before and after the click.
// Selectors are X's data-testid values; not exercised against the live site.

import { REMOVAL, X } from '../config.ts';
import type { RemovalOutcome } from '../removal/queue.ts';

const POLL_MS = 300;

/** The post with this id among those on the page (replies below it have other ids). */
function findPost(doc: Document, postId: string): Element | null {
  for (const post of doc.querySelectorAll(X.selectors.post)) {
    for (const link of post.querySelectorAll<HTMLAnchorElement>('a[href*="/status/"]')) {
      if (new RegExp(`/status/${postId}(?:[/?#]|$)`).test(link.getAttribute('href') ?? '')) return post;
    }
  }
  return null;
}

async function waitFor<T>(read: () => T | null, timeoutMs: number, sleep: (ms: number) => Promise<void>): Promise<T | null> {
  for (let waited = 0; ; waited += POLL_MS) {
    const value = read();
    if (value || waited >= timeoutMs) return value;
    await sleep(POLL_MS);
  }
}

export async function unbookmark(
  doc: Document,
  postId: string,
  sleep: (ms: number) => Promise<void>,
  limits = REMOVAL.x,
): Promise<RemovalOutcome> {
  if (!/^\d+$/.test(postId)) return { status: 'failed', detail: 'source_id is not a post id' };
  if (/^\/(i\/flow\/)?login/.test(doc.location?.pathname ?? '')) {
    return { status: 'failed', detail: 'X is asking to sign in', abort: true };
  }

  const post = await waitFor(() => findPost(doc, postId), limits.renderTimeoutMs, sleep);
  if (!post) return { status: 'failed', detail: 'post did not appear (deleted, private, or X changed its page)' };

  const state = await waitFor(
    () => post.querySelector<HTMLElement>(`${X.selectors.bookmarked}, ${X.selectors.notBookmarked}`),
    limits.verifyTimeoutMs,
    sleep,
  );
  if (!state) return { status: 'failed', detail: 'bookmark button not found' };
  if (!state.matches(X.selectors.bookmarked)) return { status: 'already_gone', detail: 'post is not bookmarked' };

  state.click();

  const cleared = await waitFor(
    () => (post.querySelector(X.selectors.bookmarked) ? null : post.querySelector(X.selectors.notBookmarked)),
    limits.verifyTimeoutMs,
    sleep,
  );
  return cleared
    ? { status: 'removed', detail: 'bookmark removed' }
    : { status: 'failed', detail: 'clicked, but the button still shows the post as bookmarked' };
}
