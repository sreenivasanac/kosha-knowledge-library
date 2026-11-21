/** Branding and tunable values, in one place. */
export const APP_NAME = 'Kosha';
export const APP_TAGLINE = 'Your saved posts, searchable and organised';
/** The landing page's headline and the description search engines show. Keep index.html's meta tags in step. */
export const HERO_LINE = 'Search everything you saved, on a server you own.';
export const APP_DESCRIPTION = 'Kosha is a free, open-source library for your X bookmarks and LinkedIn saves. It runs on your own server, organises posts in a tag tree, and searches them by keyword and by meaning.';

export const REPO_URL = 'https://github.com/sreenivasanac/kosha-knowledge-library';
export const LICENSE_NAME = 'MIT';
export const LINKS = {
  repo: REPO_URL,
  readme: `${REPO_URL}#readme`,
  extension: `${REPO_URL}/tree/HEAD/extension#readme`,
  mcpDocs: `${REPO_URL}/blob/HEAD/docs/MCP.md`,
  license: `${REPO_URL}/blob/HEAD/LICENSE`,
};
export const AUTHOR = {
  name: 'Sreenivasan AC',
  github: 'https://github.com/sreenivasanac',
  linkedin: 'https://www.linkedin.com/in/sreenivasan-ac',
  x: 'https://x.com/sreenivasan_ac',
};

/** The server's rule for a new password; the forms check it before sending. */
export const PASSWORD_MIN_LENGTH = 10;
export const DEMO_BANNER = 'Demo — changes are reset every night. Some features are off.';
export const DEMO_UNAVAILABLE = 'Not available in the demo';
/** How often the Storage card asks for progress while files are being moved. */
export const MIGRATE_POLL_MS = 2000;

export const PAGE_SIZES = [25, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = 50;

export const VIEWS = ['list', 'grid', 'compact'] as const;
export type View = (typeof VIEWS)[number];

export const MODES = ['hybrid', 'keyword', 'semantic'] as const;
export type Mode = (typeof MODES)[number];

export const SORTS = [
  { value: 'default', label: 'Default order' },
  { value: 'saved', label: 'Saved, newest first' },
  { value: 'posted', label: 'Posted, newest first' },
  { value: 'likes', label: 'Most liked' },
  { value: 'views', label: 'Most viewed' },
] as const;
/** Sends no `sort`: the server ranks by relevance when there are words, by saved order otherwise. */
export const DEFAULT_SORT = 'default';

/** Wait this long after the last keystroke before running a search. */
export const SEARCH_DEBOUNCE_MS = 250;
/** A second key of a sequence such as `g a` must follow within this time. */
export const SEQUENCE_TIMEOUT_MS = 1200;
export const TOAST_MS = 6000;
export const RECENT_SEARCHES_MAX = 8;
/** A match excerpt is shown on a card only when the body is longer than this (the match may be below the fold). */
export const CLAMP_CHARS = 420;
/** Body text taller than this many lines is clamped on a card, with "more" to expand in place. */
export const CLAMP_LINES = 8;
/** Tallest a photo or video is drawn, in pixels; the lightbox shows the full size. */
export const MEDIA_MAX_HEIGHT = { list: 420, quote: 260, drawer: 560 };
/** Jev's cut-off for "yes" (docs/MCP.md); 0.8 to 0.9 is the band worth a spot check. */
export const JEV_DEFAULT_THRESHOLD = 0.8;
export const DOUBTFUL_BELOW = 0.9;
/** Example shown in the Jev dialog: Jev reads literally, so a full sentence works best. */
export const JEV_EXAMPLE_QUESTION = 'Is this post about retrieval-augmented generation (RAG)?';
export const PIPELINE_HISTORY_MAX = 50;
/** The one sync `POST /api/sync` runs on the server: the official X API bookmark sync. */
export const X_API_SYNC_SOURCE = 'x_api';
/** A media-text run estimated above this (USD) does not put the keyboard focus on Start. */
export const MEDIA_TEXT_CAREFUL_USD = 1;
/** How often the sidebar asks the server for sync status. */
export const SYNC_POLL_MS = 60_000;
/** How often the Archive page asks the extension for removal progress. */
export const REMOVAL_POLL_MS = 2000;

/** Colours offered for tags on the tag management page. */
export const TAG_COLORS = ['#2F3FB8', '#0E7C86', '#1F7A4D', '#B7791F', '#B3261E', '#8E3B9E', '#5B6675'];

export const STORAGE_KEYS = {
  sidebar: 'kosha.sidebar',
  recent: 'kosha.recentSearches',
  extensionId: 'kosha.extensionId',
  position: 'kosha.position:',
  pipelineHistory: 'kosha.pipelineHistory',
  theme: 'kosha.theme',
  view: 'kosha.view',
  pageSize: 'kosha.pageSize',
  /** Set while this browser holds a session, so the public landing page can skip the "who am I" call. */
  session: 'kosha.session',
};

/** Sent as X-Requested-With on every request; the backend rejects writes without it. */
export const CSRF_HEADER_VALUE = 'kosha';
