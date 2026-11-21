/** The one keydown listener: turns key presses into registry actions. */
import { useEffect } from 'react';
import { runAction } from './actions';
import { SEQUENCE_TIMEOUT_MS } from './constants';
import { createMatcher, formatKeys, strokeOf, WORKS_WHILE_TYPING } from './keymap';
import { getState, setState } from './store';
import { IS_MAC, useBindings } from './ui';

export function KeyboardLayer() {
  const bindings = useBindings();
  useEffect(() => {
    const matcher = createMatcher(bindings);
    let timer: ReturnType<typeof setTimeout>;
    const clear = () => {
      matcher.reset();
      if (getState().pendingKeys) setState({ pendingKeys: '' });
    };

    function onKeyDown(e: KeyboardEvent) {
      if (e.isComposing) return;
      const stroke = strokeOf(e);
      if (!stroke) return;
      const target = e.target as HTMLElement;
      const typing = target.matches?.('input, textarea, select, [contenteditable="true"]');
      // A focused button or link keeps its own Enter and Space.
      if (target.matches?.('button, a, summary, [role="button"]') && (stroke === 'Enter' || stroke === 'Space')) return;
      // Dialogs own their keys (the browser closes them on Esc); only the palette toggle passes.
      const allowed = (id: string) => (getState().overlay ? id === 'palette.open' : typing ? WORKS_WHILE_TYPING.has(id) : true);
      const match = matcher.feed(stroke, allowed);
      clearTimeout(timer);
      if (match.type === 'action') {
        e.preventDefault();
        clear();
        runAction(match.id);
      } else if (match.type === 'pending') {
        e.preventDefault();
        setState({ pendingKeys: formatKeys(matcher.strokes().join(' '), IS_MAC) });
        timer = setTimeout(clear, SEQUENCE_TIMEOUT_MS);
      } else {
        clear();
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      clearTimeout(timer);
    };
  }, [bindings]);
  return null;
}
