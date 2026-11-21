import { describe, expect, it } from 'vitest';
import { countPosts, folderIdFromPath, folderIdFromRequestUrl, kindForOperation, matchOperation, parseFolderList } from '../src/x/match.ts';

const api = (op: string, query = '?variables=%7B%7D') => `https://x.com/i/api/graphql/fjf5b5Lu82lYMSBRhppK2g/${op}${query}`;

describe('matchOperation', () => {
  it('matches the three bookmark operations', () => {
    expect(matchOperation(api('Bookmarks'))).toBe('Bookmarks');
    expect(matchOperation(api('BookmarkFolderTimeline'))).toBe('BookmarkFolderTimeline');
    expect(matchOperation(api('BookmarkFoldersSlice'))).toBe('BookmarkFoldersSlice');
    expect(matchOperation(api('Bookmarks', ''))).toBe('Bookmarks');
  });

  it('ignores other requests, including mutations that only start with the same letters', () => {
    expect(matchOperation(api('HomeTimeline'))).toBeNull();
    expect(matchOperation(api('CreateBookmark'))).toBeNull();
    expect(matchOperation(api('DeleteBookmark'))).toBeNull();
    expect(matchOperation(api('BookmarksAllDelete'))).toBeNull();
    expect(matchOperation(api('BookmarkSearchTimeline'))).toBeNull();
    expect(matchOperation('https://x.com/i/bookmarks')).toBeNull();
  });
});

describe('kinds and folders', () => {
  it('maps operations to envelope kinds', () => {
    expect(kindForOperation('Bookmarks')).toBe('bookmarks_page');
    expect(kindForOperation('BookmarkFolderTimeline')).toBe('folder_page');
    expect(kindForOperation('BookmarkFoldersSlice')).toBe('folder_list');
  });

  it('reads the folder id from the request variables and from the page address', () => {
    const variables = encodeURIComponent(JSON.stringify({ bookmark_collection_id: '1800000000000000001', count: 20 }));
    expect(folderIdFromRequestUrl(api('BookmarkFolderTimeline', `?variables=${variables}`))).toBe('1800000000000000001');
    expect(folderIdFromRequestUrl(api('BookmarkFolderTimeline', '?variables=notjson'))).toBeNull();
    expect(folderIdFromPath('/i/bookmarks/1800000000000000001')).toBe('1800000000000000001');
    expect(folderIdFromPath('/i/history')).toBeNull();
  });

  it('reads folders from the folder list response', () => {
    const payload = { data: { viewer: { user_results: { result: { bookmark_collections_slice: { items: [{ id: '11', name: 'AI', media: null }, { id: '12', name: 'Design' }] } } } } } };
    expect(parseFolderList(payload)).toEqual([{ id: '11', name: 'AI' }, { id: '12', name: 'Design' }]);
    expect(parseFolderList({ errors: [] })).toEqual([]);
  });
});

describe('countPosts', () => {
  const page = (entryIds: string[]) => ({
    data: { bookmark_timeline_v2: { timeline: { instructions: [{ type: 'TimelineAddEntries', entries: entryIds.map((entryId) => ({ entryId })) }] } } },
  });

  it('counts posts and not cursors', () => {
    expect(countPosts(page(['tweet-1', 'tweet-2', 'cursor-top-9', 'cursor-bottom-9']))).toBe(2);
    expect(countPosts(page(['cursor-top-9', 'cursor-bottom-9']))).toBe(0);
  });

  it('finds the timeline under another key, as a folder response has, and survives junk', () => {
    expect(countPosts({ data: { bookmark_collection_timeline: { timeline: { instructions: [{ entries: [{ entryId: 'tweet-5' }] }] } } } })).toBe(1);
    expect(countPosts(null)).toBe(0);
    expect(countPosts({ errors: [{ message: 'x' }] })).toBe(0);
  });
});
