// Every LinkedIn selector and label the extension depends on, in one place.
//
// UNVERIFIED: none of this was run against the live site. The class names are
// the ones LinkedIn's saved-posts page (a search-results style list) has used,
// written from memory, with broader fallbacks after them. LinkedIn changes its
// markup often and is moving to generated class names, so expect to edit this
// file after the first real run. Each list is tried in order; the first
// selector that matches wins.

export const SELECTORS = {
  /** One saved post. Fallback when none match: the nearest <li> around any link to a post (see extract.ts). */
  card: ['li.reusable-search__result-container', 'div[data-chameleon-result-urn]', 'li:has([data-urn^="urn:li:"])'],

  /** A link to the post itself; its address carries the urn. */
  postLink: 'a[href*="/feed/update/urn:li:"]',

  /** Attributes that may carry the post's urn, on the card or inside it. */
  urnAttributes: ['data-chameleon-result-urn', 'data-urn', 'data-entity-urn', 'data-id'],

  /** Link to the author's profile or company page. */
  authorLink: ['.entity-result__content-actor a[href*="/in/"]', '.entity-result__content-actor a[href*="/company/"]', 'a[href*="/in/"]', 'a[href*="/company/"]'],

  /** The author's name. The aria-hidden span holds the name without screen-reader extras. */
  authorName: [
    '.entity-result__title-text a span[aria-hidden="true"]',
    '.entity-result__title-text a',
    '.update-components-actor__title span[aria-hidden="true"]',
  ],

  authorHeadline: ['.entity-result__primary-subtitle', '.update-components-actor__description span[aria-hidden="true"]'],

  /** The relative date, for example "2w". */
  postedLabel: [
    '.entity-result__content-actor p.t-black--light span[aria-hidden="true"]',
    '.entity-result__secondary-subtitle',
    '.update-components-actor__sub-description span[aria-hidden="true"]',
    'time',
  ],

  text: ['.entity-result__content-summary', '.entity-result__summary', '.update-components-text', '.feed-shared-inline-show-more-text'],

  /** The control that shows the full text. Pressed only when it is a button with no link (no navigation). */
  seeMore: ['button.reusable-search-show-more-link', 'button.inline-show-more-text__button', 'button[aria-label*="see more" i]'],

  /** A shared post, article or other embedded object inside a saved post. */
  reshared: ['.entity-result__content-embedded-object', '.update-components-mini-update-v2', '.feed-shared-update-v2__reshared-content'],

  /** Images that are profile pictures, not post media. */
  avatar: '.presence-entity, .ivm-image-view-model, .EntityPhoto-circle-3, [class*="EntityPhoto"], [class*="avatar"]',

  /** The "..." menu of a saved post. */
  overflowButton: [
    '.entity-result__actions-overflow-menu-dropdown button',
    'button[aria-label*="more actions" i]',
    'button.artdeco-dropdown__trigger',
  ],

  /** Entries of the open menu. */
  menuItem: ['.artdeco-dropdown__content--is-open [role="button"]', '.artdeco-dropdown__item', '[role="menuitem"]'],

  /** The button that loads the next batch of saved posts, when LinkedIn shows one instead of loading on scroll. */
  showMoreResults: ['button.scaffold-finite-scroll__load-button'],

  /** Any dialog. */
  dialog: '[role="alertdialog"], [role="dialog"], .artdeco-modal',

  /** Dialog-like parts of the normal page that are not a reason to stop (the messaging windows). */
  dialogIgnore: '.msg-overlay-container, #msg-overlay, .msg-overlay-conversation-bubble',

  /** The button that accepts a confirmation dialog. */
  dialogConfirm: ['button.artdeco-button--primary', 'button'],

  /** Signs of a sign-in wall or captcha. */
  blockers: [
    'iframe[src*="captcha" i]',
    '#captcha-internal',
    'form.login__form',
    'form[action*="checkpoint"]',
    'input[name="session_password"]',
  ],
};

export const LABELS = {
  /** Menu entry that removes the post from Saved. */
  unsave: /^\s*(unsave|remove from saved)/i,
  /** Menu entry shown when the post is no longer saved. */
  save: /^\s*save\b/i,
  /** A dialog whose text matches this is the expected "really unsave?" question. */
  unsaveDialog: /unsave|remove (this )?(post )?from (your )?saved/i,
  /** The accepting button inside that dialog. */
  confirmButton: /^\s*(unsave|remove|yes|confirm)/i,
  /** Trailing "…see more" left in the text when it could not be expanded. */
  seeMoreSuffix: /\s*(…|\.\.\.)\s*(see more|more)\s*$/i,
};

/** Addresses LinkedIn sends you to when it wants a sign-in or a check. */
export const BLOCKER_PATHS = /^\/(checkpoint|login|uas|authwall|signup)(\/|$)/;

/** A post urn as it appears in attributes and links. */
export const URN_PATTERN = /urn:li:(?:activity|ugcPost|share):\d+/;
