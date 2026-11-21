/** Small display formatters. */
const full = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const sameYear = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
const otherYear = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 });

export const fullDate = (iso: string | null) => (iso ? full.format(new Date(iso)) : '');
export function shortDate(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return (d.getFullYear() === new Date().getFullYear() ? sameYear : otherYear).format(d);
}
export const count = (n: number | null | undefined) => (n == null ? '' : compact.format(n));
export function duration(ms: number | null): string {
  if (!ms) return '';
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
export const usd = (n: number | null | undefined) => (n == null ? '—' : n < 0.01 && n > 0 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`);

/** `github.com/owner/repo…` for a link shown inside post text. */
export function displayUrl(url: string, max = 42): string {
  try {
    const u = new URL(url);
    const text = u.hostname.replace(/^www\./, '') + (u.pathname === '/' ? '' : u.pathname);
    return text.length > max ? text.slice(0, max - 1) + '…' : text;
  } catch {
    return url;
  }
}
