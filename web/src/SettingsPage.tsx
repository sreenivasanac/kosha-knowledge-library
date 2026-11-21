/** Settings: storage, X API sync and spend, media text, API tokens, the extension, and key remapping. */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type ReactNode } from 'react';
import { askExtension, getExtensionId } from './ArchiveTools';
import { api, keybindingsQuery, settingsQuery, statsQuery, useCapabilities } from './api';
import { SEQUENCE_TIMEOUT_MS, STORAGE_KEYS } from './constants';
import { usd } from './format';
import { ACTIONS, defaultBindings, findConflicts, resolveBindings, strokeOf, type Area } from './keymap';
import { openOverlay, toast, toastError } from './store';
import { PageHeader } from './TagsPage';
import { ErrorNote, Keys, Unavailable } from './ui';

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 rounded-lg border border-line bg-surface p-4">
      <h2 className="font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Toggle({ label, help, checked, onChange }: { label: string; help: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-start gap-3">
      <input type="checkbox" role="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-1 h-4 w-4 accent-(--accent)" />
      <span>
        <span className="block text-sm font-medium">{label}</span>
        <span className="block text-sm text-ink-2">{help}</span>
      </span>
    </label>
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
      {tokens.error && <ErrorNote error={tokens.error} />}
      <ul className="divide-y divide-line text-sm">
        {tokens.data?.map((t) => (
          <li key={t.id} className="flex items-center justify-between gap-3 py-1.5">
            <span>{t.name}</span>
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
        <p role="status" className="rounded-md bg-sunken px-3 py-2 text-sm">
          Copy it now; it is not shown again: <code className="break-all">{created}</code>
        </p>
      )}
    </>
  );
}

function Extension() {
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
      <p className="text-sm text-ink-2">The Archive page asks the extension to remove items at their sources. Its id is on the browser’s extensions page.</p>
      <div className="flex gap-2">
        <input aria-label="Extension id" placeholder="Extension id" spellCheck={false} className="field flex-1" value={id} onChange={(e) => setId(e.target.value)} />
        <button className="btn">Save and test</button>
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

export function SettingsPage() {
  const client = useQueryClient();
  const caps = useCapabilities();
  const settings = useQuery(settingsQuery);
  const stats = useQuery(statsQuery).data ?? {};
  const s = settings.data ?? {};
  const [cap, setCap] = useState('');
  useEffect(() => setCap(String(s.x_api_monthly_cap_usd ?? '')), [s.x_api_monthly_cap_usd]);

  async function patch(body: Record<string, unknown>) {
    try {
      client.setQueryData(['settings'], await api('/api/settings', { method: 'PATCH', body }));
      toast('Saved');
    } catch (error) {
      toastError(error);
    }
  }
  // Reported by the server once it tracks spend; the field name is tried in both places.
  const spend = (stats.spend_month_usd ?? s.spend_month_usd ?? stats.x_api_spend_month_usd) as number | undefined;

  return (
    <div className="px-3 py-4 sm:px-5">
      <PageHeader title="Settings" />
      {settings.isPending ? (
        <div className="skeleton h-64 max-w-2xl" aria-busy="true" />
      ) : settings.error ? (
        <ErrorNote error={settings.error} retry={() => void settings.refetch()} />
      ) : (
        <div className="max-w-2xl space-y-4">
          <Card title="Stored media">
            <Toggle label="Store images" help="Keeps photos in Kosha so they still load if the source removes them." checked={!!s.store_images} onChange={(v) => void patch({ store_images: v })} />
            <Toggle label="Store videos" help="Keeps video and audio files too. They take far more space; previews are always stored." checked={!!s.store_videos} onChange={(v) => void patch({ store_videos: v })} />
          </Card>
          <Card title="X API sync">
            <Toggle label="Sync new bookmarks from the X API" help="A paid daily fetch, on top of what the extension captures." checked={!!s.x_api_sync_enabled} onChange={(v) => void patch({ x_api_sync_enabled: v })} />
            <form className="flex items-end gap-2" onSubmit={(e) => (e.preventDefault(), Number(cap) >= 0 && void patch({ x_api_monthly_cap_usd: Number(cap) }))}>
              <label className="grid gap-1 text-sm font-medium">
                Monthly cap (USD)
                <input type="number" min={0} step={0.5} className="field w-32" value={cap} onChange={(e) => setCap(e.target.value)} />
              </label>
              <button className="btn" disabled={cap === '' || Number(cap) === s.x_api_monthly_cap_usd}>
                Save cap
              </button>
            </form>
            <p className="text-sm text-ink-2">Spent this month: {spend == null ? 'not reported by this server yet' : <strong className="text-ink">{usd(spend)}</strong>}</p>
          </Card>
          <Card title="Media text">
            <p className="text-sm text-ink-2">Transcripts for video and audio, and descriptions for images. You see the count, the minutes and the cost before anything runs.</p>
            <button className="btn" disabled={!caps.mediaText} onClick={() => openOverlay({ kind: 'mediaText', ids: 'all' })}>
              Get text for all media without it
            </button>
            {!caps.mediaText && <Unavailable what="Media text" />}
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
        </div>
      )}
    </div>
  );
}
