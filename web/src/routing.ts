/**
 * Which screen a visitor gets, decided from what the server says about itself
 * (`/api/public-config`) and about the visitor (`/api/auth/me`). Pure, tested.
 */

export interface EntryState {
  /** False on a fresh install: nobody can sign in until the admin account exists. */
  hasUsers: boolean;
  /** The owner's switch for the public landing page. */
  landing: boolean;
  signedIn: boolean;
  /** False until the person finished or skipped the setup guide. */
  onboardingDone: boolean;
}

export type Entry = { page: 'setup' } | { page: 'landing' } | { page: 'login'; next?: string } | { page: 'onboarding' } | { page: 'app' };

/** Where a request for an app page (`href` is its path and query) ends up. */
export function resolveEntry(state: EntryState, href: string): Entry {
  if (!state.hasUsers) return { page: 'setup' };
  if (state.signedIn) return state.onboardingDone ? { page: 'app' } : { page: 'onboarding' };
  const home = href === '/' || href === '';
  if (home && state.landing) return { page: 'landing' };
  // A deep link is kept, so signing in lands on the page that was asked for.
  return home ? { page: 'login' } : { page: 'login', next: href };
}

const PUBLIC_PAGES = ['/login', '/setup', '/signup', '/reset'];

/** The page to open after signing in: `next` only when it is a path inside this app (never another site). */
export function safeNext(next: unknown): string {
  if (typeof next !== 'string' || !next.startsWith('/') || next.startsWith('//') || next.includes('\\')) return '/';
  return PUBLIC_PAGES.some((p) => next === p || next.startsWith(`${p}?`)) ? '/' : next;
}

/**
 * Where a sign-in page itself should send the visitor instead of showing, or
 * null to show it: `/setup` only exists before the first account, and the
 * other pages make no sense before it or once signed in.
 */
export function publicPageRedirect(page: 'login' | 'setup' | 'signup', state: Pick<EntryState, 'hasUsers' | 'signedIn'>): '/' | '/setup' | null {
  if (!state.hasUsers) return page === 'setup' ? null : '/setup';
  if (page === 'setup' || state.signedIn) return '/';
  return null;
}

/** An invite or reset link works whatever path the server put the token on. */
export function tokenPage(pathname: string, search: Record<string, unknown>): { to: '/signup' | '/reset'; token: string } | null {
  if (search.invite != null && pathname !== '/signup') return { to: '/signup', token: String(search.invite) };
  if (search.reset != null && pathname !== '/reset') return { to: '/reset', token: String(search.reset) };
  return null;
}

/** Whether the sign-up page shows its form: open to all, or the visitor holds an invite (the server judges the token). */
export const canSignUp = (signups: 'open' | 'invite' | 'closed', invite: string | undefined) => signups === 'open' || !!invite;
