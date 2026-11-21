/** One saved item as a list card, a grid card or a compact row, and the parts the reader drawer reuses. */
import { Archive, ArchiveRestore, BookOpen, ExternalLink, Eye, FileText, Heart, MessageCircle, Play, Repeat2, Star, StickyNote, Tag, Trash2 } from 'lucide-react';
import { useState, type MouseEvent, type ReactNode } from 'react';
import { focusItem, runAction } from './actions';
import type { Author, Item, ItemTag, Link, Media } from './api';
import { deleteForever } from './ArchiveTools';
import { CLAMP_CHARS, CLAMP_LINES, type View } from './constants';
import { count, displayUrl, duration, fullDate, shortDate } from './format';
import { goQuery, setSearch } from './nav';
import { quoteValue } from './query';
import { getState, openOverlay, setState, useStore } from './store';
import { useHint } from './ui';

const SOURCE_GLYPH: Record<string, string> = { x: '𝕏', linkedin: 'in', youtube: 'YT', chrome: 'Ch', instagram: 'IG', gmail: 'M', web: 'W' };

export function SourceIcon({ source }: { source: string }) {
  return (
    <span title={`From ${source}`} aria-label={`From ${source}`} className="inline-grid h-4 min-w-4 place-items-center rounded-sm bg-sunken px-0.5 text-[10px] font-semibold leading-none text-ink-2">
      {SOURCE_GLYPH[source] ?? source.slice(0, 2)}
    </span>
  );
}

export function Avatar({ author, size = 40 }: { author: Author; size?: number }) {
  const [broken, setBroken] = useState(false);
  const style = { width: size, height: size };
  if (!author.avatar || broken)
    return (
      <span style={style} aria-hidden className="grid shrink-0 place-items-center rounded-full bg-accent-soft text-sm font-semibold text-accent">
        {(author.name ?? author.handle ?? '?').slice(0, 1).toUpperCase()}
      </span>
    );
  // X serves 48px "_normal" avatars; "_bigger" (73px) stays sharp on dense screens.
  return <img src={author.avatar.replace('_normal.', '_bigger.')} alt="" loading="lazy" style={style} onError={() => setBroken(true)} className="shrink-0 rounded-full bg-sunken object-cover" />;
}

const TEXT_PARTS = /(https?:\/\/[^\s]+|@\w{1,15})/g;

/** Post text with line breaks kept, links shown as their real domain, and @handles that filter. */
export function RichText({ item }: { item: Item }) {
  let shortLinks = 0;
  const parts = (item.body ?? '').split(TEXT_PARTS).map((part, i) => {
    if (part.startsWith('@')) {
      return (
        <button key={i} className="text-accent hover:underline" title={`Show items from ${part}`} onClick={() => goQuery(`from:${part.slice(1)}`)}>
          {part}
        </button>
      );
    }
    if (!/^https?:\/\//.test(part)) return part;
    const trailing = /[.,;:!?)\]…]+$/.exec(part)?.[0] ?? '';
    let url = trailing ? part.slice(0, -trailing.length) : part;
    if (/^https?:\/\/t\.co\//.test(url)) {
      // ponytail: t.co links are matched to the item's expanded links by order; a
      // leftover one on a post with media is the media's own link, so it is hidden.
      const link = item.links[shortLinks++];
      if (link) url = link.url;
      else if (item.media.length) return null;
    }
    return (
      <span key={i}>
        <a href={url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline" title={url}>
          {displayUrl(url)}
        </a>
        {trailing}
      </span>
    );
  });
  return <>{parts}</>;
}

function Body({ item, clamp, className = '' }: { item: Item; clamp: number | null; className?: string }) {
  const [open, setOpen] = useState(false);
  const body = item.body ?? '';
  if (!body) return null;
  const long = clamp != null && (body.length > CLAMP_CHARS || body.split('\n').length > clamp);
  return (
    <div className={className}>
      <div className="whitespace-pre-wrap break-words leading-relaxed" style={long && !open ? { display: '-webkit-box', WebkitLineClamp: clamp, WebkitBoxOrient: 'vertical', overflow: 'hidden' } : undefined}>
        <RichText item={item} />
      </div>
      {long && (
        <button className="mt-0.5 text-sm font-medium text-accent hover:underline" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'less' : 'more'}
        </button>
      )}
    </div>
  );
}

