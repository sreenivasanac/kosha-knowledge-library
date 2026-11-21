/** The pages before the app: sign in, first-run setup, sign up, and setting a new password from a reset link. */
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useRouter, useSearch } from '@tanstack/react-router';
import { useState, type FormEvent, type ReactNode } from 'react';
import { api, ApiError, publicConfigQuery, queryClient, setMe, LEGACY_CONFIG, type Me, type PublicConfig } from './api';
import { APP_NAME, APP_TAGLINE, LINKS } from './constants';
import { canSignUp, safeNext } from './routing';
import { ErrorNote, Logo, NewPasswordField, useAction } from './ui';

const usePublicConfig = (): PublicConfig => useQuery(publicConfigQuery).data ?? LEGACY_CONFIG;

function AuthFrame({ title, intro, children, footer }: { title: string; intro?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <main className="grid min-h-dvh place-items-center px-4 py-8">
      <div className="w-full max-w-sm space-y-4">
        <div className="flex items-center gap-3">
          <Logo size={40} />
          <div>
            <p className="text-2xl font-semibold tracking-tight">{APP_NAME}</p>
            <p className="text-sm text-ink-2">{APP_TAGLINE}</p>
          </div>
        </div>
        <div>
          <h1 className="text-lg font-semibold">{title}</h1>
          {intro && <p className="mt-1 text-sm text-ink-2">{intro}</p>}
        </div>
        {children}
        {footer && <div className="border-t border-line pt-3 text-sm text-ink-2">{footer}</div>}
      </div>
    </main>
  );
}

const field = (label: string, props: React.InputHTMLAttributes<HTMLInputElement>) => (
  <label className="grid gap-1 text-sm font-medium">
    {label}
    <input required className="field h-10" {...props} />
  </label>
);

/** After any sign-in: remember who it is, then open the page that was asked for. */
function useEnter() {
  const router = useRouter();
  return (me: Me, next?: unknown) => {
    setMe(me);
    // A new session may see other capabilities (the demo has fewer).
    void queryClient.invalidateQueries({ queryKey: ['capabilities'] });
    router.history.push(safeNext(next));
  };
}

export function Login() {
  const config = usePublicConfig();
  const { next } = useSearch({ strict: false }) as { next?: string };
  const enter = useEnter();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function signIn(email: unknown, password: unknown) {
    setBusy(true);
    setError('');
    try {
      enter(await api<Me>('/api/auth/login', { method: 'POST', body: { email, password } }), next);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? 'That email and password do not match an account.' : err instanceof Error ? err.message : 'Could not sign in.');
    }
    setBusy(false);
  }

  return (
    <AuthFrame
      title="Sign in"
      footer={
        <>
          {canSignUp(config.signups, undefined) && (
            <p>
              No account yet?{' '}
              <Link to="/signup" className="text-accent hover:underline">
                Create one
              </Link>
            </p>
          )}
          {config.landing && (
            <p>
              <Link to="/" className="text-accent hover:underline">
                What is {APP_NAME}?
              </Link>
            </p>
          )}
        </>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          const form = new FormData(e.currentTarget);
          void signIn(form.get('email'), form.get('password'));
        }}
      >
        {field('Email', { name: 'email', type: 'email', autoComplete: 'username', autoFocus: true })}
        {field('Password', { name: 'password', type: 'password', autoComplete: 'current-password' })}
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <button className="btn btn-primary h-10 w-full justify-center" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
      {/* The owner published these on purpose: the demo account is for anyone to try. */}
      {config.demo && (
        <section aria-label="Demo" className="space-y-2 rounded-lg border border-line bg-surface p-3 text-sm">
          <h2 className="font-semibold">Demo</h2>
          <p className="text-ink-2">{config.demo.notice}</p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
            <dt className="text-ink-3">Email</dt>
            <dd className="break-all">{config.demo.email}</dd>
            <dt className="text-ink-3">Password</dt>
            <dd className="break-all">{config.demo.password}</dd>
          </dl>
          <button type="button" className="btn w-full justify-center" disabled={busy} onClick={() => void signIn(config.demo!.email, config.demo!.password)}>
            Open the demo
          </button>
        </section>
      )}
    </AuthFrame>
  );
}

