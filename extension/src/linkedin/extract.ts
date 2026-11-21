// Reads saved posts out of the LinkedIn saved-posts page, in the shape
// docs/CAPTURE_CONTRACT.md gives for kind "saved_posts". Takes a document, so
// it runs the same in the content script and in the fixture test.
// UNVERIFIED against the live site: see selectors.ts.

import { BLOCKER_PATHS, LABELS, SELECTORS, URN_PATTERN } from './selectors.ts';

export interface LinkedInMedia {
  type: 'image' | 'video';
  url: string | null;
  poster: string | null;
}

export interface LinkedInPost {
  urn: string | null;
  url: string | null;
  author_name: string | null;
  author_url: string | null;
  author_headline: string | null;
  text: string | null;
  posted_label: string | null;
  media: LinkedInMedia[];
  reshared: LinkedInPost | null;
}

/** First element matched by any selector in the list. A selector the browser rejects is skipped. */
export function first<T extends Element = HTMLElement>(root: ParentNode, selectors: string[]): T | null {
  for (const selector of selectors) {
    try {
      const found = root.querySelector<T>(selector);
      if (found) return found;
    } catch {
      /* unsupported selector */
    }
  }
  return null;
}

function all(root: ParentNode, selector: string): Element[] {
  try {
    return [...root.querySelectorAll(selector)];
  } catch {
    return [];
  }
}

