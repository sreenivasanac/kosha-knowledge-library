/** Entry: the routes, which screen a visitor gets, and the providers. */
import { QueryClientProvider, useQuery } from '@tanstack/react-query';
import { createRootRoute, createRoute, createRouter, Link, Outlet, redirect, RouterProvider, useLocation, useNavigate } from '@tanstack/react-router';
import { StrictMode, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { capabilitiesQuery, hasSessionHint, meQuery, publicConfigQuery, queryClient, setMe } from './api';
import { AskPage } from './AskPage';
import { Login, Reset, Setup, Signup } from './AuthPages';
import { ConsolePage } from './ConsolePage';
import './index.css';
import { Landing } from './Landing';
import { Library } from './Library';
import { setRouter, validateLibrarySearch } from './nav';
import { Onboarding } from './Onboarding';
import { applyTheme } from './prefs';
import { publicPageRedirect, resolveEntry, safeNext, tokenPage } from './routing';
import { SettingsPage } from './SettingsPage';
import { Shell } from './Shell';
import { ProposedPage, TagsPage } from './TagsPage';

/**
 * What the routes need to know before choosing a screen. With `who: 'skip'`
 * nobody is asked who is signed in (the landing page stays free of that call)
 * and the visitor counts as signed out; `'fresh'` asks even when the cache
 * says "nobody", which is how a browser that holds a session but no hint gets in.
 */
async function entryState(who: 'cached' | 'fresh' | 'skip') {
  if (who === 'skip' && queryClient.getQueryData(['me']) === undefined) queryClient.setQueryData(['me'], null);
  const [config, me] = await Promise.all([queryClient.ensureQueryData(publicConfigQuery), who === 'fresh' ? queryClient.fetchQuery({ ...meQuery, staleTime: 0 }) : queryClient.ensureQueryData(meQuery)]);
  // Keep the hint true to what the server just said.
  if (who !== 'skip' && !!me !== hasSessionHint()) setMe(me);
  // Old servers do not say; a demo visitor is never sent through the setup guide.
  const onboardingDone = !me || me.demo || me.onboarding_completed !== false;
  return { config, me, state: { hasUsers: config.has_users, landing: config.landing, signedIn: !!me, onboardingDone } };
}

/** The guard of the sign-in pages: each shows only when it applies (see publicPageRedirect). */
const publicPage = (page: 'login' | 'setup' | 'signup') => async () => {
  const to = publicPageRedirect(page, (await entryState(queryClient.getQueryData(['me']) ? 'cached' : 'fresh')).state);
  if (to) throw redirect({ to });
};

/** The app behind the sign-in, or the landing page at `/` for a visitor. */
function AppOrLanding() {
  const me = useQuery(meQuery).data;
  const config = useQuery(publicConfigQuery).data;
  const location = useLocation();
  const navigate = useNavigate();
  const entry = resolveEntry({ hasUsers: true, landing: !!config?.landing, signedIn: !!me, onboardingDone: true }, location.href);
  // The session ended (a 401 cleared the cached user): to the sign-in page, and back here afterwards.
  // While a link to a sign-in page is loading, this component is still mounted and already sees that
  // page's address; it must not send the visitor to sign in "for" the sign-in page (that never ends).
  const leaving = safeNext(location.href) !== location.href;
  useEffect(() => {
    // Not before the config is known: signing out empties the cache, and the landing switch decides where a visitor goes.
    if (entry.page === 'login' && !leaving && config) void navigate({ to: '/login', search: { next: entry.next } });
  }, [entry.page, entry.page === 'login' ? entry.next : undefined, leaving, !!config, navigate]); // eslint-disable-line react-hooks/exhaustive-deps
  if (entry.page === 'landing' || (leaving && !me && config?.landing)) return <Landing />;
  return me ? <Shell /> : null;
}

const rootRoute = createRootRoute({
  component: Outlet,
  beforeLoad: ({ location }) => {
    const link = tokenPage(location.pathname, location.search as Record<string, unknown>);
    if (link) throw redirect({ to: link.to, search: link.to === '/signup' ? { invite: link.token } : { reset: link.token } });
  },
  notFoundComponent: () => (
    <main className="grid min-h-dvh place-items-center text-center">
      <div>
        <p className="font-semibold">This page does not exist.</p>
        <Link to="/" className="text-accent hover:underline">
          Go to the library
        </Link>
      </div>
    </main>
  ),
});
/** Tokens and `next` are kept as text: the router would read `123` as a number. */
const text = (v: unknown) => (v == null || v === '' ? undefined : String(v));
const publicRoutes = [
  createRoute({ getParentRoute: () => rootRoute, path: '/login', component: Login, validateSearch: (s: Record<string, unknown>) => ({ next: text(s.next) }), beforeLoad: publicPage('login') }),
  createRoute({ getParentRoute: () => rootRoute, path: '/setup', component: Setup, beforeLoad: publicPage('setup') }),
  createRoute({ getParentRoute: () => rootRoute, path: '/signup', component: Signup, validateSearch: (s: Record<string, unknown>) => ({ invite: text(s.invite) }), beforeLoad: publicPage('signup') }),
  createRoute({ getParentRoute: () => rootRoute, path: '/reset', component: Reset, validateSearch: (s: Record<string, unknown>) => ({ reset: text(s.reset), token: text(s.token) }) }),
  // Full screen, outside the shell; also opened from Settings to go through the guide again.
  createRoute({
    getParentRoute: () => rootRoute,
    path: '/onboarding',
    component: Onboarding,
    beforeLoad: async () => {
      if (!(await entryState('cached')).me) throw redirect({ to: '/login', search: { next: '/onboarding' } });
    },
  }),
];
/** Everything behind the login shares the shell. */
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'app',
  component: AppOrLanding,
  beforeLoad: async ({ location }) => {
    // A visitor at `/` with no sign of a session gets the landing page without the "who am I" call.
    const { state } = await entryState(location.href !== '/' || hasSessionHint() ? 'cached' : 'skip');
    const entry = resolveEntry(state, location.href);
    if (entry.page === 'setup') throw redirect({ to: '/setup' });
    if (entry.page === 'login') throw redirect({ to: '/login', search: { next: entry.next } });
    if (entry.page === 'onboarding') throw redirect({ to: '/onboarding' });
    if (entry.page === 'app') void queryClient.prefetchQuery(capabilitiesQuery);
  },
});
const routeTree = rootRoute.addChildren([
  ...publicRoutes,
  appRoute.addChildren([
    createRoute({ getParentRoute: () => appRoute, path: '/', validateSearch: validateLibrarySearch, component: () => <Library /> }),
    createRoute({ getParentRoute: () => appRoute, path: '/archive', validateSearch: validateLibrarySearch, component: () => <Library archived /> }),
    createRoute({ getParentRoute: () => appRoute, path: '/ask', validateSearch: validateLibrarySearch, component: AskPage }),
    createRoute({ getParentRoute: () => appRoute, path: '/console', component: ConsolePage }),
    createRoute({ getParentRoute: () => appRoute, path: '/tags', component: TagsPage }),
    createRoute({ getParentRoute: () => appRoute, path: '/proposed', component: ProposedPage }),
    createRoute({ getParentRoute: () => appRoute, path: '/settings', validateSearch: (s: Record<string, unknown>) => ({ tab: text(s.tab), q: text(s.q) }), component: SettingsPage }),
  ]),
]);

applyTheme();
const router = createRouter({ routeTree });
setRouter(router);
declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