/** The three fields of a new account, shared by setup and sign-up. */
function AccountForm({ submit, action, busyLabel, what }: { submit: (body: { name: string; email: string; password: string }) => Promise<unknown>; action: string; busyLabel: string; what: string }) {
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const create = useAction();
  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const created = await create.run(() => submit({ name: String(form.get('name')).trim(), email: String(form.get('email')).trim(), password }).then(() => true));
    if (!created) return;
    void queryClient.invalidateQueries({ queryKey: ['public-config'] });
    // The server signs the new account in; ask who we are rather than trust the reply's shape.
    const me = await api<Me>('/api/auth/me').catch(() => null);
    setMe(me);
    // A new account starts with the setup guide.
    void navigate({ to: me ? '/onboarding' : '/login' });
  }
  return (
    <form className="space-y-4" onSubmit={onSubmit}>
      {field('Name', { name: 'name', autoComplete: 'name', autoFocus: true })}
      {field('Email', { name: 'email', type: 'email', autoComplete: 'username' })}
      <NewPasswordField value={password} onChange={setPassword} />
      {create.error != null && <ErrorNote error={create.error} what={what} />}
      <button className="btn btn-primary h-10 w-full justify-center" disabled={create.busy}>
        {create.busy ? busyLabel : action}
      </button>
    </form>
  );
}

export function Setup() {
  return (
    <AuthFrame title="Create the admin account" intro="This server has no accounts yet. The first one is the admin: it manages other accounts, API keys and storage.">
      <AccountForm what="First-run setup" action="Create account and continue" busyLabel="Creating…" submit={(body) => api('/api/setup', { method: 'POST', body })} />
    </AuthFrame>
  );
}

const signInLink = (
  <p>
    Already have an account?{' '}
    <Link to="/login" className="text-accent hover:underline">
      Sign in
    </Link>
  </p>
);

export function Signup() {
  const config = usePublicConfig();
  const search = useSearch({ strict: false }) as { invite?: string | number };
  const invite = search.invite == null ? undefined : String(search.invite);
  if (!canSignUp(config.signups, invite))
    return (
      <AuthFrame title="Sign-up is closed" intro={config.signups === 'invite' ? 'This server takes new accounts by invitation. Ask its admin for an invite link.' : 'This server does not take new accounts. Its admin can create one for you.'} footer={signInLink}>
        <p className="text-sm text-ink-2">
          {APP_NAME} is free to run yourself:{' '}
          <a className="text-accent hover:underline" href={LINKS.readme}>
            how to self-host
          </a>
          .
        </p>
      </AuthFrame>
    );
  return (
    <AuthFrame title="Create your account" intro={invite ? 'You were invited to this server.' : undefined} footer={signInLink}>
      <AccountForm what="Sign-up" action="Create account" busyLabel="Creating…" submit={(body) => api('/api/auth/signup', { method: 'POST', body: { ...body, ...(invite ? { invite } : {}) } })} />
    </AuthFrame>
  );
}

export function Reset() {
  // The server's links say `?token=`; `?reset=` is accepted too.
  const search = useSearch({ strict: false }) as { reset?: string; token?: string };
  const token = search.token ?? search.reset ?? '';
  const [password, setPassword] = useState('');
  const [done, setDone] = useState(false);
  const reset = useAction();
  if (!token || done)
    return (
      <AuthFrame title={done ? 'Password changed' : 'This reset link is incomplete'} intro={done ? 'Sign in with your new password.' : 'Open the whole link you were sent, or ask the admin for a new one.'}>
        <Link to="/login" className="btn btn-primary h-10 w-full justify-center">
          Sign in
        </Link>
      </AuthFrame>
    );
  return (
    <AuthFrame title="Choose a new password">
      <form
        className="space-y-4"
        onSubmit={async (e) => {
          e.preventDefault();
          if (await reset.run(() => api('/api/auth/reset', { method: 'POST', body: { token, password } }).then(() => true))) setDone(true);
        }}
      >
        <NewPasswordField label="New password" value={password} onChange={setPassword} />
        {reset.error != null && <ErrorNote error={reset.error} what="Resetting a password" />}
        <button className="btn btn-primary h-10 w-full justify-center" disabled={reset.busy}>
          {reset.busy ? 'Saving…' : 'Set password'}
        </button>
      </form>
    </AuthFrame>
  );
}
