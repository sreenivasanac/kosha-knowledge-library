// @vitest-environment happy-dom
// DOM logic against hand-built pages. These show the code is consistent with
// the selectors; they do not show the selectors match the live sites.
import { beforeEach, describe, expect, it } from 'vitest';
import { detectBlocker, expandSeeMore, extractPost, findCards } from '../src/linkedin/extract.ts';
import { unsavePost } from '../src/linkedin/unsave.ts';
import { unbookmark } from '../src/x/unbookmark.ts';
import fixture from './fixtures/linkedin-saved-posts.html?raw';


const PAGE = 'https://www.linkedin.com/my-items/saved-posts/';
const URN1 = 'urn:li:activity:7000000000000000001';
const URN2 = 'urn:li:activity:7000000000000000002';
const noSleep = async () => undefined;

beforeEach(() => {
  document.body.innerHTML = fixture;
});

describe('LinkedIn extraction (synthetic fixture)', () => {
  it('finds the cards that are posts and skips the one with no urn', () => {
    expect(findCards(document)).toHaveLength(2);
  });

  it('reads a post in the contract shape', () => {
    const post = extractPost(findCards(document)[0], PAGE);
    expect(post).toEqual({
      urn: URN1,
      url: `https://www.linkedin.com/feed/update/${URN1}`,
      author_name: 'Asha Rao',
      author_url: 'https://www.linkedin.com/in/asha-rao',
      author_headline: 'Staff Engineer at Example Co',
      // One line on purpose: the test DOM does not lay out line breaks the way a browser's innerText does.
      text: 'Notes on vector search in Postgres. Second sentence of the post.',
      posted_label: '2w •',
      media: [{ type: 'image', url: 'https://media.licdn.com/post-image-1.jpg', poster: null }], // the avatar is left out
      reshared: null,
    });
  });

  it('reads a reshared post separately from the post that shares it', () => {
    const post = extractPost(findCards(document)[1], PAGE)!;
    expect(post.urn).toBe(URN2);
    expect(post.author_name).toBe('Example Labs');
    expect(post.author_url).toBe('https://www.linkedin.com/company/example-labs/');
    expect(post.media).toEqual([]);
    expect(post.reshared).toMatchObject({
      urn: 'urn:li:activity:6999999999999999999',
      author_name: 'Ben Okafor',
      text: 'The original post about evals.',
      media: [{ type: 'video', url: null, poster: 'https://media.licdn.com/poster-9.jpg' }], // blob: address dropped
    });
  });

  it('presses "see more" only when it is a button outside any link', () => {
    const [first, second] = findCards(document);
    let pressed = 0;
    document.getElementById('see-more-2')!.addEventListener('click', () => pressed++);
    expect(expandSeeMore(first)).toBe(false); // its "see more" is text inside a link to the post
    expect(expandSeeMore(second)).toBe(true);
    expect(pressed).toBe(1);

    const link = document.createElement('a');
    link.href = '/feed/update/x';
    link.innerHTML = '<button class="reusable-search-show-more-link">see more</button>';
    first.append(link);
    expect(expandSeeMore(first)).toBe(false);
  });

  it('notices sign-in walls, captchas and dialogs, and ignores the messaging window', () => {
    expect(detectBlocker(document, PAGE)).toBeNull();
    expect(detectBlocker(document, 'https://www.linkedin.com/checkpoint/challenge/abc')).toMatch(/sign in/);
    expect(detectBlocker(document, 'https://www.linkedin.com/authwall?x=1')).toMatch(/sign in/);

    document.body.insertAdjacentHTML('beforeend', '<div class="msg-overlay-container"><div role="dialog">Messaging</div></div>');
    expect(detectBlocker(document, PAGE)).toBeNull();
    document.body.insertAdjacentHTML('beforeend', '<div id="captcha-internal"></div>');
    expect(detectBlocker(document, PAGE)).toMatch(/captcha/);
    document.getElementById('captcha-internal')!.remove();
    document.body.insertAdjacentHTML('beforeend', '<div role="dialog">Let us verify it is you</div>');
    expect(detectBlocker(document, PAGE)).toMatch(/unexpected dialog/);
  });
});

