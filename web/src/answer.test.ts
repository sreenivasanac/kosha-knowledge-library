import { describe, expect, it } from 'vitest';
import { parseAnswer, parseInline } from './answer';

describe('Ask answer rendering guard', () => {
  it('keeps HTML as text, never as markup', () => {
    const blocks = parseAnswer('<img src=x onerror=alert(1)> and <script>alert(2)</script>');
    expect(blocks).toEqual([{ kind: 'p', inline: [{ kind: 'text', text: '<img src=x onerror=alert(1)> and <script>alert(2)</script>' }] }]);
  });

  it('makes links only from http and https', () => {
    expect(parseInline('[site](https://example.com/a) [bad](javascript:alert(1)) data:text/html,x')).toEqual([
      { kind: 'link', text: 'site', href: 'https://example.com/a' },
      { kind: 'text', text: ' [bad](javascript:alert(1)) data:text/html,x' },
    ]);
    expect(parseInline('see https://example.com/x.')).toEqual([
      { kind: 'text', text: 'see ' },
      { kind: 'link', text: 'https://example.com/x', href: 'https://example.com/x' },
      { kind: 'text', text: '.' },
    ]);
  });

  it('turns bracketed numbers into citations only for returned items', () => {
    const cited = new Set([12, 40]);
    expect(parseInline('RAG [12] and [12, 40] but not [7].', cited)).toEqual([
      { kind: 'text', text: 'RAG ' },
      { kind: 'cite', id: 12 },
      { kind: 'text', text: ' and ' },
      { kind: 'cite', id: 12 },
      { kind: 'cite', id: 40 },
      { kind: 'text', text: ' but not [7].' },
    ]);
  });

  it('reads paragraphs, lists, headings and code fences', () => {
    const blocks = parseAnswer('## Summary\nTwo lines\nof text.\n\n- **one**\n- `two`\n\n1. first\n\n```\n<b>raw</b>\n```');
    expect(blocks.map((b) => b.kind)).toEqual(['h', 'p', 'ul', 'ol', 'pre']);
    expect(blocks[2]).toEqual({ kind: 'ul', items: [[{ kind: 'bold', text: 'one' }], [{ kind: 'code', text: 'two' }]] });
    expect(blocks[4]).toEqual({ kind: 'pre', text: '<b>raw</b>' });
  });
});
