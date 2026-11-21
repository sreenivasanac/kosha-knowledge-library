// All configuration, branding and pacing for the extension lives here.
// build.mjs imports this file too (to write manifest.json), so it must stay
// free of imports and of any browser API.

export const BRAND = {
  name: 'Kosha',
  extensionName: 'Kosha Capture',
  description: 'Captures your X bookmarks and LinkedIn saved posts into your Kosha library.',
  version: '0.1.0',
};

/** Goes into every envelope as meta.client. */
export const CLIENT_ID = `extension/${BRAND.version}`;

export const DEFAULT_SERVER_URL = 'http://localhost:8000';

export const API = {
  captures: '/api/captures',
  health: '/api/health',
  removalsPending: '/api/removals/pending',
  removalsResult: '/api/removals/result',
};

/**
 * Pages allowed to message the extension (the Kosha web app). The manifest
 * cannot follow the server URL set in the popup, so this list is fixed at
 * build time; the service worker then also checks that the sender's origin
 * equals the configured server origin. A pattern without a port matches
 * every port. Add your own host here and rebuild if Kosha is not on localhost.
 */
export const EXTERNALLY_CONNECTABLE = ['http://localhost/*', 'http://127.0.0.1/*'];

/** Hosts the service worker may call without asking. Others are requested when the URL is saved. */
export const LOCAL_SERVER_HOSTS = ['http://localhost/*', 'http://127.0.0.1/*'];

export type Range = readonly [min: number, max: number];

export const X = {
  origin: 'https://x.com',
  /** X redirects this to /i/history (seen November 2026). */
  bookmarksUrl: 'https://x.com/i/bookmarks',
  /** Unverified: assumed address of one bookmark folder. */
  folderUrl: (id: string) => `https://x.com/i/bookmarks/${id}`,
  /**
   * The GraphQL operations to capture. Same match that was proven in the page,
   * plus an end anchor so that mutations whose names merely begin with
   * "Bookmarks" are left alone.
   */
  graphqlPattern: /\/graphql\/[^/]+\/(Bookmarks|BookmarkFolderTimeline|BookmarkFolders\w*)(?=[?#]|$)/,
  /** The whole-bookmarks page, under either address. */
  bookmarksRootPath: /^\/i\/(?:bookmarks|history)\/?$/,
  /** One folder's page; group 1 is the folder id. */
  folderPath: /^\/i\/(?:bookmarks|history)\/(\d+)/,
  /** Name of the folder id inside a BookmarkFolderTimeline request's `variables`. Unverified. */
  folderIdVariable: 'bookmark_collection_id',
  /** Time given to X's client-side redirect before the page address is judged. */
  settleMs: 3000,
  selectors: {
    post: 'article[data-testid="tweet"]',
    bookmarked: '[data-testid="removeBookmark"]',
    notBookmarked: '[data-testid="bookmark"]',
  },
};

export const BACKFILL_PACING = {
  scrollGapMs: [2500, 6000] as Range,
  /** Share of steps that are a small scroll up instead of a scroll down. */
  scrollUpChance: 0.12,
  scrollUpPx: [150, 600] as Range,
  restEveryPages: [20, 35] as Range,
  restMs: [20_000, 60_000] as Range,
  /** Wait after X answers HTTP 429. */
  rateLimitBackoffMs: [6 * 60_000, 12 * 60_000] as Range,
  stopAfterEmptyResponses: 2,
  stopAfterIdleScrolls: 30,
};

export const OUTBOX = {
  dbName: 'kosha-outbox',
  /** Envelopes posted per flush pass. */
  batchSize: 10,
  backoffBaseMs: 5000,
  backoffMaxMs: 15 * 60_000,
  flushAlarmMinutes: 1,
};

export const LINKEDIN = {
  origin: 'https://www.linkedin.com',
  savedPostsUrl: 'https://www.linkedin.com/my-items/saved-posts/',
  savedPostsPath: '/my-items/saved-posts',
  capture: {
    scrollGapMs: [4000, 9000] as Range,
    /** Capture ends after this many scrolls in a row that show no new post. */
    stopAfterIdleScrolls: 5,
  },
};

export const REMOVAL = {
  x: {
    gapMs: [3000, 8000] as Range,
    /** How long a post page may take to load and show the post. */
    renderTimeoutMs: 25_000,
    /** How long the bookmark button may take to change after the click. */
    verifyTimeoutMs: 6000,
  },
  linkedin: {
    gapMs: [8000, 20_000] as Range,
    maxPerRun: 40,
    /** Scrolls spent looking for one post before giving up on it. */
    maxSearchScrolls: 25,
    /** Scrolls in a row with no new post that mean the end of the list. */
    endOfListScrolls: 3,
    menuTimeoutMs: 4000,
    verifyTimeoutMs: 6000,
    /** The saved-posts tab is opened in front: LinkedIn loads more posts only in a visible tab. */
    openTabActive: true,
  },
  /** A service worker is stopped after 30 s without events; this keeps it alive during a run. */
  keepAliveMs: 20_000,
};

/** Messages the Kosha web app may send (chrome.runtime.sendMessage(extensionId, {type})). */
export const EXTERNAL_MESSAGES = {
  ping: 'kosha:ping',
  removeFromSources: 'kosha:remove_from_sources',
  removalStatus: 'kosha:removal_status',
};

/** chrome.storage.local keys. */
export const STORAGE = {
  settings: 'settings',
  backfill: 'backfill',
  xFolders: 'xFolders',
  lastCapture: 'lastCapture',
  removalRun: 'removalRun',
};
