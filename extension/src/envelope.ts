// Builds the capture envelope described in docs/CAPTURE_CONTRACT.md.

import { CLIENT_ID } from './config.ts';
import { folderIdFromPath, folderIdFromRequestUrl, kindForOperation, matchOperation } from './x/match.ts';
import type { XFolder } from './x/match.ts';
import type { LinkedInPost } from './linkedin/extract.ts';

export interface Envelope {
  source: 'x' | 'linkedin';
  kind: string;
  meta: {
    url: string;
    operation: string | null;
    folder_id: string | null;
    folder_name: string | null;
    captured_at: string;
    client: string;
  };
  payload: unknown;
}

/**
 * Envelope for one X GraphQL response, or null when the URL is not one Kosha
 * captures. For a folder timeline the folder id comes from the request, with
 * the address of the page as a fallback; the name comes from the last folder
 * list seen.
 */
export function buildXEnvelope(input: {
  url: string;
  payload: unknown;
  pageUrl: string;
  folders: XFolder[];
  now?: Date;
}): Envelope | null {
  const operation = matchOperation(input.url);
  if (!operation) return null;
  const kind = kindForOperation(operation);

  let folderId: string | null = null;
  if (kind === 'folder_page') {
    folderId = folderIdFromRequestUrl(input.url);
    if (!folderId) {
      try {
        folderId = folderIdFromPath(new URL(input.pageUrl).pathname);
      } catch {
        folderId = null;
      }
    }
  }

  return {
    source: 'x',
    kind,
    meta: {
      url: input.url,
      operation,
      folder_id: folderId,
      folder_name: folderId ? (input.folders.find((f) => f.id === folderId)?.name ?? null) : null,
      captured_at: (input.now ?? new Date()).toISOString(),
      client: CLIENT_ID,
    },
    payload: input.payload,
  };
}

export function buildLinkedInEnvelope(input: { posts: LinkedInPost[]; pageUrl: string; now?: Date }): Envelope {
  return {
    source: 'linkedin',
    kind: 'saved_posts',
    meta: {
      url: input.pageUrl,
      operation: null,
      folder_id: null,
      folder_name: null,
      captured_at: (input.now ?? new Date()).toISOString(),
      client: CLIENT_ID,
    },
    payload: { posts: input.posts, page_url: input.pageUrl },
  };
}
