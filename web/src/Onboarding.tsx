/**
 * The setup guide: four steps after the account, each skippable. It opens once
 * for a new account and again from Settings. Every choice is saved as it is
 * made, so "Continue" only records that the step was seen.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { Check } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiKeys, Import, Storage } from './AdminSettings';
import { api, isAdmin, meQuery, setMe, settingsQuery, useIsDemo } from './api';
import { APP_NAME, LINKS, PAGE_SIZES, VIEWS, type View } from './constants';
import { ACTIONS } from './keymap';
import { freshStatus, nextStep, previousStep, resumeStep, STEP_TITLES, visibleSteps, withStep, type OnboardingStatus, type Step, type StepState } from './setupSteps';
import { Toasts } from './Overlays';
import { getTheme, preferredPageSize, preferredView, setPreferredPageSize, setPreferredView, setTheme, THEMES, type Theme } from './prefs';
import { CapForm, Extension } from './SettingsPage';
import { toast, toastError } from './store';
import { CopyRow, ErrorNote, Keys, Logo, Toggle, Unavailable, useAction } from './ui';

/** A server without the endpoint keeps no progress; the guide then starts from the top each time. */
const onboardingQuery = { queryKey: ['onboarding'], queryFn: () => api<OnboardingStatus>('/api/onboarding').catch(() => freshStatus()), staleTime: 0 };

function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2 border-t border-line pt-4 first:border-0 first:pt-0">
      <h3 className="font-semibold">{title}</h3>
      {children}
    </section>
  );
}

