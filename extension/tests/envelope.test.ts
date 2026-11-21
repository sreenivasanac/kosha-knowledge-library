import { describe, expect, it } from 'vitest';
import { CLIENT_ID } from '../src/config.ts';
import { buildLinkedInEnvelope, buildXEnvelope } from '../src/envelope.ts';

const now = new Date('2026-11-21T10:00:00Z');
const folders = [{ id: '11', name: 'AI' }];
const api = (op: string, variables: object = {}) =>
  `https://x.com/i/api/graphql/hash/${op}?variables=${encodeURIComponent(JSON.stringify(variables))}`;

describe('buildXEnvelope', () => {
  it('builds a bookmarks_page envelope as the contract describes', () => {
    const payload = { data: {} };
    const url = api('Bookmarks', { count: 20 });
    expect(buildXEnvelope({ url, payload, pageUrl: 'https://x.com/i/history', folders, now })).toEqual({
      source: 'x',
      kind: 'bookmarks_page',
      meta: { url, operation: 'Bookmarks', folder_id: null, folder_name: null, captured_at: '2026-11-21T10:00:00.000Z', client: CLIENT_ID },
      payload,
    });
  });

  it('sets folder id and name on a folder page, from the request', () => {
    const env = buildXEnvelope({ url: api('BookmarkFolderTimeline', { bookmark_collection_id: '11' }), payload: {}, pageUrl: 'https://x.com/home', folders, now });
    expect(env?.kind).toBe('folder_page');
    expect(env?.meta.folder_id).toBe('11');
    expect(env?.meta.folder_name).toBe('AI');
  });

  it('falls back to the page address for the folder id, and leaves an unknown name null', () => {
    const env = buildXEnvelope({ url: api('BookmarkFolderTimeline'), payload: {}, pageUrl: 'https://x.com/i/bookmarks/99', folders, now });
    expect(env?.meta.folder_id).toBe('99');
    expect(env?.meta.folder_name).toBeNull();
  });

  it('labels the folder list and refuses other URLs', () => {
    expect(buildXEnvelope({ url: api('BookmarkFoldersSlice'), payload: {}, pageUrl: '', folders, now })?.kind).toBe('folder_list');
    expect(buildXEnvelope({ url: api('HomeTimeline'), payload: {}, pageUrl: '', folders, now })).toBeNull();
  });
});

describe('buildLinkedInEnvelope', () => {
  it('wraps posts as kind saved_posts with page_url', () => {
    const pageUrl = 'https://www.linkedin.com/my-items/saved-posts/';
    const env = buildLinkedInEnvelope({ posts: [], pageUrl, now });
    expect(env).toMatchObject({ source: 'linkedin', kind: 'saved_posts', payload: { posts: [], page_url: pageUrl } });
    expect(env.meta.client).toBe(CLIENT_ID);
  });
});