/** A stored file plays from Kosha; otherwise the highest-bitrate mp4 the source offers. */
export function bestVideoSrc(m: Media): string {
  if (m.stored) return m.url;
  const mp4 = (m.variants ?? []).filter((v) => v.content_type === 'video/mp4').sort((a, b) => (b.bitrate ?? 0) - (a.bitrate ?? 0))[0];
  return mp4?.url ?? m.url;
}

const ratio = (m: Media, min: number, max: number) => Math.min(max, Math.max(min, m.width && m.height ? m.width / m.height : 16 / 9));

function Video({ m }: { m: Media }) {
  const gif = m.type === 'animated_gif';
  return (
    <video
      src={bestVideoSrc(m)}
      poster={m.preview_url ?? undefined}
      // A GIF loops silently like the original; a video waits for the user (or `p`).
      {...(gif ? { autoPlay: true, loop: true, muted: true } : { controls: true, preload: 'none' })}
      playsInline
      aria-label={m.alt_text ?? (gif ? 'Animated GIF' : 'Video')}
      style={{ aspectRatio: ratio(m, 0.6, 2.4) }}
      className="max-h-[520px] w-full rounded-lg border border-line bg-black object-contain"
    />
  );
}

/** Photos as a grid that opens the lightbox; videos and GIFs inline. Sizes are reserved up front so nothing jumps. */
export function MediaBlock({ media }: { media: Media[] }) {
  if (!media.length) return null;
  const photos = media.filter((m) => m.type === 'photo');
  const moving = media.filter((m) => m.type === 'video' || m.type === 'animated_gif');
  const other = media.filter((m) => !photos.includes(m) && !moving.includes(m));
  const shown = photos.slice(0, 4);
  return (
    <div className="space-y-2">
      {moving.map((m) => (
        <Video key={m.id} m={m} />
      ))}
      {shown.length > 0 && (
        <div className={`grid gap-0.5 overflow-hidden rounded-lg border border-line ${shown.length > 1 ? 'grid-cols-2' : ''}`} style={{ aspectRatio: shown.length === 1 ? ratio(shown[0], 0.75, 2.4) : 16 / 9 }}>
          {shown.map((m, i) => (
            <button key={m.id} aria-label={m.alt_text ?? `Open photo ${i + 1} of ${photos.length}`} onClick={() => openOverlay({ kind: 'lightbox', media: photos, index: i })} className={`relative min-h-0 bg-sunken ${shown.length === 3 && i === 0 ? 'row-span-2' : ''}`}>
              <img src={m.preview_url ?? m.url} alt={m.alt_text ?? ''} loading="lazy" className="absolute inset-0 h-full w-full object-cover" />
              {i === 3 && photos.length > 4 && <span className="absolute inset-0 grid place-items-center bg-black/55 text-lg font-semibold text-white">+{photos.length - 4}</span>}
            </button>
          ))}
        </div>
      )}
      {other.map((m) => (
        <a key={m.id} href={m.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm text-accent">
          <FileText size={16} aria-hidden /> Open {m.type}
        </a>
      ))}
    </div>
  );
}

/** The first media as a fixed 16:9 cover, for grid cards. */
function Cover({ item }: { item: Item }) {
  const m = item.media[0];
  if (!m) return null;
  return (
    <div className="relative aspect-video overflow-hidden rounded-md bg-sunken">
      <img src={m.preview_url ?? m.url} alt={m.alt_text ?? ''} loading="lazy" className="absolute inset-0 h-full w-full object-cover" />
      {m.type !== 'photo' && (
        <span className="absolute bottom-1.5 left-1.5 flex items-center gap-1 rounded bg-black/70 px-1.5 py-0.5 text-xs text-white">
          <Play size={11} aria-hidden /> {m.type === 'animated_gif' ? 'GIF' : duration(m.duration_ms)}
        </span>
      )}
      {item.media.length > 1 && <span className="absolute bottom-1.5 right-1.5 rounded bg-black/70 px-1.5 py-0.5 text-xs text-white">+{item.media.length - 1}</span>}
    </div>
  );
}

export function LinkPreview({ link }: { link: Link }) {
  return (
    <a href={link.url} target="_blank" rel="noopener noreferrer" className="flex overflow-hidden rounded-lg border border-line hover:border-ink-3">
      {link.image_url && <img src={link.image_url} alt="" loading="lazy" className="h-24 w-32 shrink-0 bg-sunken object-cover" />}
      <span className="min-w-0 px-3 py-2">
        <span className="block truncate text-sm font-medium">{link.title ?? displayUrl(link.url)}</span>
        {link.description && <span className="line-clamp-2 text-sm text-ink-2">{link.description}</span>}
        <span className="block truncate text-xs text-ink-3">{link.domain ?? displayUrl(link.url, 30)}</span>
      </span>
    </a>
  );
}
const previewOf = (item: Item) => item.links.find((l) => l.title || l.image_url);

const sourceUrl = (item: Item, sourceId: string) => (item.source === 'x' ? `https://x.com/i/status/${sourceId}` : (item.url ?? '#'));

/** The quoted post, nested with its own media; or why it cannot be shown. */
export function Quote({ item }: { item: Item }) {
  const q = item.quoted;
  if (!item.quote) return null;
  if (!q)
    return (
      <div className="rounded-lg border border-dashed border-line px-3 py-2 text-sm text-ink-2">
        {item.quote.target_state === 'unavailable' ? 'Quoted post unavailable (deleted or private).' : 'The quoted post has not been captured yet. '}
        {item.quote.target_state !== 'unavailable' && (
          <a className="text-accent hover:underline" href={sourceUrl(item, item.quote.source_id)} target="_blank" rel="noopener noreferrer">
            Open it at the source
          </a>
        )}
      </div>
    );
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Quoted post by ${q.author.name ?? q.author.handle}; open in the reader`}
      onClick={(e) => {
        if ((e.target as HTMLElement).closest('a,button,video')) return;
        e.stopPropagation();
        setSearch({ item: q.id });
      }}
      onKeyDown={(e) => e.key === 'Enter' && e.target === e.currentTarget && (e.stopPropagation(), setSearch({ item: q.id }))}
      className="space-y-2 rounded-lg border border-line p-3 hover:border-ink-3"
    >
      <div className="flex items-center gap-2 text-sm">
        <Avatar author={q.author} size={20} />
        <span className="truncate font-semibold">{q.author.name}</span>
        <span className="truncate text-ink-3">@{q.author.handle}</span>
        <span className="shrink-0 text-ink-3">{shortDate(q.posted_at)}</span>
      </div>
      {q.unavailable_reason ? <p className="text-sm text-ink-2">{q.unavailable_reason}</p> : <Body item={q} clamp={CLAMP_LINES} className="text-[0.933rem]" />}
      <MediaBlock media={q.media} />
      {q.quote && <p className="text-sm text-ink-3">Quotes another post; open to see it.</p>}
    </div>
  );
}

function TagChip({ tag, children }: { tag: ItemTag; children: ReactNode }) {
  const proposed = tag.status === 'proposed';
  return (
    <button
      onClick={() => goQuery(`#${quoteValue(tag.path.join('/'))}`)}
      title={`${proposed ? 'Proposed tag' : 'Tag'} ${tag.path.join('/')}${tag.attached_by !== 'human' ? ` · attached by ${tag.attached_by}${tag.confidence != null ? ` (${Math.round(tag.confidence * 100)}%)` : ''}` : ''}`}
      // Proposed tags are dashed and italic until approved.
      className={`inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-px text-xs hover:border-ink-3 ${proposed ? 'border-dashed border-ink-3 italic text-ink-2' : 'border-line text-ink-2'}`}
    >
      <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: tag.color ?? 'var(--ink-3)' }} />
      <span className="truncate">{children}</span>
      {proposed && <span className="not-italic text-ink-3">?</span>}
    </button>
  );
}

