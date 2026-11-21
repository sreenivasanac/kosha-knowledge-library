/**
 * The Ask answer as a small tree of text pieces. The answer is model output:
 * it is treated as plain text with a little Markdown (paragraphs, lists,
 * headings, bold, code, links, `[123]` citations) and never as HTML. Every
 * piece ends up in a React text node, and only http(s) links become links.
 * Pure, so the guard can be tested.
 */
export type Inline = { kind: 'text' | 'bold' | 'code'; text: string } | { kind: 'link'; text: string; href: string } | { kind: 'cite'; id: number };
export type Block = { kind: 'p' | 'h'; inline: Inline[] } | { kind: 'ul' | 'ol'; items: Inline[][] } | { kind: 'pre'; text: string };

const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\)|\[\d+(?:\s*,\s*\d+)*\]|https?:\/\/[^\s<>)\]]+)/g;

/** `cited` is the ids the server returned: a bracketed number is a citation only when it names one of them. */
export function parseInline(text: string, cited: ReadonlySet<number> = new Set()): Inline[] {
  const pieces = text.split(INLINE).flatMap((part, i): Inline[] => {
    if (!part) return [];
    if (i % 2 === 0) return [{ kind: 'text', text: part }];
    if (part.startsWith('**')) return [{ kind: 'bold', text: part.slice(2, -2) }];
    if (part.startsWith('`')) return [{ kind: 'code', text: part.slice(1, -1) }];
    if (/^https?:\/\//.test(part)) {
      // A full stop or comma after a bare address belongs to the sentence, not the link.
      const trailing = /[.,;:!?]+$/.exec(part)?.[0] ?? '';
      const href = trailing ? part.slice(0, -trailing.length) : part;
      return [{ kind: 'link', text: href, href }, ...(trailing ? [{ kind: 'text' as const, text: trailing }] : [])];
    }
    const link = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(part);
    if (link) return [{ kind: 'link', text: link[1], href: link[2] }];
    const ids = part.slice(1, -1).split(',').map((n) => Number(n.trim()));
    return ids.every((id) => cited.has(id)) ? ids.map((id) => ({ kind: 'cite', id })) : [{ kind: 'text', text: part }];
  });
  // Neighbouring plain pieces (a bracket that was no citation) read as one run of text again.
  return pieces.reduce<Inline[]>((out, piece) => {
    const last = out.at(-1);
    if (piece.kind === 'text' && last?.kind === 'text') out[out.length - 1] = { kind: 'text', text: last.text + piece.text };
    else out.push(piece);
    return out;
  }, []);
}

export function parseAnswer(answer: string, citedIds: number[] = []): Block[] {
  const cited = new Set(citedIds);
  const blocks: Block[] = [];
  let fence: string[] | null = null;
  for (const line of answer.replace(/\r\n/g, '\n').split('\n')) {
    if (line.trimStart().startsWith('```')) {
      if (fence) blocks.push({ kind: 'pre', text: fence.join('\n') });
      fence = fence ? null : [];
      continue;
    }
    if (fence) {
      fence.push(line);
      continue;
    }
    const last = blocks.at(-1);
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    if (!line.trim()) blocks.push({ kind: 'p', inline: [] });
    else if (heading) blocks.push({ kind: 'h', inline: parseInline(heading[1], cited) });
    else if (bullet || numbered) {
      const kind = bullet ? 'ul' : 'ol';
      const item = parseInline((bullet ?? numbered)![1], cited);
      if (last?.kind === kind) last.items.push(item);
      else blocks.push({ kind, items: [item] });
    } else if (last?.kind === 'p' && last.inline.length) last.inline.push({ kind: 'text', text: '\n' }, ...parseInline(line, cited));
    else blocks.push({ kind: 'p', inline: parseInline(line, cited) });
  }
  // An unclosed fence still shows its text.
  if (fence) blocks.push({ kind: 'pre', text: fence.join('\n') });
  return blocks.filter((b) => b.kind !== 'p' || b.inline.length > 0);
}
