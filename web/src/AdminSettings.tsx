/**
 * Account, admin and import/export panels. Settings shows each in a card; the
 * setup guide reuses the key, storage and import panels.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearch } from '@tanstack/react-router';
import { useState, type ReactNode } from 'react';
import { api, invalidateLibrary, meQuery, setMe, useCapabilities, useIsDemo, type Me, type PublicConfig } from './api';
import { DEMO_UNAVAILABLE, MIGRATE_POLL_MS } from './constants';
import { ago, bytes, fullDate } from './format';
import { openOverlay, toast, toastError } from './store';
import { CopyRow, ErrorNote, NewPasswordField, Toggle, Unavailable, useAction } from './ui';

/** Shown under a form while the first answer is on its way. */
const Loading = () => <div className="skeleton h-16" aria-busy="true" />;

/** In the demo a form is shown but cannot be used; the tooltip says why. */
function DemoLock({ children }: { children: ReactNode }) {
  const demo = useIsDemo();
  return (
    <fieldset disabled={demo} title={demo ? DEMO_UNAVAILABLE : undefined} className="min-w-0 space-y-3 disabled:opacity-60">
      {children}
    </fieldset>
  );
}

// ---------------------------------------------------------------- Account

export function Account() {
  const me = useQuery(meQuery).data;
  const [name, setName] = useState(me?.name ?? '');
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const rename = useAction();
  const password = useAction();
  const caps = useCapabilities();
  if (!me) return null;
  return (
    <DemoLock>
      <p className="text-sm text-ink-2">
        Signed in as <span data-secret>{me.email}</span>
        {me.role && ` (${me.role})`}.
      </p>
      <form
        className="flex items-end gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          const saved = await rename.run(() => api<Me>('/api/auth/me', { method: 'PATCH', body: { name: name.trim() } }), 'Saved your name');
          if (saved) setMe({ ...me, ...saved });
        }}
      >
        <label className="grid flex-1 gap-1 text-sm font-medium">
          Name
          <input className="field" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <button className="btn" disabled={!caps.rename || rename.busy || !name.trim() || name.trim() === (me.name ?? '')}>
          Save name
        </button>
      </form>
      {!caps.rename && <p className="text-sm text-ink-3">Changing your name is not available on this server yet.</p>}
      {rename.error != null && <ErrorNote error={rename.error} what="Changing your name" />}
      <form
        className="space-y-2 border-t border-line pt-3"
        onSubmit={async (e) => {
          e.preventDefault();
          const ok = await password.run(() => api('/api/auth/password', { method: 'POST', body: { current, new: next } }).then(() => true), 'Password changed');
          if (ok) {
            setCurrent('');
            setNext('');
          }
        }}
      >
        <label className="grid gap-1 text-sm font-medium">
          Current password
          <input type="password" autoComplete="current-password" required className="field h-10" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <NewPasswordField label="New password" name="new-password" value={next} onChange={setNext} />
        <button className="btn" disabled={password.busy || !current || !next}>
          Change password
        </button>
        {password.error != null && <ErrorNote error={password.error} what="Changing your password" />}
      </form>
    </DemoLock>
  );
}

// ---------------------------------------------------------------- Admin: users and invites

/** One row of `GET /api/admin/users`. */
interface AdminUser {
  id: number;
  email: string;
  name: string | null;
  role: 'admin' | 'user';
  disabled: boolean;
  last_login_at: string | null;
  item_count: number;
  storage_bytes: number;
}
/** A one-time link, as the server gives it when it is made: an invite, or a password reset. It is not shown again. */
interface OneTimeLink {
  url: string;
  expires_at: string;
}
/** One row of `GET /api/admin/invites`. The link itself is only in the reply to creating it. */
interface Invite {
  id: number;
  expires_at: string;
  used_by: string | null;
  state: 'open' | 'used' | 'expired';
}

