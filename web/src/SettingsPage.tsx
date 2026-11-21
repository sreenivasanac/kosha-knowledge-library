/** Settings: storage, X API sync and spend, media text, API tokens, the extension, and key remapping. */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { askExtension, getExtensionId } from './ArchiveTools';
import { Link, useSearch } from '@tanstack/react-router';
import { Account, ApiKeys, Export, Import, Instance, Storage, Users } from './AdminSettings';
import { api, isAdmin, keybindingsQuery, meQuery, settingsQuery, useCapabilities, useIsDemo } from './api';
import { DEMO_UNAVAILABLE, SEQUENCE_TIMEOUT_MS, STORAGE_KEYS } from './constants';
import { ago, fullDate, shortDate, usd } from './format';
import { startSync } from './Palette';
import { syncQuery, xApiSync } from './Shell';
import { ACTIONS, defaultBindings, findConflicts, resolveBindings, strokeOf, type Area } from './keymap';
import { openOverlay, toast, toastError } from './store';
import { PageHeader } from './TagsPage';
import { Card, ErrorNote, Keys, Toggle, Unavailable } from './ui';

/**
 * What a status endpoint reports, as label and value rows. The server owns the
 * shape (spend, sync and media-text status differ and may grow), so this shows
 * every plain value it sends rather than a fixed list of fields.
 */
