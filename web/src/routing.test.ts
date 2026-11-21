import { describe, expect, it } from 'vitest';
import { canSignUp, publicPageRedirect, resolveEntry, safeNext, tokenPage, type EntryState } from './routing';

const base: EntryState = { hasUsers: true, landing: true, signedIn: false, onboardingDone: true };

describe('resolveEntry', () => {
  it('sends everyone to setup while the server has no accounts', () => {
    expect(resolveEntry({ ...base, hasUsers: false }, '/')).toEqual({ page: 'setup' });
    expect(resolveEntry({ ...base, hasUsers: false, signedIn: true }, '/tags')).toEqual({ page: 'setup' });
  });
  it('shows a visitor the landing page at / when it is on', () => {
    expect(resolveEntry(base, '/')).toEqual({ page: 'landing' });
  });
  it('shows a visitor the sign-in page at / when the landing page is off', () => {
    expect(resolveEntry({ ...base, landing: false }, '/')).toEqual({ page: 'login' });
  });
  it('keeps a deep link through sign-in, landing page or not', () => {
    expect(resolveEntry(base, '/?q=from%3Aa&item=12')).toEqual({ page: 'login', next: '/?q=from%3Aa&item=12' });
    expect(resolveEntry({ ...base, landing: false }, '/settings?tab=admin')).toEqual({ page: 'login', next: '/settings?tab=admin' });
  });
  it('opens the app for someone signed in, and the setup guide the first time', () => {
    expect(resolveEntry({ ...base, signedIn: true }, '/')).toEqual({ page: 'app' });
    expect(resolveEntry({ ...base, signedIn: true, onboardingDone: false }, '/')).toEqual({ page: 'onboarding' });
  });
});

describe('safeNext', () => {
  it('accepts paths inside the app', () => {
    expect(safeNext('/tags')).toBe('/tags');
    expect(safeNext('/?q=is%3Aunread')).toBe('/?q=is%3Aunread');
  });
  it('refuses other sites and the sign-in pages themselves', () => {
    for (const bad of ['https://evil.example', '//evil.example', '/\\evil.example', 'tags', undefined, 12, '/login', '/login?next=/x', '/setup']) expect(safeNext(bad)).toBe('/');
  });
});

describe('publicPageRedirect', () => {
  it('offers setup only before the first account', () => {
    expect(publicPageRedirect('setup', { hasUsers: false, signedIn: false })).toBeNull();
    expect(publicPageRedirect('setup', { hasUsers: true, signedIn: false })).toBe('/');
    expect(publicPageRedirect('login', { hasUsers: false, signedIn: false })).toBe('/setup');
    expect(publicPageRedirect('signup', { hasUsers: false, signedIn: false })).toBe('/setup');
  });
  it('sends someone already signed in to the app', () => {
    expect(publicPageRedirect('login', { hasUsers: true, signedIn: true })).toBe('/');
    expect(publicPageRedirect('login', { hasUsers: true, signedIn: false })).toBeNull();
  });
});

describe('tokenPage', () => {
  it('routes an invite or reset token found on any path', () => {
    expect(tokenPage('/', { invite: 'abc' })).toEqual({ to: '/signup', token: 'abc' });
    expect(tokenPage('/login', { reset: 123 })).toEqual({ to: '/reset', token: '123' });
  });
  it('leaves the token pages and ordinary links alone', () => {
    expect(tokenPage('/signup', { invite: 'abc' })).toBeNull();
    expect(tokenPage('/reset', { reset: 'abc' })).toBeNull();
    expect(tokenPage('/', { q: 'x' })).toBeNull();
  });
});

describe('canSignUp', () => {
  it('needs open sign-up or an invite', () => {
    expect(canSignUp('open', undefined)).toBe(true);
    expect(canSignUp('invite', undefined)).toBe(false);
    expect(canSignUp('invite', 'tok')).toBe(true);
    expect(canSignUp('closed', undefined)).toBe(false);
  });
});
