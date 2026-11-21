/** Small shared pieces: key hints, the modal shell, inline notices. */
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, type ReactNode } from 'react';
import { keybindingsQuery } from './api';
import { formatKeys, resolveBindings, type Bindings } from './keymap';
import { closeOverlay } from './store';

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
  return <p className="rounded-md border border-dashed border-line px-3 py-2 text-sm text-ink-2">{what} is not available on this server yet.</p>;
}

export function ErrorNote({ error, retry }: { error: unknown; retry?: () => void }) {
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
