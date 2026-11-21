import { describe, expect, it } from 'vitest';
import { ACTIONS, createMatcher, defaultBindings, findConflicts, formatKeys, resolveBindings, strokeOf } from './keymap';

describe('keymap registry', () => {
  it('has unique action ids and no conflicts in the defaults', () => {
    expect(new Set(ACTIONS.map((a) => a.id)).size).toBe(ACTIONS.length);
    expect(findConflicts(defaultBindings())).toEqual([]);
  });

  it('turns key events into strokes', () => {
    expect(strokeOf({ key: 'j' })).toBe('j');
    expect(strokeOf({ key: 'J', shiftKey: true })).toBe('Shift+j');
    expect(strokeOf({ key: 'k', metaKey: true })).toBe('Mod+k');
    expect(strokeOf({ key: 'a', ctrlKey: true })).toBe('Mod+a');
    // `?` already implies Shift; keeping it would tie the binding to a keyboard layout.
    expect(strokeOf({ key: '?', shiftKey: true })).toBe('?');
    expect(strokeOf({ key: 'Shift', shiftKey: true })).toBeNull();
  });

  it('matches single keys, alternatives and two-key sequences', () => {
    const m = createMatcher(defaultBindings());
    expect(m.feed('j')).toEqual({ type: 'action', id: 'move.next' });
    expect(m.feed('o')).toEqual({ type: 'action', id: 'item.open' });
    expect(m.feed('Enter')).toEqual({ type: 'action', id: 'item.open' });
    expect(m.feed('g')).toEqual({ type: 'pending' });
    expect(m.strokes()).toEqual(['g']);
    expect(m.feed('a')).toEqual({ type: 'action', id: 'go.all' });
    expect(m.feed('g')).toEqual({ type: 'pending' });
    expect(m.feed('g')).toEqual({ type: 'action', id: 'move.first' });
    expect(m.feed('v')).toEqual({ type: 'pending' });
    expect(m.feed('g')).toEqual({ type: 'action', id: 'view.grid' });
  });

  it('starts over when a key does not continue the sequence', () => {
    const m = createMatcher(defaultBindings());
    m.feed('g');
    expect(m.feed('j')).toEqual({ type: 'action', id: 'move.next' });
    m.feed('g');
    expect(m.feed('9')).toEqual({ type: 'none' });
    expect(m.strokes()).toEqual([]);
  });

  it('only matches allowed actions (typing in a field)', () => {
    const m = createMatcher(defaultBindings());
    const whileTyping = (id: string) => id === 'palette.open' || id === 'escape';
    expect(m.feed('j', whileTyping)).toEqual({ type: 'none' });
    expect(m.feed('g', whileTyping)).toEqual({ type: 'none' });
    expect(m.feed('Mod+k', whileTyping)).toEqual({ type: 'action', id: 'palette.open' });
  });

  it('applies remaps over the defaults and ignores unknown or empty ones', () => {
    const bindings = resolveBindings({ 'item.star': 'f s', nope: 'q', 'item.read': ' ' });
    expect(bindings['item.star']).toBe('f s');
    expect(bindings['item.read']).toBe('Shift+r');
    expect('nope' in bindings).toBe(false);
    const m = createMatcher(resolveBindings({ 'item.star': 'b' }));
    expect(m.feed('b')).toEqual({ type: 'action', id: 'item.star' });
    expect(m.feed('s')).toEqual({ type: 'none' });
  });

  it('detects the same key twice and a key that hides a sequence', () => {
    const same = findConflicts(resolveBindings({ 'item.star': 'd' }));
    expect(same).toHaveLength(1);
    expect([same[0].a, same[0].b].sort()).toEqual(['item.archive', 'item.star']);
    // A bare `g` would fire before `g a` could be typed.
    const prefix = findConflicts(resolveBindings({ 'item.star': 'g' }));
    expect(prefix.length).toBeGreaterThan(1);
    expect(prefix.every((c) => c.keys === 'g')).toBe(true);
    // One alternative of a binding is enough to conflict.
    expect(findConflicts(resolveBindings({ 'item.star': 'o' }))).toHaveLength(1);
  });

  it('formats keys for people', () => {
    expect(formatKeys('Mod+k', true)).toBe('⌘K');
    expect(formatKeys('Mod+k', false)).toBe('Ctrl+K');
    expect(formatKeys('Shift+j')).toBe('Shift+J');
    expect(formatKeys('g a')).toBe('g a');
    expect(formatKeys('Enter | o')).toBe('Enter or o');
    expect(formatKeys('Escape')).toBe('Esc');
  });
});
