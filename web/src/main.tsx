/** Entry: routes, the login page, and the providers. */
import { QueryClientProvider } from '@tanstack/react-query';
import { createRootRoute, createRoute, createRouter, Link, Outlet, redirect, RouterProvider, useNavigate } from '@tanstack/react-router';
import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, ApiError, capabilitiesQuery, meQuery, queryClient } from './api';
import { APP_NAME, APP_TAGLINE } from './constants';
import './index.css';
import { Library } from './Library';
import { setRouter, validateLibrarySearch } from './nav';
import { SettingsPage } from './SettingsPage';
import { Shell } from './Shell';
import { ProposedPage, TagsPage } from './TagsPage';

function Login() {
  const navigate = useNavigate();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <main className="grid min-h-dvh place-items-center px-4">
      <form
        className="w-full max-w-sm space-y-4"
        onSubmit={async (e) => {
          e.preventDefault();
          const form = new FormData(e.currentTarget);
          setBusy(true);
          setError('');
          try {
            queryClient.setQueryData(['me'], await api('/api/auth/login', { method: 'POST', body: { email: form.get('email'), password: form.get('password') } }));
            await navigate({ to: '/' });
          } catch (err) {
            setError(err instanceof ApiError && err.status === 401 ? 'That email and password do not match an account.' : err instanceof Error ? err.message : 'Could not sign in.');
          }
          setBusy(false);
        }}
      >
        <div className="flex items-center gap-3">
          <span aria-hidden className="grid h-10 w-10 place-items-center rounded-lg bg-accent">
            <span className="h-6 w-3.5 bg-mark [clip-path:polygon(0_0,100%_0,100%_100%,50%_72%,0_100%)]" />
          </span>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{APP_NAME}</h1>
            <p className="text-sm text-ink-2">{APP_TAGLINE}</p>
          </div>
        </div>
        <label className="grid gap-1 text-sm font-medium">
          Email
          <input name="email" type="email" autoComplete="username" required autoFocus className="field h-10" />
        </label>
        <label className="grid gap-1 text-sm font-medium">
          Password
          <input name="password" type="password" autoComplete="current-password" required className="field h-10" />
        </label>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <button className="btn btn-primary h-10 w-full justify-center" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  );
}

const rootRoute = createRootRoute({
  component: Outlet,
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
const loginRoute = createRoute({ getParentRoute: () => rootRoute, path: '/login', component: Login });
/** Everything behind the login shares the shell. */
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'app',
  component: Shell,
  beforeLoad: async () => {
    if (!(await queryClient.ensureQueryData(meQuery))) throw redirect({ to: '/login' });
    void queryClient.prefetchQuery(capabilitiesQuery);
  },
});
const routeTree = rootRoute.addChildren([
  loginRoute,
  appRoute.addChildren([
    createRoute({ getParentRoute: () => appRoute, path: '/', validateSearch: validateLibrarySearch, component: () => <Library /> }),
    createRoute({ getParentRoute: () => appRoute, path: '/archive', validateSearch: validateLibrarySearch, component: () => <Library archived /> }),
    createRoute({ getParentRoute: () => appRoute, path: '/tags', component: TagsPage }),
    createRoute({ getParentRoute: () => appRoute, path: '/proposed', component: ProposedPage }),
    createRoute({ getParentRoute: () => appRoute, path: '/settings', component: SettingsPage }),
  ]),
]);

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