/** A row of exclusive choices; the current one is marked, so a default reads as already chosen. */
function Choice<T extends string | number>({ label, options, value, onChange }: { label: string; options: readonly T[]; value: T; onChange: (v: T) => void }) {
  return (
    <fieldset>
      <legend className="mb-1 text-sm font-medium">{label}</legend>
      <div className="inline-flex rounded-md border border-line bg-surface p-0.5">
        {options.map((o) => (
          <label key={o} className="cursor-pointer rounded px-3 py-1 text-sm capitalize has-checked:bg-accent-soft has-checked:font-semibold has-checked:text-accent has-focus-visible:outline-2 has-focus-visible:outline-accent">
            <input type="radio" className="sr-only" name={label} checked={o === value} onChange={() => onChange(o)} />
            {o}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

// ---------------------------------------------------------------- Step: sources

function NewToken() {
  const demo = useIsDemo();
  const [token, setToken] = useState<string | null>(null);
  const create = useAction();
  if (demo) return <Unavailable what="Creating an API token" />;
  return token ? (
    <>
      <CopyRow value={token} what="the token" />
      <p className="text-xs text-ink-3">Copy it now; it is not shown again. You can revoke it in Settings.</p>
    </>
  ) : (
    <>
      {/* Made on request, not on arrival: every visit to this step would otherwise leave an unused token behind. */}
      <button className="btn" disabled={create.busy} onClick={async () => setToken((await create.run(() => api<{ token?: string }>('/api/tokens', { method: 'POST', body: { name: 'Browser extension' } })))?.token ?? null)}>
        Create a token for the extension
      </button>
      {create.error != null && <ErrorNote error={create.error} what="Creating an API token" />}
    </>
  );
}

function SourcesStep() {
  return (
    <>
      <Part title="1. Install the browser extension">
        <p className="text-sm text-ink-2">
          Kosha Capture copies your X bookmarks and LinkedIn saved posts while you look at them in your own browser. It never sees your passwords.{' '}
          <a className="text-accent hover:underline" href={LINKS.extension} target="_blank" rel="noreferrer">
            How to install it
          </a>
        </p>
      </Part>
      <Part title="2. Point it at this server">
        <p className="text-sm text-ink-2">Open the extension’s popup and paste these two values.</p>
        <p className="text-sm font-medium">Server address</p>
        <CopyRow value={window.location.origin} what="the server address" />
        <p className="text-sm font-medium">API token</p>
        <NewToken />
      </Part>
      <Part title="3. Check the connection">
        <Extension />
      </Part>
      <Part title="4. Bring in what you already saved">
        <p className="text-sm text-ink-2">
          <span className="font-medium text-ink">Backfill</span>, in the extension’s popup, scrolls your whole bookmarks page for you, slowly. It is automated browsing of your account, so X or LinkedIn may rate-limit or restrict it, and it may be against their terms.
        </p>
        <p className="pt-1 text-sm font-medium">Or import a file</p>
        <Import />
      </Part>
    </>
  );
}

// ---------------------------------------------------------------- Step: AI keys

function useSettings() {
  const client = useQueryClient();
  const settings = useQuery(settingsQuery);
  async function patch(body: Record<string, unknown>) {
    try {
      client.setQueryData(['settings'], await api('/api/settings', { method: 'PATCH', body }));
      toast('Saved');
    } catch (error) {
      toastError(error);
    }
  }
  return { s: settings.data ?? {}, ready: !!settings.data, patch };
}

function AiStep() {
  const { s, ready, patch } = useSettings();
  return (
    <>
      <p className="rounded-md bg-accent-soft px-3 py-2 text-sm">
        <span className="font-semibold">All of these are optional.</span> Without any key, {APP_NAME} still captures, searches by keyword and lets you tag by hand.
      </p>
      <Part title="Keys">
        <ApiKeys />
      </Part>
      <Part title="Monthly spending caps">
        <p className="text-sm text-ink-2">Paid calls stop for the month when a cap is reached. These are the defaults; change them if you like.</p>
        {ready && (
          <div className="space-y-3">
            {/* Keyed by the stored value, so the field shows what the server holds after a save. */}
            <CapForm key={`jev${s.jev_monthly_budget_usd}`} label="Jev checks (USD a month)" stored={s.jev_monthly_budget_usd as number | undefined} save={(usd) => void patch({ jev_monthly_budget_usd: usd })} />
            <CapForm key={`voyage${s.voyage_monthly_cap_usd}`} label="Search by meaning (USD a month)" stored={s.voyage_monthly_cap_usd as number | undefined} save={(usd) => void patch({ voyage_monthly_cap_usd: usd })} />
            <CapForm key={`llm${s.llm_monthly_cap_usd}`} label="LLM and transcripts (USD a month)" stored={s.llm_monthly_cap_usd as number | undefined} save={(usd) => void patch({ llm_monthly_cap_usd: usd })} />
          </div>
        )}
      </Part>
    </>
  );
}

// ---------------------------------------------------------------- Step: media and storage

/** The setting that makes the server fetch media text for new items by itself. */
const MEDIA_TEXT_AUTO = 'media_text_auto';

function MediaStep({ admin }: { admin: boolean }) {
  const { s, ready, patch } = useSettings();
  const auto = !!s[MEDIA_TEXT_AUTO];
  const canAuto = MEDIA_TEXT_AUTO in s;
  return (
    <>
      <Part title="What to keep a copy of">
        {ready ? (
          <>
            <Toggle label="Store images" help="Keeps photos in Kosha so they still load if the source removes them." checked={!!s.store_images} onChange={(v) => void patch({ store_images: v })} />
            <Toggle label="Store videos" help="Keeps video and audio files too. They take far more space; previews are always stored." checked={!!s.store_videos} onChange={(v) => void patch({ store_videos: v })} />
          </>
        ) : (
          <div className="skeleton h-16" aria-busy="true" />
        )}
      </Part>
      <Part title="Text from media">
        <p className="text-sm text-ink-2">Transcripts of video and audio and descriptions of images make them searchable. Each one is a paid call to the AI keys from the previous step.</p>
        <div className="space-y-1.5">
          <label className="flex items-start gap-2 text-sm">
            <input type="radio" name="media-text" className="mt-1 accent-(--accent)" checked={!auto} onChange={() => canAuto && void patch({ [MEDIA_TEXT_AUTO]: false })} />
            <span>
              On demand
              <span className="block text-xs text-ink-3">You choose the items and see the cost before anything runs.</span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input type="radio" name="media-text" className="mt-1 accent-(--accent)" checked={auto} disabled={!canAuto} onChange={() => void patch({ [MEDIA_TEXT_AUTO]: true })} />
            <span className={canAuto ? '' : 'text-ink-3'}>
              Automatically for new items
              <span className="block text-xs text-ink-3">{canAuto ? 'Runs as items arrive, within the monthly cap.' : 'Not available on this server yet.'}</span>
            </span>
          </label>
        </div>
      </Part>
      {admin && (
        <Part title="Storage">
          <Storage withMigrate={false} />
        </Part>
      )}
    </>
  );
}

// ---------------------------------------------------------------- Step: look and keyboard

/** The handful of keys worth knowing on day one, by their ids in the keymap registry. */
const TOUR_KEYS = ['move.next', 'move.prev', 'item.tag', 'select.toggle', 'item.archive', 'search.focus', 'palette.open', 'help'];

export function KeyTour({ ids = TOUR_KEYS }: { ids?: string[] }) {
  return (
    <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
      {ids.map((id) => {
        const action = ACTIONS.find((a) => a.id === id)!;
        return (
          <div key={id} className="flex items-center justify-between gap-3 border-b border-line py-1">
            <dt>{action.label}</dt>
            <dd>
              <Keys keys={action.keys} />
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function LookStep() {
  // Kept in this browser (prefs.ts); local state only redraws the chosen option.
  const [theme, setThemeState] = useState<Theme>(getTheme);
  const [view, setViewState] = useState<View>(preferredView);
  const [size, setSizeState] = useState(preferredPageSize);
  return (
    <>
      <Part title="Look">
        <div className="flex flex-wrap gap-x-8 gap-y-3">
          <Choice label="Theme" options={THEMES} value={theme} onChange={(t) => (setTheme(t), setThemeState(t))} />
          <Choice label="Library view" options={VIEWS} value={view} onChange={(v) => (setPreferredView(v), setViewState(v))} />
          <Choice label="Items per page" options={PAGE_SIZES} value={size as (typeof PAGE_SIZES)[number]} onChange={(n) => (setPreferredPageSize(n), setSizeState(n))} />
        </div>
      </Part>
      <Part title="The keys to know">
        <p className="text-sm text-ink-2">Everything in {APP_NAME} has a key. These eight cover most of a session; the last one shows all the others.</p>
        <KeyTour />
      </Part>
    </>
  );
}

// ---------------------------------------------------------------- The guide

const INTRO: Record<Step, string> = {
  sources: 'Get your saved posts into this library.',
  ai: 'Turn on the features that need an AI provider, and cap what they may spend.',
  media: 'Choose what Kosha keeps a copy of, and where.',
  look: 'Set how the library looks, and learn the keys that move you through it.',
};

export function Onboarding() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const me = useQuery(meQuery).data;
  const admin = isAdmin(me);
  const status = useQuery(onboardingQuery).data;
  const [chosen, setChosen] = useState<Step | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const step = chosen ?? (status ? resumeStep(status, admin) : null);

  // Each step starts at its heading, for the keyboard and for screen readers.
  useEffect(() => {
    heading.current?.focus();
    window.scrollTo(0, 0);
  }, [step]);

  async function finish() {
    // Best effort: a server without the endpoint has nothing to record.
    await api('/api/onboarding/complete', { method: 'POST' }).catch(() => undefined);
    if (me) setMe({ ...me, onboarding_completed: true });
    void navigate({ to: '/' });
  }

  if (!status || !step)
    return (
      <main className="grid min-h-dvh place-items-center">
        <div className="skeleton h-40 w-80" aria-busy="true" />
      </main>
    );

  const steps = visibleSteps(status, admin);
  const index = steps.indexOf(step);
  const back = previousStep(status, admin, step);

  function answer(asked: StepState) {
    // Skipping past a step that was already done leaves it done.
    const state = asked === 'skipped' && status!.steps[step!] === 'done' ? 'done' : asked;
    const updated = withStep(status!, step!, state);
    client.setQueryData(['onboarding'], updated);
    void api('/api/onboarding', { method: 'PATCH', body: { step, state } }).catch(() => undefined);
    const next = nextStep(updated, admin, step!);
    if (next) setChosen(next);
    else void finish();
  }

  return (
    <div className="mx-auto flex min-h-dvh max-w-3xl flex-col px-4 pb-6">
      <header className="flex items-center gap-2 py-4">
        <Logo />
        <span className="flex-1 text-lg font-semibold tracking-tight">{APP_NAME} setup</span>
        <button className="btn btn-ghost px-2" onClick={() => void finish()}>
          Skip setup
        </button>
      </header>
      {/* A real sequence, so the steps are numbered. Any step can be opened directly. */}
      <nav aria-label="Setup steps">
        <ol className="flex gap-1 overflow-x-auto pb-1 text-sm">
          {steps.map((s, i) => {
            const state = status.steps[s] ?? 'todo';
            return (
              <li key={s} className="min-w-0 flex-1">
                <button aria-current={s === step ? 'step' : undefined} onClick={() => setChosen(s)} className={`flex w-full items-center gap-2 border-t-2 px-1 py-2 text-left ${s === step ? 'border-accent font-semibold text-ink' : 'border-line text-ink-2 hover:text-ink'}`}>
                  <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-full text-xs ${state === 'done' ? 'bg-ok text-surface' : s === step ? 'bg-accent text-surface' : 'bg-sunken text-ink-2'}`}>{state === 'done' ? <Check size={12} aria-label="Done" /> : i + 1}</span>
                  <span className="truncate max-sm:hidden">{STEP_TITLES[s]}</span>
                </button>
              </li>
            );
          })}
        </ol>
      </nav>
      <main className="flex-1 py-5">
        <h1 ref={heading} tabIndex={-1} style={{ outline: 'none' }} className="text-2xl font-semibold tracking-tight">
          {STEP_TITLES[step]}
        </h1>
        <p className="mt-1 text-ink-2">{INTRO[step]}</p>
        <div className="mt-5 space-y-4 rounded-lg border border-line bg-surface p-4 sm:p-5">
          {step === 'sources' && <SourcesStep />}
          {step === 'ai' && <AiStep />}
          {step === 'media' && <MediaStep admin={admin} />}
          {step === 'look' && <LookStep />}
        </div>
      </main>
      <footer className="sticky bottom-0 flex items-center gap-2 border-t border-line bg-bg py-3">
        {back && (
          <button className="btn" onClick={() => setChosen(back)}>
            Back
          </button>
        )}
        <span className="flex-1 text-sm text-ink-3">
          Step {index + 1} of {steps.length}
        </span>
        <button className="btn btn-ghost" onClick={() => answer('skipped')}>
          Skip this step
        </button>
        <button className="btn btn-primary" onClick={() => answer('done')}>
          {index === steps.length - 1 ? 'Finish' : 'Continue'}
        </button>
      </footer>
      <Toasts />
    </div>
  );
}
