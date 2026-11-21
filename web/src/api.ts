/** Typed access to the Kosha backend. All calls are relative (/api, /media). */
import { QueryClient, useQuery } from '@tanstack/react-query';
import type { Mode } from './constants';
import { CSRF_HEADER_VALUE, DEFAULT_SORT } from './constants';
import { tokenize } from './query';

export interface Author {
  name: string | null;
  handle: string | null;
  avatar: string | null;
  /** LinkedIn's line under the name, when the server sends it. */
  headline?: string | null;
}
export interface Media {
  id: number;
  type: 'photo' | 'video' | 'animated_gif' | 'audio' | 'document' | string;
  position: number;
  url: string;
  preview_url: string | null;
  remote_url: string | null;
  stored: boolean;
  mime_type: string | null;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  alt_text: string | null;
  variants: { url: string; bitrate?: number | null; content_type?: string }[] | null;
  extract_status: string;
  extract_kind: string | null;
  extract_text?: string | null;
}
export interface Link {
  url: string;
  domain: string | null;
  title: string | null;
  description: string | null;
  image_url: string | null;
}
export interface ItemTag {
  tag_id: number;
  name: string;
  path: string[];
  role: 'primary' | 'secondary' | 'other';
  status: string;
  color: string | null;
  attached_by: string;
  confidence: number | null;
}
export interface Relation {
  source_id: string;
  item_id: number | null;
  target_state: 'held' | 'missing' | 'unavailable' | string;
  author_handle?: string | null;
}
export interface Item {
  id: number;
  source: string;
  source_id: string;
  is_saved: boolean;
  url: string | null;
  title: string | null;
  body: string | null;
  summary: string | null;
  author: Author;
  posted_at: string | null;
  saved_at: string | null;
  saved_at_approximate: boolean;
  captured_at: string;
  content_types: string[];
  lang: string | null;
  likes: number | null;
  reposts: number | null;
  replies: number | null;
  views: number | null;
  duration_s: number | null;
  starred: boolean;
  note: string | null;
  read: boolean;
  archived: boolean;
  archived_at: string | null;
  unavailable_reason: string | null;
  media: Media[];
  links: Link[];
  tags: ItemTag[];
  collections: { id: number; name: string; kind: string }[];
  quote: Relation | null;
  quoted: Item | null;
  reply_to: Relation | null;
  thread: { root_source_id: string; member_count: number } | null;
  thread_items?: Item[];
  /** Present on search results once the backend's search is in. */
  snippet?: string | null;
  score?: number | null;
}
export interface ItemPage {
  items: Item[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
  snapshot_id?: string | number | null;
  /** Something the server wants said about this result (for example: semantic search fell back to keyword). */
  notice?: string | null;
}
export interface TagNode {
  id: number;
  parent_id: number | null;
  name: string;
  description: string | null;
  status: string;
  origin: string;
  color: string | null;
  own_count: number;
  count: number;
  children: TagNode[];
}
export interface Collection {
  id: number;
  name: string;
  kind: string;
  source: string;
  count: number;
}
export interface SavedView {
  id: number;
  name: string;
  query: string;
  /** The app's own state saved with the view. */
  params?: { mode?: Mode; sort?: string; view?: string } | null;
}
export interface ItemList {
  id: number;
  name: string;
  count?: number;
}
export interface Suggestion {
  insert: string;
  label: string;
  count: number | null;
  kind: string;
}
export interface Suggest {
  replace_range: [number, number];
  suggestions: Suggestion[];
}

/** An HTTP error with whatever the server said. `position` is set for search-bar syntax errors. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public position?: number,
  ) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const response = await fetch(path, {
    method: init.method ?? 'GET',
    credentials: 'same-origin',
    // The backend's CSRF guard requires this header on every cookie-authenticated write.
    headers: { 'X-Requested-With': CSRF_HEADER_VALUE, ...(init.body === undefined ? {} : { 'content-type': 'application/json' }) },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (response.ok) return (response.status === 204 ? undefined : await response.json()) as T;
  const payload = await response.json().catch(() => null);
  const detail = payload?.detail;
  // Session expired: drop the cached user so the router sends the user to the login page.
  if (response.status === 401 && !path.startsWith('/api/auth/')) queryClient.setQueryData(['me'], null);
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) throw new ApiError(response.status, detail.message ?? 'Request failed', detail.position);
  const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join('; ') : response.statusText;
  throw new ApiError(response.status, message || `HTTP ${response.status}`);
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
});

/**
 * Which optional endpoints this server has, read once from its OpenAPI
 * document. Screens that need a missing one say so inline instead of failing.
 */
export interface Capabilities {
  search: boolean;
  parse: boolean;
  suggest: boolean;
  savedViews: boolean;
  lists: boolean;
  jev: boolean;
  mediaText: boolean;
  removals: boolean;
  tokens: boolean;
  sync: boolean;
  ask: boolean;
  query: boolean;
  spend: boolean;
  mediaStatus: boolean;
  /** Rename and delete a list. */
  listEdit: boolean;
}
const NO_CAPABILITIES: Capabilities = { search: false, parse: false, suggest: false, savedViews: false, lists: false, jev: false, mediaText: false, removals: false, tokens: false, sync: false, ask: false, query: false, spend: false, mediaStatus: false, listEdit: false };

async function fetchCapabilities(): Promise<Capabilities> {
  const doc = await api<{ paths: Record<string, Record<string, { parameters?: { name: string }[] }>> }>('/api/openapi.json');
  const has = (path: string) => path in doc.paths;
  return {
    search: !!doc.paths['/api/items']?.get?.parameters?.some((p) => p.name === 'q'),
    parse: has('/api/search/parse'),
    suggest: has('/api/search/suggest'),
    savedViews: has('/api/saved-views'),
    lists: has('/api/lists'),
    jev: has('/api/jev/check'),
    mediaText: has('/api/media-text/estimate'),
    removals: has('/api/removals/pending'),
    tokens: has('/api/tokens'),
    sync: has('/api/sync'),
    ask: has('/api/ask'),
    query: has('/api/query'),
    spend: has('/api/spend'),
    mediaStatus: has('/api/media-text/status'),
    listEdit: !!doc.paths['/api/lists/{list_id}']?.delete,
  };
}
export const capabilitiesQuery = { queryKey: ['capabilities'], queryFn: fetchCapabilities, staleTime: 60_000 };
export const useCapabilities = (): Capabilities => useQuery(capabilitiesQuery).data ?? NO_CAPABILITIES;
export const getCapabilities = (): Capabilities => queryClient.getQueryData<Capabilities>(['capabilities']) ?? NO_CAPABILITIES;

export interface ItemsParams {
  q: string;
  mode: Mode;
  sort: string;
  page: number;
  size: number;
  archived: boolean;
  /** A result set from a Jev check; filters the view to what it kept. */
  snap?: string;
}

/** Snapshot ids by search, so page 3 stays page 3 while reading. Not part of the query key. */
const snapshots = new Map<string, string>();

/**
 * For a server without the qualifier search: the handful of qualifiers the old
 * filter parameters can express. `leftover` is what could not be applied.
 * Remove once every server has `q`.
 */
export function legacyFilters(q: string, tags: TagNode[], collections: Collection[]): { params: Record<string, string>; leftover: string[] } {
  const params: Record<string, string> = {};
  const leftover: string[] = [];
  const findTag = (path: string, nodes: TagNode[], prefix = ''): TagNode | undefined => {
    for (const n of nodes) {
      const full = prefix + n.name;
      if (full.toLowerCase() === path.toLowerCase()) return n;
      const hit = findTag(path, n.children, full + '/');
      if (hit) return hit;
    }
  };
  for (const t of tokenize(q)) {
    const v = (t.value ?? '').toLowerCase();
    const is = t.kind === 'qualifier' && !t.negated ? `${t.key}:${v}` : '';
    if (is === 'is:starred') params.starred = 'true';
    else if (is === 'is:unread') params.read = 'false';
    else if (is === 'is:read') params.read = 'true';
    else if (is === 'is:untagged' || is === 'tags:0') params.untagged = 'true';
    else if (is === 'is:archived') params.archived = 'true';
    else if (t.key === 'source' && is) params.source = v;
    else if (t.key === 'type' && is) params.content_type = v;
    else if (t.key === 'from' && is && !v.includes(',')) params.author = v;
    else if (t.key === 'saved' && /^\d{4}$/.test(v)) params.year = v;
    else if (t.key === 'folder' && is && collections.some((c) => c.name.toLowerCase() === v)) params.collection_id = String(collections.find((c) => c.name.toLowerCase() === v)!.id);
    else if (t.kind === 'tag' && !t.negated && findTag(t.value!, tags)) params.tag_id = String(findTag(t.value!, tags)!.id);
    else leftover.push(t.raw);
  }
  return { params, leftover };
}

export type ItemPageResult = ItemPage & { leftover: string[] };

export async function fetchItems(p: ItemsParams): Promise<ItemPageResult> {
  const search = new URLSearchParams({ page: String(p.page), page_size: String(p.size) });
  if (p.archived) search.set('archived', 'true');
  let leftover: string[] = [];
  const caps = await queryClient.ensureQueryData(capabilitiesQuery);
  if (caps.search) {
    const snapKey = JSON.stringify([p.q, p.mode, p.sort, p.archived]);
    // Syntax is checked first, so a half-typed qualifier shows its error without a failed request.
    if (p.q && caps.parse) {
      const parsed = await api<{ ok: boolean; error?: { message: string; position: number } | null }>('/api/search/parse', { method: 'POST', body: { q: p.q } });
      if (!parsed.ok && parsed.error) throw new ApiError(422, parsed.error.message, parsed.error.position);
    }
    if (p.q) search.set('q', p.q);
    if (p.mode !== 'hybrid') search.set('mode', p.mode);
    if (p.sort !== DEFAULT_SORT) search.set('sort', p.sort);
    const snapshot = p.snap ?? (p.page > 1 ? snapshots.get(snapKey) : undefined);
    if (snapshot) search.set('snapshot_id', snapshot);
    const page = await api<ItemPage>(`/api/items?${search}`);
    if (page.snapshot_id != null) snapshots.set(snapKey, String(page.snapshot_id));
    return { ...page, leftover };
  }
  const [tags, collections] = await Promise.all([queryClient.ensureQueryData(tagsQuery), queryClient.ensureQueryData(collectionsQuery)]);
  const legacy = legacyFilters(p.q, tags, collections);
  leftover = legacy.leftover;
  for (const [k, v] of Object.entries(legacy.params)) search.set(k, v);
  return { ...(await api<ItemPage>(`/api/items?${search}`)), leftover };
}

export const tagsQuery = { queryKey: ['tags'], queryFn: () => api<TagNode[]>('/api/tags') };
export const collectionsQuery = { queryKey: ['collections'], queryFn: () => api<Collection[]>('/api/collections') };
export const statsQuery = { queryKey: ['stats'], queryFn: () => api<Record<string, number | string | null>>('/api/stats') };
export const facetsQuery = { queryKey: ['facets'], queryFn: () => api<{ sources: { value: string; count: number }[]; years: { value: string; count: number }[] }>('/api/facets') };
export const settingsQuery = { queryKey: ['settings'], queryFn: () => api<Record<string, boolean | number | string>>('/api/settings') };
export const keybindingsQuery = { queryKey: ['keybindings'], queryFn: () => api<Record<string, string>>('/api/keybindings'), staleTime: Infinity };
export const meQuery = { queryKey: ['me'], queryFn: () => api<{ id: number; email: string }>('/api/auth/me').catch(() => null), staleTime: Infinity };

export const useSavedViews = () => {
  const enabled = useCapabilities().savedViews;
  return useQuery({ queryKey: ['saved-views'], queryFn: () => api<SavedView[]>('/api/saved-views'), enabled }).data ?? [];
};
export const useLists = () => {
  const enabled = useCapabilities().lists;
  return useQuery({ queryKey: ['lists'], queryFn: () => api<ItemList[]>('/api/lists'), enabled }).data ?? [];
};

/** A tag tree as a flat list with full paths, for type-ahead. */
export interface FlatTag {
  id: number;
  path: string;
  node: TagNode;
  depth: number;
}
export function flattenTags(nodes: TagNode[], prefix = '', depth = 0): FlatTag[] {
  return nodes.flatMap((node) => {
    const path = prefix + node.name;
    return [{ id: node.id, path, node, depth }, ...flattenTags(node.children, path + '/', depth + 1)];
  });
}

/** After anything that changes items or tags: refresh every list and count. */
export function invalidateLibrary() {
  for (const key of ['items', 'item', 'stats', 'tags', 'facets', 'collections', 'lists', 'suggest']) void queryClient.invalidateQueries({ queryKey: [key] });
}
