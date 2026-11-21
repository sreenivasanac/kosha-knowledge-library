/** Small decisions behind edits, kept free of React and the network so they can be tested. */

type Role = 'primary' | 'secondary' | 'other';

/**
 * The role a `1` / `2` press gives a tag across the targeted items. Pressing the key again turns the
 * role back to "other", but only when every target already has it: a mixed selection is first made uniform.
 * `roles` are the tag's roles on the targets that carry it; `targets` is how many items are targeted.
 */
export const nextRole = (roles: Role[], targets: number, wanted: 'primary' | 'secondary'): Role => (roles.length >= targets && roles.every((r) => r === wanted) ? 'other' : wanted);

/**
 * Saves a text field on blur. Each save is compared with the last value sent (not with what the server
 * last returned), and saves go out one after another, so an edit made while an earlier save is still
 * on its way is neither skipped nor overtaken.
 */
export function serialSaver(initial: string, send: (value: string) => Promise<unknown>) {
  let last = initial;
  let queue: Promise<unknown> = Promise.resolve();
  return (value: string): Promise<unknown> => {
    if (value === last) return queue;
    last = value;
    // A failed save is the caller's to report (in `send`); the next one still runs.
    return (queue = queue.then(() => send(value)).catch(() => undefined));
  };
}

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"' };
/** Turns the HTML entities of a server-escaped search snippet back into characters. A number that is not a character is left as written. */
export const decodeEntities = (text: string) =>
  text.replace(/&(?:#x([0-9a-f]+)|#(\d+)|(amp|lt|gt|quot));/gi, (whole, hex, dec, name) => {
    if (name) return ENTITIES[name.toLowerCase()];
    const code = hex ? parseInt(hex, 16) : Number(dec);
    return code <= 0x10ffff ? String.fromCodePoint(code) : whole;
  });
