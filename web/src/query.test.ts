import { describe, expect, it } from 'vitest';
import { applySuggestion, chipsOf, hasToken, quoteValue, removeToken, toggleToken, tokenize } from './query';

describe('search-bar tokeniser', () => {
  it('splits words, qualifiers and tags', () => {
    const tokens = tokenize('rag from:karpathy #ai/agents is:unread');
    expect(tokens.map((t) => t.kind)).toEqual(['text', 'qualifier', 'tag', 'qualifier']);
    expect(tokens[1]).toMatchObject({ key: 'from', value: 'karpathy', start: 4, end: 17 });
    expect(tokens[2]).toMatchObject({ key: '#', value: 'ai/agents' });
  });

  it('keeps quoted phrases and quoted values whole', () => {
    const tokens = tokenize('"exact phrase" folder:"Coding with AI" x');
    expect(tokens.map((t) => t.raw)).toEqual(['"exact phrase"', 'folder:"Coding with AI"', 'x']);
    expect(tokens[0]).toMatchObject({ kind: 'text', value: 'exact phrase' });
    expect(tokens[1]).toMatchObject({ kind: 'qualifier', key: 'folder', value: 'Coding with AI' });
  });

  it('handles negation, operators, parentheses, comparisons and regex', () => {
    const tokens = tokenize('(-#spam OR -from:bot) likes:>1000 posted:2026-03..2026-05 /re g+/');
    expect(tokens.map((t) => t.kind)).toEqual(['operator', 'tag', 'operator', 'qualifier', 'operator', 'qualifier', 'qualifier', 'text']);
    expect(tokens[1]).toMatchObject({ negated: true, value: 'spam' });
    expect(tokens[3]).toMatchObject({ negated: true, key: 'from', value: 'bot' });
    expect(tokens[5]).toMatchObject({ key: 'likes', value: '>1000' });
    expect(tokens[7].raw).toBe('/re g+/');
  });

  it('does not treat a URL-like word or an unfinished quote as a qualifier crash', () => {
    expect(tokenize('"open').map((t) => t.raw)).toEqual(['"open']);
    expect(tokenize('').length).toBe(0);
    expect(tokenize('   ')).toEqual([]);
  });

  it('lists chips and removes one by position', () => {
    const q = 'rag from:a  #x is:starred';
    const chips = chipsOf(q);
    expect(chips.map((c) => c.raw)).toEqual(['from:a', '#x', 'is:starred']);
    expect(removeToken(q, chips[1])).toBe('rag from:a is:starred');
    expect(removeToken('#x', chipsOf('#x')[0])).toBe('');
  });

  it('toggles a token on and off', () => {
    expect(toggleToken('rag', 'from:a')).toBe('rag from:a');
    expect(toggleToken('rag from:a', 'FROM:A')).toBe('rag');
    expect(toggleToken('', 'is:untagged')).toBe('is:untagged');
    expect(hasToken('a is:untagged', 'is:untagged')).toBe(true);
    expect(hasToken('a is:untaggedx', 'is:untagged')).toBe(false);
  });

  it('quotes values with spaces and applies a suggestion at the caret', () => {
    expect(quoteValue('Coding with AI')).toBe('"Coding with AI"');
    expect(quoteValue('github.com')).toBe('github.com');
    expect(applySuggestion('rag fr', [4, 6], 'from:')).toEqual({ text: 'rag from:', caret: 9 });
    expect(applySuggestion('rag from:ka more', [4, 11], 'from:karpathy')).toEqual({ text: 'rag from:karpathy more', caret: 17 });
    expect(applySuggestion('is:un', [0, 5], 'is:unread')).toEqual({ text: 'is:unread ', caret: 10 });
  });
});
