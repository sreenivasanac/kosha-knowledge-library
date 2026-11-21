// The two settings (server URL, API token) and the authenticated call to Kosha.

import { DEFAULT_SERVER_URL, STORAGE } from './config.ts';

export interface Settings {
  serverUrl: string;
  token: string;
}

export async function getSettings(): Promise<Settings> {
  const stored = (await chrome.storage.local.get(STORAGE.settings))[STORAGE.settings] as Partial<Settings> | undefined;
  return { serverUrl: stored?.serverUrl || DEFAULT_SERVER_URL, token: stored?.token ?? '' };
}

export function saveSettings(settings: Settings): Promise<void> {
  return chrome.storage.local.set({ [STORAGE.settings]: settings });
}

/** fetch against the Kosha server with the bearer token. */
export async function apiFetch(path: string, init: { method?: string; body?: unknown } = {}): Promise<Response> {
  const { serverUrl, token } = await getSettings();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init.body !== undefined) headers['Content-Type'] = 'application/json';
  return fetch(serverUrl.replace(/\/+$/, '') + path, {
    method: init.method ?? 'GET',
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
}
