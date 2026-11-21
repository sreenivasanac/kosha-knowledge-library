// Recognising X's bookmark responses and reading the little the extension
// needs from them (post counts, folder names). Full parsing is server-side.
//
// The URL match and the path to the timeline instructions follow
// twitter-web-exporter (c) prinsss, MIT licence: src/modules/bookmarks/api.ts.
// See ../../NOTICE.

import { X } from '../config.ts';

/** Marks the window messages the page script sends to the content script. */
export const INPAGE_SOURCE = 'kosha-inpage';

export interface InpageMessage {
  source: typeof INPAGE_SOURCE;
  url: string;
  status: number;
  /** Raw response text. */
  body: string;
}

export type XKind = 'bookmarks_page' | 'folder_page' | 'folder_list';

export interface XFolder {
  id: string;
  name: string;
}

/** The GraphQL operation name when the URL is one Kosha captures, else null. */
export function matchOperation(url: string): string | null {
  return X.graphqlPattern.exec(url)?.[1] ?? null;
}

export function kindForOperation(operation: string): XKind {
  if (operation === 'Bookmarks') return 'bookmarks_page';
  if (operation === 'BookmarkFolderTimeline') return 'folder_page';
  return 'folder_list'; // BookmarkFoldersSlice was the name seen in November 2026
}

/** The folder a BookmarkFolderTimeline request asks for, read from its `variables` parameter. */
export function folderIdFromRequestUrl(url: string): string | null {
  try {
    const variables = JSON.parse(new URL(url).searchParams.get('variables') ?? '{}');
    const id = variables[X.folderIdVariable];
    return id == null ? null : String(id);
  } catch {
    return null;
  }
}

/** The folder id when the page address is a bookmark folder. */
export function folderIdFromPath(pathname: string): string | null {
  return X.folderPath.exec(pathname)?.[1] ?? null;
}

// The timeline sits at data.bookmark_timeline_v2.timeline.instructions for
// Bookmarks; the folder timeline uses another key, so the first
// `instructions` array found near the top is used instead of a fixed path.
function findInstructions(node: unknown, depth = 0): unknown[] | null {
  if (!node || typeof node !== 'object' || depth > 6) return null;
  const record = node as Record<string, unknown>;
  if (Array.isArray(record.instructions)) return record.instructions;
  for (const value of Object.values(record)) {
    const found = findInstructions(value, depth + 1);
    if (found) return found;
  }
  return null;
}

/** Number of posts in a bookmark timeline response (cursor entries are not posts). */
export function countPosts(payload: unknown): number {
  let count = 0;
  for (const instruction of findInstructions(payload) ?? []) {
    const entries = (instruction as { entries?: { entryId?: string }[] }).entries;
    if (!Array.isArray(entries)) continue;
    count += entries.filter((e) => typeof e?.entryId === 'string' && e.entryId.startsWith('tweet-')).length;
  }
  return count;
}

/** Folders in a folder-list response (shape seen in the BookmarkFoldersSlice capture). */
export function parseFolderList(payload: unknown): XFolder[] {
  const items = (payload as any)?.data?.viewer?.user_results?.result?.bookmark_collections_slice?.items;
  if (!Array.isArray(items)) return [];
  return items
    .filter((item) => item && item.id != null)
    .map((item) => ({ id: String(item.id), name: String(item.name ?? '') }));
}