function Facts({ data }: { data: Record<string, unknown> }) {
  const rows = Object.entries(data).filter(([, v]) => v != null && typeof v !== 'object');
  const show = (key: string, v: unknown) => (typeof v === 'number' ? (/usd|cost|spen[dt]|cap|budget/.test(key) ? usd(v) : v.toLocaleString()) : typeof v === 'boolean' ? (v ? 'Yes' : 'No') : /^\d{4}-\d\d-\d\dT/.test(String(v)) ? fullDate(String(v)) : String(v));
  if (!rows.length) return <p className="text-sm text-ink-3">Nothing reported yet.</p>;
  return (
    <dl className="grid grid-cols-[minmax(0,14rem)_1fr] gap-x-3 gap-y-1 text-sm">
      {rows.map(([key, v]) => (
        <div key={key} className="contents">
          <dt className="text-ink-3 first-letter:uppercase">{key.replace(/_usd$/, '').replaceAll('_', ' ')}</dt>
          <dd className="min-w-0 break-words tabular-nums">{show(key, v)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The last X API sync, from `GET /api/sync`. */
function SyncFacts() {
  const sync = useQuery(syncQuery);
  const row = xApiSync(sync.data);
  if (sync.error) return <ErrorNote error={sync.error} what="Sync status" retry={() => void sync.refetch()} />;
  if (!sync.data) return <div className="skeleton h-12" aria-busy="true" />;
  return row ? <Facts data={{ running: row.running, ...row.state }} /> : <p className="text-sm text-ink-3">No API sync has run yet.</p>;
}

/** `GET /api/spend`: this calendar month by provider, and each monthly cap with what counts against it. */
interface SpendReport {
  month: string;
  total_usd: number;
  providers: { provider: string; usd: number; calls: number }[];
  caps: { name: string; setting: string; cap_usd: number; spent_usd: number }[];
}
const CAP_LABEL: Record<string, string> = { x: 'X API sync', jev: 'Jev checks', llm: 'LLM and speech-to-text (Ask, summaries, labels, media text)' };

function Spend() {
  const spend = useQuery({ queryKey: ['spend'], queryFn: () => api<SpendReport>('/api/spend') });
  if (spend.error) return <ErrorNote error={spend.error} what="The spending report" retry={() => void spend.refetch()} />;
  const d = spend.data;
  if (!d) return <div className="skeleton h-24" aria-busy="true" />;
  return (
    <>
      <p className="text-sm">
        <span className="text-2xl font-semibold tabular-nums">{usd(d.total_usd)}</span> <span className="text-ink-3">this month ({d.month})</span>
      </p>
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-ink-3">
          <tr>
            <th className="py-1 font-semibold">Provider</th>
            <th className="py-1 text-right font-semibold">Calls</th>
            <th className="py-1 text-right font-semibold">Cost</th>
          </tr>
        </thead>
        <tbody>
          {d.providers.map((p) => (
            <tr key={p.provider} className="border-t border-line">
              <td className="py-1.5">{p.provider}</td>
              <td className="py-1.5 text-right tabular-nums">{p.calls.toLocaleString()}</td>
              <td className="py-1.5 text-right tabular-nums">{usd(p.usd)}</td>
            </tr>
          ))}
          {d.providers.length === 0 && (
            <tr className="border-t border-line">
              <td colSpan={3} className="py-1.5 text-ink-3">
                Nothing spent yet this month.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      <h3 className="pt-1 text-xs font-semibold text-ink-3">Monthly caps</h3>
      <ul className="space-y-2 text-sm">
        {d.caps.map((c) => (
          <li key={c.name}>
            <div className="flex justify-between gap-3">
              <span>{CAP_LABEL[c.name] ?? c.name}</span>
              <span className="shrink-0 tabular-nums">
                {usd(c.spent_usd)} of {usd(c.cap_usd)}
              </span>
            </div>
            {/* The numbers above say it; the bar only shows how near the cap is. */}
            <div aria-hidden className="mt-1 h-1.5 overflow-hidden rounded-full bg-sunken">
              <div className={`h-full ${c.spent_usd >= c.cap_usd ? 'bg-danger' : 'bg-accent'}`} style={{ width: `${c.cap_usd > 0 ? Math.min(100, (c.spent_usd / c.cap_usd) * 100) : 100}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

/** `GET /api/media-text/status`. `job` is the last run: its totals and the reason of each failure by media id. */
interface MediaTextStatus {
  counts: { none: number; queued: number; done: number; failed: number };
  running: boolean;
  job: (Record<string, unknown> & { errors?: Record<string, string> }) | null;
  ffmpeg: boolean;
}

function MediaTextStatusFacts() {
  const status = useQuery({ queryKey: ['media-text-status'], queryFn: () => api<MediaTextStatus>('/api/media-text/status'), refetchInterval: (q) => (q.state.data?.running ? 5_000 : 60_000) });
  if (status.error) return <ErrorNote error={status.error} what="Media text progress" retry={() => void status.refetch()} />;
  const d = status.data;
  if (!d) return <div className="skeleton h-12" aria-busy="true" />;
  const errors = Object.entries(d.job?.errors ?? {});
  return (
    <>
      <Facts data={{ media_without_text: d.counts.none, queued: d.counts.queued, with_text: d.counts.done, failed: d.counts.failed, running: d.running }} />
      {!d.ffmpeg && <p className="text-sm text-danger">ffmpeg is not installed on the server, so video and audio cannot be transcribed.</p>}
      {d.job && (
        <details className="text-sm">
          <summary className="text-ink-2">The last run</summary>
          <div className="pt-2">
            <Facts data={{ ...d.job, running: undefined }} />
            {errors.length > 0 && (
              <ul className="mt-2 space-y-0.5 text-ink-2">
                {errors.map(([id, reason]) => (
                  <li key={id}>
                    Media {id}: {reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </details>
      )}
    </>
  );
}

interface Token {
  id: number;
  name: string;
  created_at?: string;
  last_used_at?: string | null;
  /** Only in the reply to creating one. */
  token?: string;
}

function Tokens() {
  const caps = useCapabilities();
  const client = useQueryClient();
  const tokens = useQuery({ queryKey: ['tokens'], queryFn: () => api<Token[]>('/api/tokens'), enabled: caps.tokens });
  const [name, setName] = useState('');
  const [created, setCreated] = useState<string | null>(null);
  if (!caps.tokens) return <Unavailable what="Managing API tokens here" />;
  const refresh = () => client.invalidateQueries({ queryKey: ['tokens'] });
  return (
    <>
      <p className="text-sm text-ink-2">Tokens let the extension, the MCP server and scripts use your library. A token is shown once, when it is created.</p>
      {tokens.error && <ErrorNote error={tokens.error} what="Managing API tokens here" />}
      <ul className="divide-y divide-line text-sm">
        {tokens.data?.map((t) => (
          <li key={t.id} className="flex items-center justify-between gap-3 py-1.5">
            <span className="min-w-0">
              <span className="block truncate">{t.name}</span>
              <span className="block text-xs text-ink-3">
                Created {shortDate(t.created_at ?? null)} · {t.last_used_at ? `last used ${ago(t.last_used_at)}` : 'never used'}
              </span>
            </span>
            <button className="btn btn-danger h-7 px-2 text-xs" onClick={() => openOverlay({ kind: 'confirm', danger: true, title: `Revoke the token ${t.name}?`, body: 'Anything using it stops working at once.', action: 'Revoke', run: () => api(`/api/tokens/${t.id}`, { method: 'DELETE' }).then(refresh, toastError) })}>
              Revoke
            </button>
          </li>
        ))}
      </ul>
      <form
        className="flex gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            setCreated((await api<Token>('/api/tokens', { method: 'POST', body: { name: name.trim() } })).token ?? null);
            setName('');
            void refresh();
          } catch (error) {
            toastError(error);
          }
        }}
      >
        <input aria-label="Name for a new token" placeholder="Name, for example: extension" className="field flex-1" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn" disabled={!name.trim()}>
          Create token
        </button>
      </form>
      {created && (
        <div role="status" className="space-y-2 rounded-md bg-sunken px-3 py-2 text-sm">
          <p>Copy the new token now; it is not shown again.</p>
          <div className="flex items-center gap-2">
            <code data-secret className="min-w-0 flex-1 break-all">{created}</code>
            <button type="button" className="btn shrink-0" onClick={() => navigator.clipboard.writeText(created).then(() => toast('Copied the token'), () => toast('The browser blocked copying', { error: true }))}>
              Copy
            </button>
            <button type="button" className="btn btn-ghost shrink-0" onClick={() => setCreated(null)}>
              Done
            </button>
          </div>
        </div>
      )}
    </>
  );
}

export function Extension() {
  const demo = useIsDemo();
  const [id, setId] = useState(getExtensionId);
  const [status, setStatus] = useState('');
  async function test() {
    try {
      localStorage.setItem(STORAGE_KEYS.extensionId, id.trim());
    } catch {
      /* private mode: the id works until the tab closes */
    }
    const reply = await askExtension('kosha:ping');
    setStatus(reply?.ok ? `Connected (version ${reply.version ?? '?'})` : (reply?.detail ?? 'Extension not connected. Check the id, and that the extension is installed in this browser and set to this server.'));
  }
  return (
    <form className="space-y-2" onSubmit={(e) => (e.preventDefault(), void test())}>
      {demo && <Unavailable what="Connecting the extension" />}
      <p className="text-sm text-ink-2">The Archive page asks the extension to remove items at their sources. Its id is on the browser’s extensions page.</p>
      <div className="flex gap-2">
        <input aria-label="Extension id" placeholder="Extension id" spellCheck={false} className="field flex-1" value={id} onChange={(e) => setId(e.target.value)} />
        <button className="btn" disabled={demo} title={demo ? DEMO_UNAVAILABLE : undefined}>
          Save and test
        </button>
      </div>
      {status && (
        <p role="status" className="text-sm text-ink-2">
          {status}
        </p>
      )}
    </form>
  );
}

function Keybindings() {
  const client = useQueryClient();
  const overrides = useQuery(keybindingsQuery).data ?? {};
  const bindings = resolveBindings(overrides);
  const defaults = defaultBindings();
  const [capturing, setCapturing] = useState<string | null>(null);
  const [strokes, setStrokes] = useState<string[]>([]);
  const [problem, setProblem] = useState('');
  const label = (id: string) => ACTIONS.find((a) => a.id === id)?.label ?? id;

  async function save(next: Record<string, string>) {
    // Only what differs from the defaults is stored, so new defaults still reach the user.
    const changed = Object.fromEntries(Object.entries(next).filter(([id, keys]) => keys !== defaults[id]));
    try {
      client.setQueryData(['keybindings'], await api('/api/keybindings', { method: 'PUT', body: changed }));
    } catch (error) {
      toastError(error);
    }
  }

  function commit(id: string, keys: string) {
    const next = { ...bindings, [id]: keys };
    const clash = findConflicts(next).find((c) => c.a === id || c.b === id);
    if (clash) return setProblem(`${keys} is already used by “${label(clash.a === id ? clash.b : clash.a)}”. Change that one first, or pick other keys.`);
    setProblem('');
    void save(next);
    toast(`“${label(id)}” is now on ${keys}`);
  }

  // Capture mode: every key press adds a stroke; a pause ends the sequence. Esc cancels.
  useEffect(() => {
    if (!capturing) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === 'Escape') return (setCapturing(null), setStrokes([]));
      const stroke = strokeOf(e);
      if (stroke) setStrokes((s) => [...s, stroke].slice(0, 3));
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [capturing]);
  useEffect(() => {
    if (!capturing || !strokes.length) return;
    const timer = setTimeout(() => {
      commit(capturing, strokes.join(' '));
      setCapturing(null);
      setStrokes([]);
    }, SEQUENCE_TIMEOUT_MS);
    return () => clearTimeout(timer);
    // `commit` reads the current bindings; re-running on its identity would restart the timer.
  }, [capturing, strokes]); // eslint-disable-line react-hooks/exhaustive-deps

  const existing = findConflicts(bindings);
  const areas = [...new Set(ACTIONS.map((a) => a.area))] as Area[];
  return (
    <>
      <p className="text-sm text-ink-2">Choose Change, then press the new key or sequence (for example g then a). It is saved after a short pause; Esc cancels. Escape itself cannot be reassigned here.</p>
      {problem && (
        <p role="alert" className="rounded-md border border-danger/40 px-3 py-2 text-sm text-danger">
          {problem}
        </p>
      )}
      {existing.length > 0 && (
        <p role="alert" className="rounded-md border border-danger/40 px-3 py-2 text-sm text-danger">
          Conflicts: {existing.map((c) => `${c.keys} (“${label(c.a)}” and “${label(c.b)}”)`).join('; ')}.
        </p>
      )}
      {areas.map((area) => (
        <table key={area} className="w-full text-sm">
          <caption className="pb-1 pt-2 text-left text-xs font-semibold text-ink-3">{area}</caption>
          <tbody>
            {ACTIONS.filter((a) => a.area === area).map((a) => (
              <tr key={a.id} className="border-t border-line">
                <th scope="row" className="py-1.5 text-left font-normal">
                  {a.label}
                </th>
                <td className="w-40 py-1.5">{capturing === a.id ? <span className="text-accent">{strokes.length ? strokes.join(' ') + ' …' : 'Press keys…'}</span> : <Keys keys={bindings[a.id]} />}</td>
                <td className="w-36 whitespace-nowrap py-1.5 text-right">
                  {bindings[a.id] !== defaults[a.id] && (
                    <button className="mr-2 text-xs text-accent hover:underline" onClick={() => commit(a.id, defaults[a.id])}>
                      Reset
                    </button>
                  )}
                  <button className="btn h-7 px-2 text-xs" disabled={a.id === 'escape'} aria-label={`Change the key for ${a.label}`} onClick={(e) => (e.currentTarget.blur(), setProblem(''), setStrokes([]), setCapturing(a.id))}>
                    Change
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ))}
      <button className="btn" disabled={Object.keys(overrides).length === 0} onClick={() => (setProblem(''), void save(defaults), toast('All keys are back to the defaults'))}>
        Reset all keys
      </button>
    </>
  );
}

export function CapForm({ stored, save, label = 'Monthly cap (USD)' }: { stored: number | undefined; save: (usd: number) => void; label?: string }) {
  const [cap, setCap] = useState(String(stored ?? ''));
  return (
    <form className="flex items-end gap-2" onSubmit={(e) => (e.preventDefault(), Number(cap) >= 0 && save(Number(cap)))}>
      <label className="grid gap-1 text-sm font-medium">
        {label}
        <input type="number" min={0} step={0.5} className="field w-32" value={cap} onChange={(e) => setCap(e.target.value)} />
      </label>
      <button className="btn" disabled={cap === '' || Number(cap) === stored}>
        Save cap
      </button>
    </form>
  );
}

export function SettingsPage() {
  const client = useQueryClient();
  const caps = useCapabilities();
  const settings = useQuery(settingsQuery);
  const s = settings.data ?? {};
  const me = useQuery(meQuery).data;
  // The admin tab exists only for an admin; in the demo nobody is one.
  const tabs = [{ id: 'general', label: 'General' }, { id: 'account', label: 'Account' }, { id: 'data', label: 'Import and export' }, ...(isAdmin(me) ? [{ id: 'admin', label: 'Admin' }] : [])];
  const asked = (useSearch({ strict: false }) as { tab?: string }).tab;
  const tab = tabs.some((t) => t.id === asked) ? asked! : 'general';

  async function patch(body: Record<string, unknown>) {
    try {
      client.setQueryData(['settings'], await api('/api/settings', { method: 'PATCH', body }));
      toast('Saved');
    } catch (error) {
      toastError(error);
    }
  }
  const general = settings.isPending ? (
    <div className="skeleton h-64 max-w-2xl" aria-busy="true" />
  ) : settings.error ? (
    <ErrorNote error={settings.error} retry={() => void settings.refetch()} />
  ) : (
    <>
          <Card title="Stored media">
            <Toggle label="Store images" help="Keeps photos in Kosha so they still load if the source removes them." checked={!!s.store_images} onChange={(v) => void patch({ store_images: v })} />
            <Toggle label="Store videos" help="Keeps video and audio files too. They take far more space; previews are always stored." checked={!!s.store_videos} onChange={(v) => void patch({ store_videos: v })} />
          </Card>
          <Card title="X API sync">
            <Toggle label="Sync new bookmarks from the X API" help="A paid daily fetch, on top of what the extension captures." checked={!!s.x_api_sync_enabled} onChange={(v) => void patch({ x_api_sync_enabled: v })} />
            {/* Keyed by the stored value, so the field shows what the server holds after a save. */}
            <CapForm key={String(s.x_api_monthly_cap_usd)} stored={s.x_api_monthly_cap_usd as number | undefined} save={(usd) => void patch({ x_api_monthly_cap_usd: usd })} />
            <div className="flex items-center gap-3">
              <button className="btn" disabled={!caps.sync} onClick={() => void startSync()}>
                Sync now
              </button>
              {!caps.sync && <span className="text-sm text-ink-3">Starting a sync is not available on this server yet.</span>}
            </div>
            {caps.sync && <SyncFacts />}
          </Card>
          <Card title="Spending">
            <p className="text-sm text-ink-2">What paid work cost: X API sync, Jev checks, Ask, embeddings and media text.</p>
            {caps.spend ? <Spend /> : <Unavailable what="The spending report" />}
          </Card>
          <Card title="Media text">
            <p className="text-sm text-ink-2">
              Transcripts for video and audio, and descriptions for images. In the library, press <kbd className="kbd">m</kbd> on an item, or select items and choose Media text: that runs for the selection only. You see the count, the minutes and the cost before anything runs.
            </p>
            {!caps.mediaText && <Unavailable what="Media text" />}
            {/* Shown when the server has the setting. */}
            {'media_text_auto' in s && <Toggle label="Get media text for new items automatically" help="Runs as items arrive, within the monthly cap. Off: you choose the items and see the cost first." checked={!!s.media_text_auto} onChange={(v) => void patch({ media_text_auto: v })} />}
            {caps.mediaStatus && <MediaTextStatusFacts />}
            {/* The whole library is the expensive case, so it is the quiet button and only opens the estimate. */}
            <button className="btn btn-ghost px-2 text-sm text-ink-2" disabled={!caps.mediaText} onClick={() => openOverlay({ kind: 'mediaText', ids: 'all' })}>
              Estimate the whole library instead…
            </button>
          </Card>
          <Card title="API tokens">
            <Tokens />
          </Card>
          <Card title="Browser extension">
            <Extension />
          </Card>
          <Card title="Keyboard">
            <Keybindings />
          </Card>
    </>
  );
  return (
    <div className="px-3 py-4 sm:px-5">
      <PageHeader title="Settings">
        <Link to="/onboarding" className="btn">
          Setup guide
        </Link>
      </PageHeader>
      <div role="tablist" aria-label="Settings sections" className="mb-4 flex max-w-2xl gap-1 overflow-x-auto border-b border-line">
        {tabs.map((t) => (
          <Link key={t.id} to="/settings" search={{ tab: t.id === 'general' ? undefined : t.id }} role="tab" aria-selected={t.id === tab} className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm ${t.id === tab ? 'border-accent font-semibold text-ink' : 'border-transparent text-ink-2 hover:text-ink'}`}>
            {t.label}
          </Link>
        ))}
        {me?.demo && (
          <span role="tab" aria-disabled="true" aria-selected={false} title={DEMO_UNAVAILABLE} className="cursor-not-allowed whitespace-nowrap px-3 py-2 text-sm text-ink-3 opacity-60">
            Admin
          </span>
        )}
      </div>
      <div role="tabpanel" className={`space-y-4 ${tab === 'admin' ? 'max-w-4xl' : 'max-w-2xl'}`}>
        {tab === 'general' && general}
        {tab === 'account' && (
          <Card title="Account">
            <Account />
          </Card>
        )}
        {tab === 'data' && (
          <>
            <Card title="Import">
              <Import />
            </Card>
            <Card title="Export">
              <Export />
            </Card>
          </>
        )}
        {tab === 'admin' && (
          <>
            <Card title="Users">
              <Users />
            </Card>
            <Card title="Instance">
              <Instance />
            </Card>
            <Card title="API keys">
              <p className="text-sm text-ink-2">Keys for the optional AI features, for everyone on this server. Kosha works without any of them: keyword search and tagging by hand.</p>
              <ApiKeys />
            </Card>
            <Card title="Storage">
              <Storage />
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
