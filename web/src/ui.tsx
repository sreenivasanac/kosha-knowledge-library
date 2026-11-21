/** Small shared pieces: key hints, the modal shell, inline notices. */
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiError, keybindingsQuery, useIsDemo } from './api';
import { PASSWORD_MIN_LENGTH } from './constants';
import { formatKeys, resolveBindings, type Bindings } from './keymap';
import { passwordHint } from './setupSteps';
import { closeOverlay, isDemoRefusal, toast, toastError } from './store';

export const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);

/** The keys in force: defaults with the user's remaps from the server on top. */
export function useBindings(): Bindings {
  const { data } = useQuery(keybindingsQuery);
  // Stable until the remaps change: the key listener keeps its half-typed sequence across renders.
  return useMemo(() => resolveBindings(data ?? {}), [data]);
}

/** `Star (s)`: a tooltip that always names the action's current key. */
export function useHint() {
  const bindings = useBindings();
  return (label: string, actionId: string) => `${label} (${formatKeys(bindings[actionId], IS_MAC)})`;
}

export function Keys({ keys }: { keys: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {formatKeys(keys, IS_MAC)
        .split(' ')
        .map((part, i) =>
          part === 'or' ? (
            <span key={i} className="text-xs text-ink-3">
              or
            </span>
          ) : (
            <kbd key={i} className="kbd">
              {part}
            </kbd>
          ),
        )}
    </span>
  );
}

/**
 * A native modal <dialog>: the browser gives the focus trap, Esc and the
 * backdrop. Closing it always clears the store's overlay.
 */
export function Modal({ label, children, className = '' }: { label: string; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    if (!dialog.open) dialog.showModal();
    // showModal focuses the first control (the Close button); the field or main button is what should have it.
    dialog.querySelector<HTMLElement>('[data-autofocus]')?.focus();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      aria-label={label}
      onCancel={(e) => {
        e.preventDefault();
        closeOverlay();
      }}
      onMouseDown={(e) => e.target === ref.current && closeOverlay()}
      className={`mx-auto mt-[10vh] max-h-[80vh] w-[min(40rem,calc(100vw-1.5rem))] overflow-hidden rounded-xl border border-line bg-surface p-0 text-ink shadow-pop backdrop:bg-black/40 ${className}`}
    >
      {children}
    </dialog>
  );
}

/** Says plainly that a screen needs something this server does not have yet. */
export function Unavailable({ what }: { what: string }) {
  const demo = useIsDemo();
  return <p className="rounded-md border border-dashed border-line px-3 py-2 text-sm text-ink-2">{what} is not available {demo ? 'in the demo' : 'on this server yet'}.</p>;
}

/** An error, with a retry when one is given. With `what`, a 404 is read as "this server does not have the endpoint yet". */
export function ErrorNote({ error, retry, what }: { error: unknown; retry?: () => void; what?: string }) {
  if (what && error instanceof ApiError && (error.status === 404 || error.status === 405 || error.code === 'demo')) return <Unavailable what={what} />;
  return (
    <div role="alert" className="flex items-center gap-3 rounded-md border border-danger/40 px-3 py-2 text-sm text-danger">
      <span>{error instanceof Error ? error.message : String(error)}</span>
      {retry && (
        <button className="btn" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

/** The bookmark-ribbon mark. `size` is the square's side in pixels. */
export function Logo({ size = 24 }: { size?: number }) {
  return (
    <span aria-hidden className="grid shrink-0 place-items-center bg-accent" style={{ width: size, height: size, borderRadius: size * 0.25 }}>
      <span className="bg-mark [clip-path:polygon(0_0,100%_0,100%_100%,50%_72%,0_100%)]" style={{ width: size * 0.34, height: size * 0.58 }} />
    </span>
  );
}

export function Card({ title, children, id }: { title: string; children: ReactNode; id?: string }) {
  return (
    <section id={id} className="space-y-3 rounded-lg border border-line bg-surface p-4">
      <h2 className="font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export function Toggle({ label, help, checked, onChange, disabled }: { label: string; help: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="flex items-start gap-3">
      <input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} className="mt-1 h-4 w-4 accent-(--accent)" />
      <span>
        <span className="block text-sm font-medium">{label}</span>
        <span className="block text-sm text-ink-2">{help}</span>
      </span>
    </label>
  );
}

/** A new-password field with the length rule and a strength hint under it. */
export function NewPasswordField({ label = 'Password', name = 'password', value, onChange }: { label?: string; name?: string; value: string; onChange: (v: string) => void }) {
  const hint = passwordHint(value, PASSWORD_MIN_LENGTH);
  const tone = ['bg-danger', 'bg-danger', 'bg-mark', 'bg-ok'][hint.level];
  return (
    <label className="grid gap-1 text-sm font-medium">
      {label}
      <input name={name} type="password" autoComplete="new-password" required minLength={PASSWORD_MIN_LENGTH} className="field h-10" value={value} onChange={(e) => onChange(e.target.value)} aria-describedby={`${name}-hint`} />
      <span id={`${name}-hint`} className="flex items-center gap-2 text-xs font-normal text-ink-2">
        {/* The text says it; the three bars only show how far along the password is. */}
        <span aria-hidden className="flex gap-0.5">
          {[1, 2, 3].map((n) => (
            <span key={n} className={`h-1 w-5 rounded-full ${value && hint.level >= n ? tone : 'bg-line'}`} />
          ))}
        </span>
        {value ? hint.text : `At least ${PASSWORD_MIN_LENGTH} characters.`}
      </span>
    </label>
  );
}

/**
 * Runs one request for a form: `busy` while it runs, and `error` to show next
 * to the form (ErrorNote turns a missing endpoint into a plain sentence). A
 * refusal from the demo server becomes a toast instead.
 */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function run<T>(work: () => Promise<T>, done?: string): Promise<T | undefined> {
    setBusy(true);
    setError(null);
    try {
      const result = await work();
      if (done) toast(done);
      return result;
    } catch (e) {
      if (isDemoRefusal(e)) toastError(e);
      else setError(e);
    } finally {
      setBusy(false);
    }
  }
  return { run, busy, error, clear: () => setError(null) };
}

export const copyText = (text: string, what: string) => navigator.clipboard.writeText(text).then(() => toast(`Copied ${what}`), () => toast('The browser blocked copying', { error: true }));

/** A value to copy (an address, a token, a link), with its Copy button. `data-secret` marks it for hiding in screenshots. */
export function CopyRow({ value, what }: { value: string; what: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md bg-sunken px-3 py-2 text-sm">
      <code data-secret className="min-w-0 flex-1 break-all">
        {value}
      </code>
      <button type="button" className="btn shrink-0" onClick={() => void copyText(value, what)}>
        Copy
      </button>
    </div>
  );
}
