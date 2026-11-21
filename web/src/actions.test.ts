import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api', () => {
  class ApiError extends Error {
    constructor(public status: number, message: string) {
      super(message);
    }
  }
  return { api: vi.fn(), ApiError, invalidateLibrary: vi.fn(), queryClient: {}, tagsQuery: {}, fetchItems: vi.fn(), flattenTags: vi.fn(), listOfQuery: vi.fn() };
});
vi.mock('./nav', () => ({ currentSearch: () => ({}), hasDrawer: () => false }));

import { undo } from './actions';
import { api, ApiError } from './api';
import { getState, setState } from './store';

const call = vi.mocked(api);

describe('undo', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    call.mockReset();
    setState({ undoStack: [11] });
  });

  it('removes only the undone batch, keeping one added while the request was pending', async () => {
    call.mockImplementation(async () => void setState({ undoStack: [...getState().undoStack, 12] }));
    await undo();
    expect(call).toHaveBeenCalledWith('/api/undo/11', { method: 'POST' });
    expect(getState().undoStack).toEqual([12]);
  });

  it('keeps the batch for another try when the request got no answer', async () => {
    call.mockRejectedValue(new TypeError('Failed to fetch'));
    await undo();
    expect(getState().undoStack).toEqual([11]);
  });

  it('drops a batch the server refuses, so undo does not stick on it', async () => {
    call.mockRejectedValue(new ApiError(404, 'Nothing to undo'));
    await undo();
    expect(getState().undoStack).toEqual([]);
  });
});
