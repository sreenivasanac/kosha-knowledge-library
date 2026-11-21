/** Branding and tunable values, in one place. */
export const APP_NAME = 'Kosha';
export const APP_TAGLINE = 'Your saved posts, searchable and organised';

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
/** Body text longer than this is clamped on a card, with "more" to expand in place. */
export const CLAMP_CHARS = 420;
export const CLAMP_LINES = 9;
export const JEV_DEFAULT_THRESHOLD = 0.5;
/** How often the Archive page asks the extension for removal progress. */
export const REMOVAL_POLL_MS = 2000;

/** Colours offered for tags on the tag management page. */
export const TAG_COLORS = ['#2F3FB8', '#0E7C86', '#1F7A4D', '#B7791F', '#B3261E', '#8E3B9E', '#5B6675'];

export const STORAGE_KEYS = {
  sidebar: 'kosha.sidebar',
  recent: 'kosha.recentSearches',
  extensionId: 'kosha.extensionId',
  position: 'kosha.position:',
};

/** Sent as X-Requested-With on every request; the backend rejects writes without it. */
export const CSRF_HEADER_VALUE = 'kosha';