/** Makes the fixture's "..." button open a menu, the way the page would. */
function wireMenu(urn: string, entries: string[], onUnsave: (card: Element) => void = () => undefined) {
  const card = findCards(document).find((c) => c.querySelector(`[data-chameleon-result-urn="${urn}"]`))!;
  card.querySelector('button.artdeco-dropdown__trigger')!.addEventListener('click', () => {
    document.querySelector('.artdeco-dropdown__content--is-open')?.remove();
    const menu = document.createElement('div');
    menu.className = 'artdeco-dropdown__content--is-open';
    for (const label of entries) {
      const entry = document.createElement('div');
      entry.setAttribute('role', 'button');
      entry.textContent = label;
      if (/^unsave/i.test(label)) entry.addEventListener('click', () => (menu.remove(), onUnsave(card)));
      menu.append(entry);
    }
    document.body.append(menu);
  });
  return card;
}

describe('LinkedIn unsave (synthetic fixture)', () => {
  const deps = { sleep: noSleep, loadMore: () => undefined, href: () => PAGE };

  it('unsaves through the menu and confirms the post left the list', async () => {
    wireMenu(URN1, ['Copy link', 'Unsave'], (card) => card.remove());
    expect(await unsavePost(document, URN1, deps)).toMatchObject({ status: 'removed' });
    expect(findCards(document)).toHaveLength(1);
  });

  it('accepts the confirmation dialog when one appears', async () => {
    wireMenu(URN1, ['Unsave'], (card) => {
      document.body.insertAdjacentHTML('beforeend', '<div role="alertdialog" id="dlg">Remove this post from your saved items?<button>Cancel</button><button class="artdeco-button--primary" id="yes">Remove</button></div>');
      document.getElementById('yes')!.addEventListener('click', () => (document.getElementById('dlg')!.remove(), card.remove()));
    });
    expect(await unsavePost(document, URN1, deps)).toMatchObject({ status: 'removed' });
  });

  it('aborts on any other dialog, without pressing anything in it', async () => {
    let pressed = false;
    wireMenu(URN1, ['Unsave'], () => {
      document.body.insertAdjacentHTML('beforeend', '<div role="dialog">We noticed unusual activity<button id="ok">Continue</button></div>');
      document.getElementById('ok')!.addEventListener('click', () => (pressed = true));
    });
    expect(await unsavePost(document, URN1, deps)).toMatchObject({ status: 'failed', abort: true });
    expect(pressed).toBe(false);
  });

  it('aborts before touching anything when a sign-in wall is up', async () => {
    let opened = false;
    wireMenu(URN1, ['Unsave']).querySelector('button.artdeco-dropdown__trigger')!.addEventListener('click', () => (opened = true));
    expect(await unsavePost(document, URN1, { ...deps, href: () => 'https://www.linkedin.com/login' })).toMatchObject({ status: 'failed', abort: true });
    expect(opened).toBe(false);
  });

  it('fails when the list ends without the post; already gone only when the menu offers Save', async () => {
    expect(await unsavePost(document, 'urn:li:activity:1', deps)).toMatchObject({ status: 'failed' });
    wireMenu(URN2, ['Save']);
    expect(await unsavePost(document, URN2, deps)).toMatchObject({ status: 'already_gone' });
  });

  it('aborts, and does not say "already gone", when no saved post is recognised at all', async () => {
    document.body.innerHTML = '<main><div class="something-new">LinkedIn changed its markup</div></main>';
    expect(await unsavePost(document, URN1, deps)).toMatchObject({ status: 'failed', abort: true });
  });

  it('does not take a re-rendered card for a removal, nor a card that only embeds the post for the post', async () => {
    // LinkedIn replaces the card's node; the post is still saved.
    wireMenu(URN1, ['Unsave'], (card) => card.replaceWith(card.cloneNode(true)));
    expect(await unsavePost(document, URN1, deps)).toMatchObject({ status: 'failed' });

    // Another saved post reshares URN1: asked for URN1 alone, its menu must not be opened.
    const [first, second] = findCards(document);
    first.remove();
    second.querySelector('.entity-result__content-embedded-object')!.setAttribute('data-chameleon-result-urn', URN1);
    const own = second.querySelector('.entity-result')!; // its own urn in an attribute read after the embedded one's
    own.removeAttribute('data-chameleon-result-urn');
    own.setAttribute('data-urn', URN2);
    let opened = false;
    second.querySelector('button.artdeco-dropdown__trigger')!.addEventListener('click', () => (opened = true));
    expect(await unsavePost(document, URN1, deps)).toMatchObject({ status: 'failed' });
    expect(opened).toBe(false);
  });

  it('fails when the post stays listed after Unsave', async () => {
    wireMenu(URN1, ['Unsave']);
    expect(await unsavePost(document, URN1, deps)).toMatchObject({ status: 'failed' });
  });
});

