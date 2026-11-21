// Unsaves one post on the LinkedIn saved-posts page through the post's own
// "..." menu, as a person would. Stops the whole run (abort) on a sign-in
// wall, a captcha or any dialog other than the expected confirmation.
// UNVERIFIED against the live site: see selectors.ts.

import { LINKEDIN, REMOVAL } from '../config.ts';
import { between } from '../util.ts';
import type { Rng } from '../util.ts';
import type { RemovalOutcome } from '../removal/queue.ts';
import { detectBlocker, findCards, first, openDialogs, readUrn } from './extract.ts';
import { LABELS, SELECTORS } from './selectors.ts';

const POLL_MS = 300;

export interface UnsaveDeps {
  sleep(ms: number): Promise<void>;
  /** Brings more saved posts into the page (scroll, or the "show more" button). */
  loadMore(): void;
  href(): string;
  rng?: Rng;
}

function menuItems(doc: Document): HTMLElement[] {
  for (const selector of SELECTORS.menuItem) {
    const items = [...doc.querySelectorAll<HTMLElement>(selector)];
    if (items.length) return items;
  }
  return [];
}

async function waitFor<T>(read: () => T | null | undefined, timeoutMs: number, sleep: (ms: number) => Promise<void>): Promise<T | null> {
  for (let waited = 0; ; waited += POLL_MS) {
    const value = read();
    if (value || waited >= timeoutMs) return value ?? null;
    await sleep(POLL_MS);
  }
}

/** Looks for the post among the loaded ones, loading more at the capture pace until the list ends. */
async function findCard(doc: Document, urn: string, deps: UnsaveDeps, limits: typeof REMOVAL.linkedin): Promise<Element | 'end' | 'none' | 'limit' | 'blocked'> {
  let lastCount = -1;
  let unchanged = 0;
  for (let scrolls = 0; ; scrolls++) {
    if (detectBlocker(doc, deps.href())) return 'blocked';
    const cards = findCards(doc);
    const card = cards.find((c) => readUrn(c) === urn);
    if (card) return card;
    unchanged = cards.length === lastCount ? unchanged + 1 : 0;
    lastCount = cards.length;
    if (unchanged >= limits.endOfListScrolls) return cards.length ? 'end' : 'none';
    if (scrolls >= limits.maxSearchScrolls) return 'limit';
    deps.loadMore();
    await deps.sleep(between(LINKEDIN.capture.scrollGapMs, deps.rng));
  }
}

export async function unsavePost(doc: Document, urn: string, deps: UnsaveDeps, limits = REMOVAL.linkedin): Promise<RemovalOutcome> {
  const blocked = () => {
    const reason = detectBlocker(doc, deps.href());
    return reason ? ({ status: 'failed', detail: reason, abort: true } as RemovalOutcome) : null;
  };

  const found = await findCard(doc, urn, deps, limits);
  if (found === 'blocked') return blocked() ?? { status: 'failed', detail: 'page changed', abort: true };
  // No saved post recognised at all means the selectors are out of date, not that the post is gone.
  if (found === 'none') return { status: 'failed', detail: 'no saved posts recognised on the page; selectors may be out of date', abort: true };
  if (found === 'limit') return { status: 'failed', detail: 'post not found within the scroll limit' };
  if (found === 'end') return { status: 'already_gone', detail: 'not in the saved posts list' };
  const card = found;

  const trigger = first(card, SELECTORS.overflowButton);
  if (!trigger) return { status: 'failed', detail: 'menu button not found on the post' };
  trigger.click();

  const item = await waitFor(() => menuItems(doc).find((el) => LABELS.unsave.test(el.textContent ?? '')), limits.menuTimeoutMs, deps.sleep);
  if (!item) {
    const saveAgain = menuItems(doc).some((el) => LABELS.save.test(el.textContent ?? ''));
    trigger.click(); // close the menu again
    return saveAgain
      ? { status: 'already_gone', detail: 'the menu offers Save, so the post is not saved' }
      : { status: 'failed', detail: 'no Unsave entry in the menu' };
  }
  item.click();

  // Wait for the post to leave the list, accepting the confirmation if LinkedIn asks for one.
  let confirmedDialog: Element | null = null;
  for (let waited = 0; waited < limits.verifyTimeoutMs; waited += POLL_MS) {
    await deps.sleep(POLL_MS);
    const dialog = openDialogs(doc)[0];
    if (dialog) {
      if (dialog === confirmedDialog) continue; // already accepted, still closing
      const expected = !confirmedDialog && LABELS.unsaveDialog.test(dialog.textContent ?? '');
      const button = expected
        ? SELECTORS.dialogConfirm
            .flatMap((selector) => [...dialog.querySelectorAll<HTMLElement>(selector)])
            .find((el) => LABELS.confirmButton.test(el.textContent ?? ''))
        : null;
      if (!button) return { status: 'failed', detail: `unexpected dialog: "${(dialog.textContent ?? '').trim().slice(0, 80)}"`, abort: true };
      button.click();
      confirmedDialog = dialog;
      continue;
    }
    const stop = blocked();
    if (stop) return stop;
    if (!card.isConnected || !findCards(doc).some((c) => readUrn(c) === urn)) {
      return { status: 'removed', detail: 'unsaved' };
    }
  }
  return { status: 'failed', detail: 'chose Unsave, but the post is still listed' };
}