function NewUserForm({ done }: { done: () => void }) {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<'user' | 'admin'>('user');
  const create = useAction();
  return (
    <form
      className="space-y-2 rounded-md border border-line p-3"
      onSubmit={async (e) => {
        e.preventDefault();
        const ok = await create.run(() => api('/api/admin/users', { method: 'POST', body: { email: email.trim(), name: name.trim(), password, role } }).then(() => true), `Created the account for ${name.trim() || email.trim()}`);
        if (ok) done();
      }}
    >
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="grid gap-1 text-sm font-medium">
          Name
          <input className="field h-10" required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="grid gap-1 text-sm font-medium">
          Email
          <input type="email" autoComplete="off" className="field h-10" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
      </div>
      <NewPasswordField label="Their first password" name="user-password" value={password} onChange={setPassword} />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="h-4 w-4 accent-(--accent)" checked={role === 'admin'} onChange={(e) => setRole(e.target.checked ? 'admin' : 'user')} />
        Admin: can manage accounts, keys and storage
      </label>
      <button className="btn btn-primary" disabled={create.busy}>
        Create account
      </button>
      {create.error != null && <ErrorNote error={create.error} what="Creating accounts" />}
    </form>
  );
}

export function Users() {
  const client = useQueryClient();
  const me = useQuery(meQuery).data;
  const users = useQuery({ queryKey: ['admin', 'users'], queryFn: () => api<AdminUser[]>('/api/admin/users') });
  const [adding, setAdding] = useState(false);
  /** The reset link just made, shown once under the table. */
  const [reset, setReset] = useState<{ user: AdminUser; link: OneTimeLink } | null>(null);
  const refresh = () => client.invalidateQueries({ queryKey: ['admin', 'users'] });
  const change = (work: Promise<unknown>, done: string) => work.then(() => (toast(done), void refresh()), toastError);
  const label = (u: AdminUser) => u.name || u.email;

  if (users.error) return <ErrorNote error={users.error} what="Managing accounts" retry={() => void users.refetch()} />;
  if (!users.data) return <Loading />;
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[40rem] text-sm">
          <thead className="text-left text-xs text-ink-3">
            <tr>
              <th className="py-1 font-semibold">Person</th>
              <th className="py-1 font-semibold">Role</th>
              <th className="py-1 font-semibold">Last sign-in</th>
              <th className="py-1 text-right font-semibold">Items</th>
              <th className="py-1 text-right font-semibold">Storage</th>
              <th className="py-1" />
            </tr>
          </thead>
          <tbody>
            {users.data.map((u) => {
              const self = u.id === me?.id;
              return (
                <tr key={u.id} className={`border-t border-line align-top ${u.disabled ? 'text-ink-3' : ''}`}>
                  <td className="py-1.5 pr-3">
                    <span className="block font-medium">
                      {u.name || '—'} {self && <span className="font-normal text-ink-3">(you)</span>}
                    </span>
                    <span data-secret className="block text-xs text-ink-3">
                      {u.email}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3">
                    {u.role === 'admin' ? 'Admin' : 'User'}
                    {u.disabled && ', disabled'}
                  </td>
                  <td className="py-1.5 pr-3" title={fullDate(u.last_login_at)}>
                    {u.last_login_at ? ago(u.last_login_at) : 'Never'}
                  </td>
                  <td className="py-1.5 text-right tabular-nums">{(u.item_count ?? 0).toLocaleString()}</td>
                  <td className="py-1.5 text-right tabular-nums">{bytes(u.storage_bytes ?? 0)}</td>
                  <td className="whitespace-nowrap py-1.5 pl-3 text-right">
                    <button className="btn h-7 px-2 text-xs" onClick={() => api<OneTimeLink>(`/api/admin/users/${u.id}/reset-link`, { method: 'POST' }).then((link) => setReset({ user: u, link }), toastError)}>
                      Reset link
                    </button>{' '}
                    {/* An admin cannot lock themselves out from here. */}
                    <button className="btn h-7 px-2 text-xs" disabled={self} onClick={() => void change(api(`/api/admin/users/${u.id}`, { method: 'PATCH', body: { disabled: !u.disabled } }), `${u.disabled ? 'Enabled' : 'Disabled'} ${label(u)}`)}>
                      {u.disabled ? 'Enable' : 'Disable'}
                    </button>{' '}
                    <button
                      className="btn btn-danger h-7 px-2 text-xs"
                      disabled={self}
                      onClick={() =>
                        openOverlay({
                          kind: 'confirm',
                          danger: true,
                          title: `Delete the account of ${label(u)}?`,
                          body: `This deletes the account, its ${(u.item_count ?? 0).toLocaleString()} items and their stored files. It cannot be undone. To keep the data, disable the account instead.`,
                          action: 'Delete account',
                          run: () => change(api(`/api/admin/users/${u.id}`, { method: 'DELETE' }), `Deleted the account of ${label(u)}`),
                        })
                      }
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {reset && (
        <div role="status" className="space-y-2 text-sm">
          <p>
            Send this link to {label(reset.user)}. It sets a new password once and stops working {ago(reset.link.expires_at)}.
          </p>
          <CopyRow value={reset.link.url} what="the reset link" />
        </div>
      )}
      {adding ? (
        <NewUserForm done={() => (setAdding(false), void refresh())} />
      ) : (
        <button className="btn" onClick={() => setAdding(true)}>
          Create an account
        </button>
      )}
      <Invites />
    </>
  );
}

function Invites() {
  const client = useQueryClient();
  const invites = useQuery({ queryKey: ['admin', 'invites'], queryFn: () => api<Invite[]>('/api/admin/invites') });
  /** The link just made, shown once. */
  const [made, setMade] = useState<OneTimeLink | null>(null);
  const create = useAction();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['admin', 'invites'] });
    // The first invite turns closed sign-up into "by invite".
    void client.invalidateQueries({ queryKey: ['admin', 'instance'] });
  };
  return (
    <div className="space-y-2 border-t border-line pt-3">
      <h3 className="text-sm font-semibold">Invites</h3>
      <p className="text-sm text-ink-2">An invite link lets one person create their own account. Making one turns sign-up to “by invite” if it was closed.</p>
      <button className="btn" disabled={create.busy} onClick={async () => (setMade((await create.run(() => api<OneTimeLink>('/api/admin/invites', { method: 'POST', body: {} }))) ?? null), refresh())}>
        Create an invite link
      </button>
      {made && (
        <div role="status" className="space-y-1 text-sm">
          <p>Send this link to the person you are inviting. It is shown only now and stops working {ago(made.expires_at)}.</p>
          <CopyRow value={made.url} what="the invite link" />
        </div>
      )}
      {create.error != null && <ErrorNote error={create.error} what="Creating invites" />}
      {invites.error ? (
        <ErrorNote error={invites.error} what="Invites" />
      ) : (
        <ul className="divide-y divide-line text-sm">
          {invites.data?.map((invite) => (
            <li key={invite.id} className="flex items-center gap-2 py-1.5">
              <span className="flex-1 text-ink-2">
                {invite.state === 'used' ? (
                  <>
                    Used by <span data-secret>{invite.used_by ?? 'someone'}</span>
                  </>
                ) : invite.state === 'expired' ? (
                  `Expired ${ago(invite.expires_at)}`
                ) : (
                  `Open, stops working ${ago(invite.expires_at)}`
                )}
              </span>
              <button className="text-xs text-danger hover:underline" onClick={() => api(`/api/admin/invites/${invite.id}`, { method: 'DELETE' }).then(refresh, toastError)}>
                {invite.state === 'open' ? 'Revoke' : 'Remove'}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Admin: instance

const SIGNUP_MODES: { value: PublicConfig['signups']; label: string }[] = [
  { value: 'closed', label: 'Closed: only an admin creates accounts' },
  { value: 'invite', label: 'By invite link' },
  { value: 'open', label: 'Open: anyone who reaches this server can sign up' },
];
/** What each mode means for someone arriving at the sign-up page; shown under the select. */
const SIGNUP_HELP: Record<PublicConfig['signups'], string> = {
  closed: 'Nobody can sign up, and invite links do not work. Creating an invite link above switches this to “By invite link”.',
  invite: 'Only someone with an unused invite link (under Users) can sign up. Creating an invite turns this on; revoking invites does not turn it off, so choose Closed here when you want sign-up shut again.',
  open: 'Anyone who can reach this address can create an account, with or without an invite.',
};

export function Instance() {
  const client = useQueryClient();
  const instance = useQuery({ queryKey: ['admin', 'instance'], queryFn: () => api<Pick<PublicConfig, 'signups' | 'landing'>>('/api/admin/instance') });
  async function patch(body: Partial<Pick<PublicConfig, 'signups' | 'landing'>>) {
    try {
      client.setQueryData(['admin', 'instance'], await api('/api/admin/instance', { method: 'PATCH', body }));
      // The sign-in pages read the same two values from the public config.
      void client.invalidateQueries({ queryKey: ['public-config'] });
      toast('Saved');
    } catch (error) {
      toastError(error);
    }
  }
  if (instance.error) return <ErrorNote error={instance.error} what="Changing instance settings" retry={() => void instance.refetch()} />;
  if (!instance.data) return <Loading />;
  return (
    <>
      <label className="grid gap-1 text-sm font-medium">
        Sign-up
        <select className="field max-w-full" value={instance.data.signups} onChange={(e) => void patch({ signups: e.target.value as PublicConfig['signups'] })}>
          {SIGNUP_MODES.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
        <span className="font-normal text-ink-2">{SIGNUP_HELP[instance.data.signups]}</span>
      </label>
      <Toggle label="Public landing page" help="Visitors who are not signed in see a page about Kosha at this address. Off: they see the sign-in form." checked={instance.data.landing} onChange={(v) => void patch({ landing: v })} />
    </>
  );
}

// ---------------------------------------------------------------- Admin: API keys

interface ApiKey {
  name: string;
  set: boolean;
  source: 'env' | 'db' | null;
  last4: string | null;
  /** `transcription` only: which service and model do the transcripts. */
  provider?: string | null;
  model?: string | null;
}
/** The four keys the server knows, in the order shown, with what each one turns on. */
const KEY_INFO: { name: string; label: string; unlocks: string }[] = [
  { name: 'voyage', label: 'Voyage', unlocks: 'Search by meaning, not only by the words in a post.' },
  { name: 'typesafe', label: 'TypeSafe Jev', unlocks: 'Yes/no checks over many posts, and automatic labelling.' },
  { name: 'openai', label: 'OpenAI or another LLM', unlocks: 'Ask, summaries and image descriptions.' },
  { name: 'transcription', label: 'Transcription provider', unlocks: 'Transcripts of video and audio.' },
];

function KeyRow({ info, state, refresh }: { info: (typeof KEY_INFO)[number]; state: ApiKey | undefined; refresh: () => void }) {
  const [value, setValue] = useState('');
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const save = useAction();
  const test = useAction();
  return (
    <li className="space-y-1.5 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-sm font-medium">{info.label}</span>
        <span className={`text-xs ${state?.set ? 'text-ok' : 'text-ink-3'}`}>
          {state?.set ? 'Set' : 'Not set'}
          {state?.set && state.last4 && (
            <>
              , ends in <span data-secret>{state.last4}</span>
            </>
          )}
          {state?.set && state.source === 'env' && ' (from .env)'}
        </span>
      </div>
      <p className="text-sm text-ink-2">
        {info.unlocks}
        {state?.provider && ` Uses ${[state.provider, state.model].filter(Boolean).join(', ')}.`}
      </p>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          await save.run(() => api('/api/admin/keys', { method: 'PUT', body: { name: info.name, value: value.trim() } }), `Saved the ${info.label} key`);
          setValue('');
          setResult(null);
          refresh();
        }}
      >
        <input type="password" autoComplete="off" spellCheck={false} aria-label={`${info.label} API key`} placeholder={state?.set ? 'Paste a new key to replace it' : 'Paste the key'} className="field min-w-0 flex-1" value={value} onChange={(e) => setValue(e.target.value)} />
        <button className="btn" disabled={save.busy || !value.trim()}>
          Save
        </button>
        <button type="button" className="btn" disabled={test.busy || !state?.set} onClick={async () => setResult((await test.run(() => api<{ ok: boolean; message: string }>('/api/admin/keys/test', { method: 'POST', body: { name: info.name } }))) ?? null)}>
          {test.busy ? 'Testing…' : 'Test'}
        </button>
        {/* A key from .env is not Kosha's to remove; one saved here is. */}
        {state?.source === 'db' && (
          <button type="button" className="btn btn-ghost px-2 text-danger" onClick={() => api(`/api/admin/keys/${info.name}`, { method: 'DELETE' }).then(() => (toast(`Removed the ${info.label} key`), setResult(null), refresh()), toastError)}>
            Remove
          </button>
        )}
      </form>
      {result && (
        <p role="status" className={`text-sm ${result.ok ? 'text-ok' : 'text-danger'}`}>
          {result.ok ? 'Works. ' : 'Did not work. '}
          {result.message}
        </p>
      )}
      {(save.error ?? test.error) != null && <ErrorNote error={save.error ?? test.error} what="Managing API keys here" />}
    </li>
  );
}

export function ApiKeys() {
  const client = useQueryClient();
  const keys = useQuery({ queryKey: ['admin', 'keys'], queryFn: () => api<ApiKey[]>('/api/admin/keys') });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['admin', 'keys'] });
    // A new key can turn on Ask, Jev or media text.
    void client.invalidateQueries({ queryKey: ['capabilities'] });
  };
  if (keys.error) return <ErrorNote error={keys.error} what="Managing API keys here" retry={() => void keys.refetch()} />;
  if (!keys.data) return <Loading />;
  return (
    <ul className="divide-y divide-line">
      {KEY_INFO.map((info) => (
        <KeyRow key={info.name} info={info} state={keys.data.find((k) => k.name === info.name)} refresh={refresh} />
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- Admin: storage

type Backend = 'local' | 's3';
/** `GET /api/admin/storage`. The S3 secret is never sent back; `key_set` says one is stored. */
interface StorageConfig {
  backend: Backend;
  local_dir: string;
  s3: { endpoint: string | null; region: string | null; bucket: string | null; prefix: string | null; path_style: boolean; serve_mode: string; public_base_url: string | null; key_set: boolean };
  /** `error` when the active storage could not be reached to count. */
  usage: { files: number | null; bytes: number | null; error?: string };
}
/** `GET /api/admin/storage/migrate`: the last or current copy of every file to the other storage. */
interface Migration {
  running: boolean;
  to?: Backend;
  total?: number;
  copied?: number;
  failed?: number;
  switched?: boolean;
  message?: string | null;
}
const BACKEND_NAME: Record<Backend, string> = { local: 'this server’s disk', s3: 'the S3-compatible storage' };
const SERVE_MODES = [
  { value: 'proxy', label: 'Through Kosha (the bucket stays private)' },
  { value: 'presigned', label: 'Short-lived links straight to the bucket' },
];

function StorageForm({ stored, withMigrate }: { stored: StorageConfig; withMigrate: boolean }) {
  const client = useQueryClient();
  const [backend, setBackend] = useState(stored.backend);
  const [s3, setS3] = useState(stored.s3);
  const [accessKey, setAccessKey] = useState('');
  const [secretKey, setSecretKey] = useState('');
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const save = useAction();
  const test = useAction();
  /** Choosing the other storage drops the last test result and error: they were about the one left. */
  const pick = (b: Backend) => {
    setBackend(b);
    setResult(null);
    save.clear();
    test.clear();
  };
  const text = (key: 'endpoint' | 'region' | 'bucket' | 'prefix' | 'public_base_url', label: string, placeholder = '') => (
    <label className="grid gap-1 text-sm font-medium">
      {label}
      <input className="field" spellCheck={false} placeholder={placeholder} value={s3[key] ?? ''} onChange={(e) => setS3({ ...s3, [key]: e.target.value })} />
    </label>
  );
  // The keys go only when typed: an empty field keeps what the server has.
  const s3Body = () => ({ ...s3, key_set: undefined, ...(accessKey ? { access_key_id: accessKey } : {}), ...(secretKey ? { secret_access_key: secretKey } : {}) });
  /** Saves the S3 fields without changing which storage is active, so they can be tested or copied to first. */
  const saveS3 = async () => {
    client.setQueryData(['admin', 'storage'], await api<StorageConfig>('/api/admin/storage', { method: 'PATCH', body: { s3: s3Body() } }));
    setAccessKey('');
    setSecretKey('');
  };
  return (
    <form
      className="space-y-3"
      onSubmit={async (e) => {
        e.preventDefault();
        const saved = await save.run(() => api<StorageConfig>('/api/admin/storage', { method: 'PATCH', body: { backend, ...(backend === 's3' ? { s3: s3Body() } : {}) } }), 'Saved the storage settings');
        if (saved) {
          client.setQueryData(['admin', 'storage'], saved);
          setAccessKey('');
          setSecretKey('');
        }
      }}
    >
      <fieldset className="space-y-1.5">
        <legend className="mb-1 text-sm font-medium">Where files go</legend>
        <label className="flex items-start gap-2 text-sm">
          <input type="radio" name="storage-backend" className="mt-1 accent-(--accent)" checked={backend === 'local'} onChange={() => pick('local')} />
          <span>
            This server’s disk
            <code className="block break-all text-xs text-ink-3">{stored.local_dir}</code>
          </span>
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input type="radio" name="storage-backend" className="mt-1 accent-(--accent)" checked={backend === 's3'} onChange={() => pick('s3')} />
          <span>
            S3-compatible storage
            <span className="block text-xs text-ink-3">Amazon S3, Cloudflare R2, Backblaze B2, MinIO and others.</span>
          </span>
        </label>
      </fieldset>
      {backend === 's3' && (
        <div className="grid gap-2 sm:grid-cols-2">
          {text('endpoint', 'Endpoint', 'https://…')}
          {text('region', 'Region', 'auto')}
          {text('bucket', 'Bucket')}
          {text('prefix', 'Folder inside the bucket (optional)')}
          <label className="grid gap-1 text-sm font-medium">
            Access key id
            <input className="field" autoComplete="off" spellCheck={false} placeholder={s3.key_set ? 'Saved; type to replace' : ''} value={accessKey} onChange={(e) => setAccessKey(e.target.value)} />
          </label>
          <label className="grid gap-1 text-sm font-medium">
            Secret access key
            <input type="password" autoComplete="off" className="field" placeholder={s3.key_set ? 'Saved; type to replace' : ''} value={secretKey} onChange={(e) => setSecretKey(e.target.value)} />
          </label>
          <label className="grid gap-1 text-sm font-medium">
            How files are served
            <select className="field" value={s3.serve_mode} onChange={(e) => setS3({ ...s3, serve_mode: e.target.value })}>
              {SERVE_MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          {text('public_base_url', 'Public address of the bucket (optional)', 'https://media.example.com')}
          <label className="flex items-center gap-2 text-sm sm:col-span-2">
            <input type="checkbox" className="h-4 w-4 accent-(--accent)" checked={!!s3.path_style} onChange={(e) => setS3({ ...s3, path_style: e.target.checked })} />
            Path-style addresses (needed by MinIO and some other providers)
          </label>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn" disabled={save.busy}>
          {backend === stored.backend ? 'Save storage' : `Save and use ${BACKEND_NAME[backend]} for new files`}
        </button>
        {/* The test runs on the server's saved settings, so the S3 fields are saved first (the active storage stays as it is). */}
        <button
          type="button"
          className="btn"
          disabled={test.busy}
          onClick={async () =>
            setResult(
              (await test.run(async () => {
                if (backend === 's3') await saveS3();
                return api<{ ok: boolean; message: string }>('/api/admin/storage/test', { method: 'POST', body: { backend } });
              })) ?? null,
            )
          }
        >
          {test.busy ? 'Testing…' : 'Test'}
        </button>
        {result && (
          <span role="status" className={`text-sm ${result.ok ? 'text-ok' : 'text-danger'}`}>
            {result.ok ? 'Works. ' : 'Did not work. '}
            {result.message}
          </span>
        )}
      </div>
      {(save.error ?? test.error) != null && <ErrorNote error={save.error ?? test.error} what="Changing storage here" />}
      <p className="text-sm text-ink-2">{stored.usage.error ? `The storage in use could not be read: ${stored.usage.error}` : `In use on ${BACKEND_NAME[stored.backend]}: ${(stored.usage.files ?? 0).toLocaleString()} files, ${bytes(stored.usage.bytes)}.`}</p>
      {withMigrate && <Migrate to={stored.backend === 'local' ? 's3' : 'local'} prepare={stored.backend === 'local' ? saveS3 : async () => undefined} />}
    </form>
  );
}

/** Copies every stored file to the other storage, with progress; the server switches over only after a complete copy. */
function Migrate({ to, prepare }: { to: Backend; prepare: () => Promise<void> }) {
  const client = useQueryClient();
  const migration = useQuery({ queryKey: ['admin', 'storage-migrate'], queryFn: () => api<Migration>('/api/admin/storage/migrate'), refetchInterval: (q) => (q.state.data?.running ? MIGRATE_POLL_MS : false) });
  const start = useAction();
  const m = migration.data;
  if (migration.error) return <ErrorNote error={migration.error} what="Moving stored files" />;
  return (
    <div className="space-y-2 border-t border-line pt-3">
      <p className="text-sm text-ink-2">Saving above changes where new files go and copies nothing. To take the files already stored along, move them: Kosha copies every file to {BACKEND_NAME[to]}, checks the copy, and only then switches. The originals are left in place.</p>
      <button
        type="button"
        className="btn"
        disabled={m?.running || start.busy}
        onClick={() =>
          openOverlay({
            kind: 'confirm',
            title: `Move the stored files to ${BACKEND_NAME[to]}?`,
            body: 'Every stored file is copied there, and Kosha switches to it when the copy is complete. This can take a while and uses bandwidth.',
            action: 'Start the move',
            run: async () => {
              await start.run(async () => {
                await prepare();
                client.setQueryData(['admin', 'storage-migrate'], await api<Migration>('/api/admin/storage/migrate', { method: 'POST', body: { to } }));
              }, 'Started moving files');
              void client.invalidateQueries({ queryKey: ['admin', 'storage-migrate'] });
            },
          })
        }
      >
        Move stored files to {BACKEND_NAME[to]}
      </button>
      {m && (m.running || m.total != null) && (
        <div role="status" className="text-sm">
          <p>
            {m.running ? 'Moving' : m.switched ? 'Moved' : 'Stopped'}: {(m.copied ?? 0).toLocaleString()} of {(m.total ?? 0).toLocaleString()} files{m.failed ? `, ${m.failed.toLocaleString()} failed` : ''}.{m.message ? ` ${m.message}` : ''}
          </p>
          <progress className="mt-1 h-1.5 w-full accent-(--accent)" value={m.copied ?? 0} max={m.total || 1} />
        </div>
      )}
      {start.error != null && <ErrorNote error={start.error} what="Moving stored files" />}
    </div>
  );
}

export function Storage({ withMigrate = true }: { withMigrate?: boolean }) {
  const storage = useQuery({ queryKey: ['admin', 'storage'], queryFn: () => api<StorageConfig>('/api/admin/storage') });
  if (storage.error) return <ErrorNote error={storage.error} what="Changing storage here" retry={() => void storage.refetch()} />;
  if (!storage.data) return <Loading />;
  // Keyed by the stored backend so the form shows what the server holds after a switch.
  return <StorageForm key={storage.data.backend} stored={storage.data} withMigrate={withMigrate} />;
}

// ---------------------------------------------------------------- Import and export

/** `POST /api/import`. With `dry_run` nothing is written and `rows` (`preview` in the contract) lists the first items found. */
interface ImportReport {
  created: number;
  duplicates: number;
  skipped: number;
  errors: (string | { message?: string; row?: number })[] | number;
  rows?: { title?: string | null; url?: string | null }[];
  preview?: { title?: string | null; url?: string | null }[];
}
const IMPORT_FORMATS = [
  { value: '', label: 'Detect from the file' },
  { value: 'raindrop_csv', label: 'Raindrop CSV' },
  { value: 'netscape', label: 'Browser bookmarks (HTML)' },
  { value: 'csv', label: 'CSV with a url column' },
];

export function Import() {
  const caps = useCapabilities();
  const demo = useIsDemo();
  const [file, setFile] = useState<File | null>(null);
  const [format, setFormat] = useState('');
  /** The dry run's answer; importing for real is offered only after it. */
  const [preview, setPreview] = useState<ImportReport | null>(null);
  const [done, setDone] = useState<ImportReport | null>(null);
  const send = useAction();
  const found = (r: ImportReport) => r.rows ?? r.preview ?? [];
  // `errors` explains the skipped rows (the server keeps the first few), so it is listed, not counted.
  const reasons = (r: ImportReport) => (Array.isArray(r.errors) ? r.errors.map((e) => (typeof e === 'string' ? e : (e.message ?? ''))).filter(Boolean) : []);
  const counts = (r: ImportReport) => `${r.created.toLocaleString()} new, ${r.duplicates.toLocaleString()} already in your library, ${r.skipped.toLocaleString()} skipped`;

  async function post(dryRun: boolean) {
    if (!file) return;
    const form = new FormData();
    form.set('file', file);
    if (format) form.set('format', format);
    form.set('dry_run', String(dryRun));
    const report = await send.run(() => api<ImportReport>('/api/import', { method: 'POST', form }));
    if (!report) return;
    if (dryRun) return setPreview(report);
    setDone(report);
    setPreview(null);
    setFile(null);
    invalidateLibrary();
  }

  if (demo) return <Unavailable what="Importing a file" />;
  return (
    <form className="space-y-2" onSubmit={(e) => (e.preventDefault(), void post(true))}>
      <p className="text-sm text-ink-2">A Raindrop export, a browser’s bookmarks file, or any CSV with a url column. Kosha first shows what it found; nothing is saved until you confirm.</p>
      <div className="flex flex-wrap items-center gap-2">
        {/* Keyed so the field empties after an import. */}
        <input key={done ? `done${done.created}` : 'file'} type="file" accept=".csv,.html,.htm,text/csv,text/html" aria-label="File to import" className="min-w-0 flex-1 text-sm file:mr-2 file:rounded-md file:border file:border-line file:bg-surface file:px-2 file:py-1 file:text-sm file:text-ink" onChange={(e) => (setFile(e.target.files?.[0] ?? null), setPreview(null), setDone(null))} />
        <select aria-label="File format" className="field" value={format} onChange={(e) => (setFormat(e.target.value), setPreview(null))}>
          {IMPORT_FORMATS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
        <button className="btn" disabled={!file || send.busy}>
          {send.busy && !preview ? 'Reading…' : 'Preview'}
        </button>
      </div>
      {!caps.import && send.error == null && <p className="text-sm text-ink-3">Importing is not available on this server yet.</p>}
      {send.error != null && <ErrorNote error={send.error} what="Importing a file" />}
      {preview && (
        <div role="status" className="space-y-2 rounded-md bg-sunken px-3 py-2 text-sm">
          <p>
            <span className="font-medium">Found:</span> {counts(preview)}.
          </p>
          {!!found(preview).length && (
            <ul className="space-y-0.5 text-ink-2">
              {found(preview).slice(0, 5).map((row, i) => (
                <li key={i} className="truncate">
                  {row.title || row.url}
                </li>
              ))}
            </ul>
          )}
          {reasons(preview).length > 0 && (
            <details className="text-ink-2">
              <summary>Why rows were skipped</summary>
              <ul className="mt-1 space-y-0.5">
                {reasons(preview).map((reason, i) => (
                  <li key={i}>{reason}</li>
                ))}
              </ul>
            </details>
          )}
          <button type="button" className="btn btn-primary" disabled={send.busy || preview.created === 0} onClick={() => void post(false)}>
            {send.busy ? 'Importing…' : `Import ${preview.created.toLocaleString()} item${preview.created === 1 ? '' : 's'}`}
          </button>
        </div>
      )}
      {done && (
        <p role="status" className="text-sm text-ok">
          Imported: {counts(done)}.
        </p>
      )}
    </form>
  );
}

const EXPORT_FORMATS = [
  { value: 'csv', label: 'CSV' },
  { value: 'html', label: 'Bookmarks HTML' },
  { value: 'json', label: 'JSON' },
];

/** Plain download links: the browser sends the session cookie, and the server streams the file. */
export function Export() {
  const caps = useCapabilities();
  // "Export the current search" in the command palette arrives with that search in the URL.
  const [q, setQ] = useState((useSearch({ strict: false }) as { q?: string }).q ?? '');
  const href = (format: string) => `/api/export?${new URLSearchParams({ format, ...(q.trim() ? { q: q.trim() } : {}) })}`;
  if (!caps.export) return <Unavailable what="Exporting your library" />;
  return (
    <div className="space-y-2">
      <p className="text-sm text-ink-2">Download everything, or only what a search finds. CSV and HTML open in other bookmark tools; JSON holds every field.</p>
      <label className="grid gap-1 text-sm font-medium">
        Only items matching (leave empty for everything)
        <input className="field" spellCheck={false} placeholder="A search, for example #AI/Agents saved:2026" value={q} onChange={(e) => setQ(e.target.value)} />
      </label>
      <div className="flex flex-wrap gap-2">
        {EXPORT_FORMATS.map((f) => (
          <a key={f.value} className="btn" href={href(f.value)} download>
            Export {q.trim() ? 'this search' : 'everything'} as {f.label}
          </a>
        ))}
      </div>
    </div>
  );
}