describe('X un-bookmark (hand-built page)', () => {
  const limits = { gapMs: [0, 0] as const, renderTimeoutMs: 600, verifyTimeoutMs: 600 };
  const page = (id: string, testid: string) =>
    `<article data-testid="tweet"><a href="/someone/status/${id}"><time>1h</time></a><button data-testid="${testid}"></button></article>
     <article data-testid="tweet"><a href="/reply/status/999"><time>1h</time></a><button data-testid="removeBookmark" id="other"></button></article>`;

  it('presses the button of the right post when it is bookmarked, and checks the state changed', async () => {
    document.body.innerHTML = page('123', 'removeBookmark');
    const button = document.querySelector<HTMLElement>('article button')!;
    button.addEventListener('click', () => button.setAttribute('data-testid', 'bookmark'));
    let otherPressed = false;
    document.getElementById('other')!.addEventListener('click', () => (otherPressed = true));
    expect(await unbookmark(document, '123', noSleep, limits)).toMatchObject({ status: 'removed' });
    expect(otherPressed).toBe(false);
  });

  it('picks the post by its own permalink, not a post above it that links to it', async () => {
    document.body.innerHTML =
      `<article data-testid="tweet"><a href="/q/status/999"><time>2h</time></a><a href="/someone/status/123">quoted</a><button data-testid="removeBookmark" id="quoting"></button></article>` +
      page('123', 'removeBookmark');
    let quotingPressed = false;
    document.getElementById('quoting')!.addEventListener('click', () => (quotingPressed = true));
    const button = document.querySelectorAll<HTMLElement>('article button')[1];
    button.addEventListener('click', () => button.setAttribute('data-testid', 'bookmark'));
    expect(await unbookmark(document, '123', noSleep, limits)).toMatchObject({ status: 'removed' });
    expect(quotingPressed).toBe(false);
  });

  it('does not press anything when the post is not bookmarked', async () => {
    document.body.innerHTML = page('123', 'bookmark');
    let pressed = false;
    document.querySelector('article button')!.addEventListener('click', () => (pressed = true));
    expect(await unbookmark(document, '123', noSleep, limits)).toMatchObject({ status: 'already_gone' });
    expect(pressed).toBe(false);
  });

  it('fails when the button does not change, when the post is missing, and on a bad id', async () => {
    document.body.innerHTML = page('123', 'removeBookmark');
    expect(await unbookmark(document, '123', noSleep, limits)).toMatchObject({ status: 'failed' });
    expect(await unbookmark(document, '12', noSleep, limits)).toMatchObject({ status: 'failed' }); // 12 is not 123
    expect(await unbookmark(document, 'abc', noSleep, limits)).toMatchObject({ status: 'failed' });
  });
});