/** Primary tag as a breadcrumb path, then the secondary, then the rest as chips. */
export function TagRow({ tags }: { tags: ItemTag[] }) {
  if (!tags.length) return null;
  const order = { primary: 0, secondary: 1, other: 2 };
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {[...tags]
        .sort((a, b) => order[a.role] - order[b.role])
        .map((t) => (
          <TagChip key={t.tag_id} tag={t}>
            {t.role === 'primary' ? <span className="font-semibold text-ink">{t.path.join(' › ')}</span> : t.role === 'secondary' ? <span className="font-medium">{t.name}</span> : t.name}
          </TagChip>
        ))}
    </div>
  );
}

export function Engagement({ item }: { item: Item }) {
  const stat = (icon: ReactNode, n: number | null, label: string) =>
    n != null && (
      <span className="inline-flex items-center gap-1" title={`${n.toLocaleString()} ${label}`}>
        {icon}
        {count(n)}
      </span>
    );
  return (
    <span className="flex items-center gap-3 text-xs text-ink-3">
      {stat(<MessageCircle size={12} aria-hidden />, item.replies, 'replies')}
      {stat(<Repeat2 size={12} aria-hidden />, item.reposts, 'reposts')}
      {stat(<Heart size={12} aria-hidden />, item.likes, 'likes')}
      {stat(<Eye size={12} aria-hidden />, item.views, 'views')}
    </span>
  );
}

