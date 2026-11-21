import { describe, expect, it } from 'vitest';
import { decodeEntities, nextRole, serialSaver } from './edits';

describe('nextRole', () => {
  it('toggles back to other only when every target already has the role', () => {
    expect(nextRole(['primary', 'primary'], 2, 'primary')).toBe('other');
    expect(nextRole(['primary', 'secondary'], 2, 'primary')).toBe('primary');
    expect(nextRole(['secondary', 'primary'], 2, 'primary')).toBe('primary');
    // One of the two items does not carry the tag yet.
    expect(nextRole(['primary'], 2, 'primary')).toBe('primary');
    expect(nextRole([], 1, 'secondary')).toBe('secondary');
  });
});

describe('serialSaver', () => {
  it('skips an unchanged value', async () => {
    const sent: string[] = [];
    const save = serialSaver('A', async (v) => void sent.push(v));
    await save('A');
    expect(sent).toEqual([]);
  });

  it('sends a revert made while the earlier save is pending, after it', async () => {
    const sent: string[] = [];
    let release!: () => void;
    const first = new Promise<void>((r) => (release = r));
    const save = serialSaver('A', async (v) => {
      if (v === 'B') await first;
      sent.push(v);
    });
    void save('B');
    const reverted = save('A');
    release();
    await reverted;
    expect(sent).toEqual(['B', 'A']);
  });

  it('keeps saving after a failure', async () => {
    const sent: string[] = [];
    const save = serialSaver('', async (v) => {
      if (v === 'bad') throw new Error('offline');
      sent.push(v);
    });
    await save('bad');
    await save('good');
    expect(sent).toEqual(['good']);
  });
});

describe('decodeEntities', () => {
  it('decodes the entities the server writes', () => {
    expect(decodeEntities('Fish &amp; Chips &lt;b&gt; &#39;x&#x27; &quot;')).toBe(`Fish & Chips <b> 'x' "`);
  });
  it('does not throw on a number that is not a character', () => {
    expect(decodeEntities('a &#1114112; b &#x110000;')).toBe('a &#1114112; b &#x110000;');
  });
});