function clean(value: string | null | undefined): string | null {
  const text = (value ?? '')
    .replace(/[ \t ]+/g, ' ')
    .replace(/ ?\n ?/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  return text || null;
}

function readText(el: Element | null): string | null {
  if (!el) return null;
  // innerText keeps line breaks as shown; textContent is the fallback where layout is unavailable.
  return clean((el as HTMLElement).innerText ?? el.textContent);
}

function absoluteUrl(href: string | null | undefined, base: string): string | null {
  if (!href) return null;
  try {
    const url = new URL(href, base);
    url.search = ''; // tracking parameters
    url.hash = '';
    return url.href;
  } catch {
    return null;
  }
}

/** The saved posts now in the page. */
export function findCards(doc: ParentNode): Element[] {
  for (const selector of SELECTORS.card) {
    const cards = all(doc, selector).filter((card) => readUrn(card) !== null);
    if (cards.length) return cards;
  }
  // Fallback for unknown markup: the list item around each link to a post.
  const cards = new Set<Element>();
  for (const link of all(doc, SELECTORS.postLink)) {
    const card = link.closest('li');
    if (card) cards.add(card);
  }
  return [...cards];
}

/** The post's urn, from an attribute or from a link to the post. `skip` is the embedded post, whose urn is not this one's. */
export function readUrn(card: Element, skip: Element | null = null): string | null {
  const inScope = (el: Element) => !skip || !skip.contains(el);
  for (const attribute of SELECTORS.urnAttributes) {
    for (const el of [card, ...all(card, `[${attribute}]`)]) {
      const match = inScope(el) ? URN_PATTERN.exec(el.getAttribute(attribute) ?? '') : null;
      if (match) return match[0];
    }
  }
  for (const link of all(card, SELECTORS.postLink)) {
    const match = inScope(link) ? URN_PATTERN.exec(decodeURIComponent(link.getAttribute('href') ?? '')) : null;
    if (match) return match[0];
  }
  return null;
}

function readMedia(scope: Element, skip: Element | null, base: string): LinkedInMedia[] {
  const media: LinkedInMedia[] = [];
  const inScope = (el: Element) => !skip || !skip.contains(el);
  for (const video of all(scope, 'video').filter(inScope) as HTMLVideoElement[]) {
    media.push({
      type: 'video',
      // A blob: address is useless outside this tab.
      url: absoluteMediaUrl(video.getAttribute('src') ?? video.querySelector('source')?.getAttribute('src'), base),
      poster: absoluteMediaUrl(video.getAttribute('poster'), base),
    });
  }
  for (const img of all(scope, 'img').filter(inScope) as HTMLImageElement[]) {
    if (img.closest(SELECTORS.avatar) || img.closest('a[href*="/in/"], a[href*="/company/"]')) continue; // profile picture
    const url = absoluteMediaUrl(img.getAttribute('src') ?? img.getAttribute('data-delayed-url'), base);
    if (!url) continue;
    // An image that is only a video's preview is already recorded as that video's poster.
    if (media.some((m) => m.poster === url)) continue;
    media.push({ type: 'image', url, poster: null });
  }
  return media;
}

function absoluteMediaUrl(src: string | null | undefined, base: string): string | null {
  if (!src || src.startsWith('blob:') || src.startsWith('data:')) return null;
  try {
    return new URL(src, base).href;
  } catch {
    return null;
  }
}

/** Reads one post from `scope`, leaving out anything inside `skip` (the embedded post, read separately). */
function readPost(scope: Element, skip: Element | null, base: string): LinkedInPost {
  const pick = (selectors: string[]): HTMLElement | null => {
    for (const selector of selectors) {
      const found = all(scope, selector).find((el) => !skip || !skip.contains(el));
      if (found) return found as HTMLElement;
    }
    return null;
  };
  const urn = readUrn(scope, skip);
  const link = all(scope, SELECTORS.postLink).find((el) => !skip || !skip.contains(el));
  const text = readText(pick(SELECTORS.text));
  return {
    urn,
    url: absoluteUrl(link?.getAttribute('href'), base) ?? (urn ? `${new URL(base).origin}/feed/update/${urn}/` : null),
    author_name: readText(pick(SELECTORS.authorName)),
    author_url: absoluteUrl(pick(SELECTORS.authorLink)?.getAttribute('href'), base),
    author_headline: readText(pick(SELECTORS.authorHeadline)),
    text: text ? clean(text.replace(LABELS.seeMoreSuffix, '')) : null,
    posted_label: readText(pick(SELECTORS.postedLabel)),
    media: readMedia(scope, skip, base),
    reshared: null,
  };
}

/** One saved post, or null when the card has no urn (then the selectors need attention). */
export function extractPost(card: Element, base: string): LinkedInPost | null {
  const resharedEl = first(card, SELECTORS.reshared);
  const post = readPost(card, resharedEl, base);
  if (!post.urn) return null;
  if (resharedEl) {
    const inner = readPost(resharedEl, null, base);
    // An embedded object with neither text nor author is a link card or media frame, not a post.
    if (inner.text || inner.author_name) post.reshared = inner;
  }
  return post;
}

/**
 * Presses "see more" inside a card when that is a plain button. A link, or a
 * button inside a link, would leave the page, so it is left alone and the
 * shortened text is captured as it is. Returns true when something was pressed.
 */
export function expandSeeMore(card: Element): boolean {
  let pressed = false;
  for (const selector of SELECTORS.seeMore) {
    for (const el of all(card, selector) as HTMLElement[]) {
      if (el.tagName !== 'BUTTON' || el.closest('a[href]') || el.getAttribute('aria-expanded') === 'true') continue;
      el.click();
      pressed = true;
    }
  }
  return pressed;
}

function isShown(el: Element): boolean {
  return !el.closest('[hidden], [aria-hidden="true"]');
}

/** Dialogs on screen, without the messaging windows that are part of the normal page. */
export function openDialogs(doc: ParentNode): Element[] {
  return all(doc, SELECTORS.dialog).filter((el) => isShown(el) && !el.closest(SELECTORS.dialogIgnore));
}

/**
 * A reason to stop at once, or null: a sign-in or checkpoint address, a
 * captcha or sign-in form, or any dialog (none is expected while reading).
 */
export function detectBlocker(doc: Document, href: string): string | null {
  try {
    if (BLOCKER_PATHS.test(new URL(href).pathname)) return 'LinkedIn is asking to sign in or pass a check';
  } catch {
    /* keep checking the page */
  }
  for (const selector of SELECTORS.blockers) {
    if (all(doc, selector).some(isShown)) return 'sign-in form or captcha on the page';
  }
  const dialog = openDialogs(doc)[0];
  if (dialog) return `unexpected dialog: "${(readText(dialog) ?? '').slice(0, 80)}"`;
  return null;
}
