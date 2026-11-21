import { describe, expect, it } from 'vitest';
import { pushHistory, stepHistory } from './history';

describe('pipeline console history', () => {
  it('appends, trims, ignores blanks and moves repeats to the end', () => {
    expect(pushHistory([], '  all | count  ', 5)).toEqual(['all | count']);
    expect(pushHistory(['a', 'b'], '   ', 5)).toEqual(['a', 'b']);
    expect(pushHistory(['a', 'b', 'c'], 'a', 5)).toEqual(['b', 'c', 'a']);
  });

  it('drops the oldest past the cap', () => {
    expect(pushHistory(['a', 'b', 'c'], 'd', 3)).toEqual(['b', 'c', 'd']);
  });

  it('walks up from the draft and back down to it', () => {
    expect(stepHistory(0, null, -1)).toBeNull();
    expect(stepHistory(3, null, -1)).toBe(2);
    expect(stepHistory(3, 2, -1)).toBe(1);
    expect(stepHistory(3, 0, -1)).toBe(0);
    expect(stepHistory(3, 1, 1)).toBe(2);
    expect(stepHistory(3, 2, 1)).toBeNull();
    expect(stepHistory(3, null, 1)).toBeNull();
  });
});
