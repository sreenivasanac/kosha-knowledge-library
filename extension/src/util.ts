import type { Range } from './config.ts';

export type Rng = () => number;

/** A random number in [min, max]. */
export function between([min, max]: Range, rng: Rng = Math.random): number {
  return min + rng() * (max - min);
}

/** A random whole number in [min, max], both ends included. */
export function intBetween([min, max]: Range, rng: Rng = Math.random): number {
  return Math.min(max, Math.floor(min + rng() * (max - min + 1)));
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** "http://localhost:8000/" -> "http://localhost:8000"; null when it is not an http(s) URL. */
export function originOf(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.origin : null;
  } catch {
    return null;
  }
}