/** Posted date; the tooltip adds when it was saved, marked approximate when estimated. */
export function PostedDate({ item }: { item: Item }) {
  const saved = item.saved_at ? `Saved ${item.saved_at_approximate ? 'about ' : ''}${fullDate(item.saved_at)}` : `Captured ${fullDate(item.captured_at)}`;
  return (
    <time dateTime={item.posted_at ?? undefined} title={`Posted ${fullDate(item.posted_at)}\n${saved}`} className="shrink-0 text-ink-3">
      {shortDate(item.posted_at)}
    </time>
  );
}

/** Star, unread and note marks; the same in every view. */
function Marks({ item }: { item: Item }) {
  return (
    <>
      {!item.read && <span title="Unread" aria-label="Unread" className="h-2 w-2 shrink-0 rounded-full bg-accent" />}
      {item.starred && <Star size={14} aria-label="Starred" className="shrink-0 fill-mark text-mark" />}
      {item.note && (
        <span title={item.note} className="shrink-0 text-ink-3">
          <StickyNote size={14} aria-label="Has a note" />
        </span>
      )}
    </>
  );
}

/** Search-result excerpt; the server marks matches with <mark> or <b>, rendered here without trusting HTML. */
const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"' };
/** The server escapes the snippet as HTML; turn the few entities back into characters (it is rendered as text). */
const decode = (text: string) => text.replace(/&(?:#x([0-9a-f]+)|#(\d+)|(amp|lt|gt|quot));/gi, (_, hex, dec, name) => (name ? ENTITIES[name.toLowerCase()] : String.fromCodePoint(hex ? parseInt(hex, 16) : Number(dec))));

function Snippet({ text }: { text: string }) {
  const parts = text.split(/<\/?(?:mark|b|em)>/).map(decode);
  return <p className="rounded-md bg-sunken px-2 py-1 text-sm text-ink-2">{parts.map((p, i) => (i % 2 ? <mark key={i} className="rounded-sm bg-mark/30 text-ink">{p}</mark> : p))}</p>;
}

function Context({ item }: { item: Item }) {
  if (!item.reply_to && !(item.thread && item.thread.member_count > 1)) return null;
  return (
    <div className="flex flex-wrap gap-x-3 text-sm text-ink-3">
      {item.reply_to && (
        <button className="hover:text-accent hover:underline" onClick={() => setSearch({ item: item.id })}>
          Replying to {item.reply_to.author_handle ? `@${item.reply_to.author_handle}` : 'a post'}
        </button>
      )}
      {item.thread && item.thread.member_count > 1 && (
        <button className="inline-flex items-center gap-1 font-medium text-accent hover:underline" onClick={() => setSearch({ item: item.id })}>
          <BookOpen size={13} aria-hidden /> Read thread ({item.thread.member_count})
        </button>
      )}
    </div>
  );
}

function toggleSelected(id: number) {
  const next = new Set(getState().selection);
  if (!next.delete(id)) next.add(id);
  setState({ selection: next });
}

/** Checkbox that is always visible once anything is selected, and on hover or focus before that. */
function SelectBox({ item, selected, show }: { item: Item; selected: boolean; show: boolean }) {
  const hint = useHint();
  return (
    <input
      type="checkbox"
      checked={selected}
      onChange={() => toggleSelected(item.id)}
      title={hint('Select', 'select.toggle')}
      aria-label={`Select the item by ${item.author.name ?? item.author.handle ?? 'unknown'}`}
      className={`h-4 w-4 shrink-0 accent-(--accent) ${show ? '' : 'opacity-0 focus-visible:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-60'}`}
    />
  );
}

function CardActions({ item }: { item: Item }) {
  const hint = useHint();
  const act = (id: string) => (e: MouseEvent) => {
    e.stopPropagation();
    focusItem(item.id, false);
    runAction(id);
  };
  const cls = 'grid h-7 w-7 place-items-center rounded text-ink-3 hover:bg-sunken hover:text-ink';
  return (
    <span className="flex shrink-0 items-center opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 group-data-[focused=true]:opacity-100 [@media(hover:none)]:opacity-100">
      <button className={cls} title={hint(item.starred ? 'Unstar' : 'Star', 'item.star')} aria-label="Star" aria-pressed={item.starred} onClick={act('item.star')}>
        <Star size={15} className={item.starred ? 'fill-mark text-mark' : ''} />
      </button>
      <button className={cls} title={hint('Add a tag', 'item.tag')} aria-label="Add a tag" onClick={act('item.tag')}>
        <Tag size={15} />
      </button>
      <button className={cls} title={hint(item.archived ? 'Restore from Archive' : 'Move to Archive', 'item.archive')} aria-label={item.archived ? 'Restore' : 'Archive'} onClick={act('item.archive')}>
        {item.archived ? <ArchiveRestore size={15} /> : <Archive size={15} />}
      </button>
      {item.archived && (
        <button className={`${cls} hover:text-danger`} title="Delete permanently" aria-label="Delete permanently" onClick={(e) => (e.stopPropagation(), deleteForever([item.id]))}>
          <Trash2 size={15} />
        </button>
      )}
      {item.url && (
        <a className={cls} href={item.url} target="_blank" rel="noopener noreferrer" title={hint('Open the original', 'item.openOriginal')} aria-label="Open the original">
          <ExternalLink size={15} />
        </a>
      )}
    </span>
  );
}

/** A click on the card (not on a link, button or selected text) focuses it and opens the reader. */
function openFromClick(item: Item) {
  return (e: MouseEvent) => {
    if ((e.target as HTMLElement).closest('a,button,input,video,[role="button"]') || window.getSelection()?.toString()) return;
    focusItem(item.id, false);
    setSearch({ item: item.id });
  };
}

export function ItemCard({ item, view }: { item: Item; view: View }) {
  const focused = useStore((s) => s.focusId === item.id);
  const selected = useStore((s) => s.selection.has(item.id));
  const selecting = useStore((s) => s.selection.size > 0);
  const shell = `group relative bg-surface ${focused ? 'focused' : ''} ${selected ? 'bg-accent-soft/50!' : ''}`;
  const root = { 'data-focus-id': item.id, 'data-focused': focused, onClick: openFromClick(item), 'aria-current': focused || undefined } as const;
  const preview = previewOf(item);

  if (view === 'compact')
    return (
      <article {...root} className={`${shell} flex h-9 items-center gap-2 border-b border-line px-3 text-sm`}>
        <SelectBox item={item} selected={selected} show={selecting} />
        <Avatar author={item.author} size={20} />
        <span className="w-32 shrink-0 truncate font-medium max-sm:w-20">{item.author.name ?? item.author.handle}</span>
        <span className="min-w-0 flex-1 truncate text-ink-2">
          {item.title && <span className="font-medium text-ink">{item.title} </span>}
          {decode((item.snippet ?? item.body ?? '').replace(/<\/?(?:mark|b|em)>/g, '')).replace(/\s+/g, ' ')}
        </span>
        <span className="flex max-w-[30%] shrink-0 items-center gap-1 overflow-hidden max-sm:hidden">
          {item.tags.slice(0, 2).map((t) => (
            <TagChip key={t.tag_id} tag={t}>
              {t.name}
            </TagChip>
          ))}
        </span>
        {item.media.length > 0 && <span className="shrink-0 text-xs text-ink-3">{item.media[0].type === 'photo' ? `${item.media.length} img` : 'video'}</span>}
        <Marks item={item} />
        <SourceIcon source={item.source} />
        <span className="w-16 shrink-0 text-right text-xs">
          <PostedDate item={item} />
        </span>
      </article>
    );

  const header = (
    <div className="flex min-w-0 items-center gap-1.5 text-sm">
      <span className="truncate font-semibold">{item.author.name ?? item.author.handle ?? 'Unknown author'}</span>
      {item.author.handle && <span className="truncate text-ink-3">@{item.author.handle}</span>}
      <PostedDate item={item} />
      <Marks item={item} />
    </div>
  );

  if (view === 'grid')
    return (
      <article {...root} className={`${shell} flex min-w-0 flex-col gap-2 rounded-lg border border-line p-3`}>
        <div className="flex items-center gap-2">
          <Avatar author={item.author} size={24} />
          <div className="min-w-0 flex-1">{header}</div>
          <SelectBox item={item} selected={selected} show={selecting} />
        </div>
        <Cover item={item} />
        {item.title && <h3 className="line-clamp-2 font-semibold leading-snug">{item.title}</h3>}
        {item.unavailable_reason ? <p className="text-sm text-ink-2">{item.unavailable_reason}</p> : <p className={`whitespace-pre-wrap break-words text-[0.933rem] leading-relaxed ${item.media.length ? 'line-clamp-4' : 'line-clamp-[9]'}`}>{item.body}</p>}
        {item.quoted && (
          <p className="truncate rounded-md border border-line px-2 py-1 text-sm text-ink-2">
            Quotes @{item.quoted.author.handle}: {item.quoted.body}
          </p>
        )}
        {item.quote && !item.quoted && <p className="text-sm text-ink-3">Quoted post unavailable</p>}
        {preview && !item.media.length && <span className="truncate text-sm text-accent">{preview.title ?? preview.domain}</span>}
        <div className="mt-auto space-y-2 pt-1">
          <TagRow tags={item.tags} />
          <div className="flex items-center justify-between gap-2">
            <span className="flex min-w-0 items-center gap-2 overflow-hidden">
              <SourceIcon source={item.source} />
              <Engagement item={item} />
            </span>
            <CardActions item={item} />
          </div>
        </div>
      </article>
    );

  return (
    <article {...root} className={`${shell} flex gap-3 border-b border-line px-3 py-4 sm:px-4`}>
      <div className="flex shrink-0 flex-col items-center gap-2">
        <Avatar author={item.author} />
        <SelectBox item={item} selected={selected} show={selecting} />
      </div>
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex items-center justify-between gap-2">
          {header}
          {/* A phone has no room for these next to the author; the reader drawer and the bulk bar have them. */}
          <span className="max-sm:hidden">
            <CardActions item={item} />
          </span>
        </div>
        <Context item={item} />
        {item.title && <h3 className="text-lg font-semibold leading-snug">{item.title}</h3>}
        {item.unavailable_reason ? <p className="text-sm text-ink-2">{item.unavailable_reason}</p> : <Body item={item} clamp={CLAMP_LINES} />}
        {/* With the whole text on the card, the excerpt only helps when the match may be below the fold. */}
        {item.snippet && (item.body ?? '').length > CLAMP_CHARS && <Snippet text={item.snippet} />}
        <MediaBlock media={item.media} />
        <Quote item={item} />
        {preview && <LinkPreview link={preview} />}
        <TagRow tags={item.tags} />
        <div className="flex items-center gap-3">
          <SourceIcon source={item.source} />
          <Engagement item={item} />
        </div>
      </div>
    </article>
  );
}

/** The full item as the reader drawer shows it: nothing clamped. */
export function ItemFull({ item }: { item: Item }) {
  const preview = previewOf(item);
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <Avatar author={item.author} />
        <div className="min-w-0">
          <div className="truncate font-semibold">{item.author.name ?? item.author.handle ?? 'Unknown author'}</div>
          <div className="flex gap-2 text-sm text-ink-3">
            {item.author.handle && <span className="truncate">@{item.author.handle}</span>}
            <PostedDate item={item} />
          </div>
        </div>
      </div>
      {item.title && <h2 className="text-xl font-semibold leading-snug">{item.title}</h2>}
      {item.unavailable_reason ? <p className="text-ink-2">{item.unavailable_reason}</p> : <Body item={item} clamp={null} className="text-[1.067rem]" />}
      <MediaBlock media={item.media} />
      <Quote item={item} />
      {preview && <LinkPreview link={preview} />}
    </div>
  );
}
