/**
 * Search-bar tokeniser. The server owns the real grammar (and reports errors
 * with a position); this only splits the bar text well enough to show the
 * qualifiers as removable chips and to add or remove one. Pure, tested.
 */

export type TokenKind = 'qualifier' | 'tag' | 'text' | 'operator';

export interface Token {
  kind: TokenKind;
  /** The token exactly as typed. */
  raw: string;
  start: number;
  end: number;
  negated: boolean;
  /** Qualifier key (`from`), or `#` for a tag. */
  key?: string;
  /** Value without surrounding quotes. */
  value?: string;
}

const unquote = (s: string) => (s.length >= 2 && s.startsWith('"') && s.endsWith('"') ? s.slice(1, -1) : s);

/** Splits on spaces, keeping "quoted phrases", /regexes/ and key:"quoted values" whole. */
export function tokenize(input: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  while (i < input.length) {
    if (/\s/.test(input[i])) {
      i++;
      continue;
    }
    const start = i;
    if (input[i] === '(' || input[i] === ')') {
      tokens.push({ kind: 'operator', raw: input[i], start, end: ++i, negated: false });
      continue;
    }
    let quote: string | null = null;
    while (i < input.length) {
      const ch = input[i];
      if (quote) {
        if (ch === quote) quote = null;
      } else if (ch === '"' || (ch === '/' && i === start)) {
        quote = ch;
      } else if (/[\s()]/.test(ch)) {
        break;
      }
      i++;
    }
    const raw = input.slice(start, i);
    const negated = raw.startsWith('-') && raw.length > 1;
    const body = negated ? raw.slice(1) : raw;
    const base = { raw, start, end: i, negated };
    const qualifier = /^([a-z][a-z-]*):(.*)$/i.exec(body);
    if (raw === 'OR' || raw === 'AND') tokens.push({ ...base, kind: 'operator', negated: false });
    else if (body.startsWith('#') && body.length > 1) tokens.push({ ...base, kind: 'tag', key: '#', value: unquote(body.slice(1)) });
    else if (qualifier && !body.startsWith('"')) tokens.push({ ...base, kind: 'qualifier', key: qualifier[1].toLowerCase(), value: unquote(qualifier[2]) });
    else tokens.push({ ...base, kind: 'text', value: unquote(body) });
  }
  return tokens;
}

/** The chips under the bar: every qualifier and tag token. */
export const chipsOf = (input: string): Token[] => tokenize(input).filter((t) => t.kind === 'qualifier' || t.kind === 'tag');

/** The bar text with one token cut out. */
export function removeToken(input: string, token: Pick<Token, 'start' | 'end'>): string {
  return (input.slice(0, token.start) + input.slice(token.end)).replace(/\s+/g, ' ').trim();
}

export const hasToken = (input: string, raw: string): boolean => tokenize(input).some((t) => t.raw.toLowerCase() === raw.toLowerCase());

/** Adds the token if it is absent, removes it if present (chip rows and `f`). */
export function toggleToken(input: string, raw: string): string {
  const found = tokenize(input).find((t) => t.raw.toLowerCase() === raw.toLowerCase());
  return found ? removeToken(input, found) : `${input.trim()} ${raw}`.trim();
}

/** A value as it must be typed in the bar: quoted when it has spaces. */
export const quoteValue = (value: string): string => (/[\s()]/.test(value) ? `"${value}"` : value);

/** Applies a suggestion from the server: replaces [start, end) and returns the new text and caret. */
export function applySuggestion(input: string, range: [number, number], insert: string): { text: string; caret: number } {
  const before = input.slice(0, range[0]) + insert;
  const after = input.slice(range[1]);
  // A finished qualifier gets a trailing space so the next one can be typed at once.
  const spaced = insert.endsWith(':') || after.startsWith(' ') ? before : before + ' ';
  return { text: spaced + after, caret: spaced.length };
}
