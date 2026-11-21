/** Choices kept in this browser: the theme, and the view and page size a library view starts with. */
import { DEFAULT_PAGE_SIZE, PAGE_SIZES, STORAGE_KEYS, VIEWS, type View } from './constants';

export const THEMES = ['system', 'light', 'dark'] as const;
export type Theme = (typeof THEMES)[number];

const read = (key: string) => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // no storage (private mode, tests): the defaults apply
  }
};
const write = (key: string, value: string) => {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* the choice lasts until the tab closes */
  }
};

export const getTheme = (): Theme => THEMES.find((t) => t === read(STORAGE_KEYS.theme)) ?? 'system';
/** `system` removes the attribute, so the CSS media query decides. */
export function applyTheme(theme: Theme = getTheme()) {
  if (theme === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}
export function setTheme(theme: Theme) {
  write(STORAGE_KEYS.theme, theme);
  applyTheme(theme);
}

export const preferredView = (): View => VIEWS.find((v) => v === read(STORAGE_KEYS.view)) ?? 'list';
export const setPreferredView = (view: View) => write(STORAGE_KEYS.view, view);
export const preferredPageSize = (): number => PAGE_SIZES.find((n) => n === Number(read(STORAGE_KEYS.pageSize))) ?? DEFAULT_PAGE_SIZE;
export const setPreferredPageSize = (size: number) => write(STORAGE_KEYS.pageSize, String(size));
