/** Command palette (Cmd+K): Ask, Go to, Tags, Actions; slash commands when the input starts with "/". */
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { runAction, tagByPath } from './actions';
import { api, flattenTags, getCapabilities, queryClient, tagsQuery, useLists, useSavedViews } from './api';
import { SORTS, VIEWS, type View } from './constants';
import { ACTIONS } from './keymap';
import { getRouter, goQuery, goTo, setSearch } from './nav';
import { quoteValue } from './query';
import { closeOverlay, openOverlay, toast, toastError } from './store';
import { Keys, Modal, useBindings } from './ui';

interface Command {
  section: string;
  label: string;
  detail?: string;
  keys?: string;
  count?: number | null;
  /** Text to put in the input instead of running: a slash command that still needs its argument. */
  complete?: string;
  run: () => void;
}

export async function startSync() {
  if (!getCapabilities().sync) return toast('Sync from the X API is not available on this server yet');
  try {
    await api('/api/sync', { method: 'POST' });
    toast('Sync started');
    void queryClient.invalidateQueries({ queryKey: ['sync'] });
  } catch (error) {
    toastError(error);
  }
}

const SLASH: { name: string; arg?: string; help: string; run: (arg: string) => void }[] = [
  { name: '/tag', arg: 'path', help: 'Add a tag to the focused item or the selection', run: (arg) => (arg ? void tagByPath(arg) : runAction('item.tag')) },
  { name: '/untag', arg: 'path', help: 'Remove a tag', run: (arg) => (arg ? void tagByPath(arg, true) : runAction('item.untag')) },
  { name: '/archive', help: 'Move the focused item or the selection to Archive', run: () => runAction('item.archive') },
  { name: '/view', arg: VIEWS.join(' | '), help: 'Change the layout', run: (arg) => (VIEWS.includes(arg as View) ? setSearch({ view: arg as View }) : toast(`Use /view ${VIEWS.join(', ')}`, { error: true })) },
  { name: '/sort', arg: SORTS.slice(1).map((s) => s.value).join(' | '), help: 'Change the order', run: (arg) => (arg ? setSearch({ sort: arg, page: undefined }) : toast('Use /sort posted, for example', { error: true })) },
  { name: '/save-view', arg: 'name', help: 'Save the current search as a view', run: (arg) => openOverlay({ kind: 'saveView', name: arg }) },
  { name: '/jev', arg: 'question', help: 'Check every item in the result against a yes/no question', run: (arg) => openOverlay({ kind: 'jev', question: arg }) },
  { name: '/sync', help: 'Fetch new bookmarks from the X API', run: () => void startSync() },
];

export function Palette({ scope, initial = '' }: { scope?: 'tags' | 'lists'; initial?: string }) {
  const [text, setText] = useState(initial);
  const [active, setActive] = useState(0);
  // Proposed tags can be jumped to as well (a fresh library has nothing else); they say what they are.
  const tags = flattenTags(useQuery(tagsQuery).data ?? []).filter((t) => t.node.status !== 'rejected');
  const views = useSavedViews();
  const lists = useLists();
  const bindings = useBindings();
  const words = text.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hit = (label: string) => words.every((w) => label.toLowerCase().includes(w));

  let commands: Command[];
  if (text.startsWith('/') && !scope) {
    const [name, ...rest] = text.trim().split(/\s+/);
    const arg = rest.join(' ');
    commands = SLASH.filter((c) => c.name.startsWith(name)).map((c) => ({
      section: 'Commands',
      label: c.arg ? `${c.name} ‹${c.arg}›` : c.name,
      detail: c.help,
      // `/vi` + Enter completes the name first when the command takes an argument.
      complete: c.name !== name && c.arg ? `${c.name} ` : undefined,
      run: () => c.run(arg),
    }));
  } else {
    const tagCommands: Command[] = tags.filter((t) => hit(t.path)).map((t) => ({ section: 'Tags', label: `#${t.path}`, detail: t.node.status === 'proposed' ? 'Proposed tag' : undefined, count: t.node.count, run: () => goQuery(`#${quoteValue(t.path)}`) }));
    const listCommands: Command[] = lists.filter((l) => hit(l.name)).map((l) => ({ section: 'Lists', label: l.name, count: l.count, run: () => goQuery(`list:${quoteValue(l.name)}`) }));
    if (scope === 'tags') commands = tagCommands;
    else if (scope === 'lists') commands = listCommands;
    else {
      const go: Command[] = [
        ...ACTIONS.filter((a) => a.area === 'Go to' && a.id !== 'go.tag' && a.id !== 'go.list').map((a) => ({ section: 'Go to', label: a.label, keys: bindings[a.id], run: () => runAction(a.id) })),
        { section: 'Go to', label: 'Tag management', run: () => goTo('/tags') },
        { section: 'Go to', label: 'Settings', run: () => goTo('/settings') },
        ...views.map((v) => ({ section: 'Go to', label: v.name, detail: 'Saved view', run: () => void getRouter().navigate({ to: '/', search: { q: v.query || undefined, ...(v.params ?? {}) } }) })),
      ].filter((c) => hit(c.label));
      commands = [
        ...(text.trim() ? [{ section: 'Ask', label: text.trim(), detail: 'Answer this from your library, with the items it used', run: () => void getRouter().navigate({ to: '/ask', search: { q: text.trim() } }) },
              { section: 'Search', label: text.trim(), detail: 'Search the library for these words', run: () => goQuery(text.trim()) }] : []),
        ...go,
        ...tagCommands.slice(0, text.trim() ? 8 : 5),
        ...listCommands.slice(0, 5),
        ...ACTIONS.filter((a) => a.area !== 'Go to' && hit(a.label)).map((a) => ({ section: 'Actions', label: a.label, keys: bindings[a.id], run: () => runAction(a.id) })),
      ];
    }
  }
  const at = Math.min(active, Math.max(commands.length - 1, 0));

  function execute(command: Command | undefined) {
    if (!command) return;
    if (command.complete) {
      setText(command.complete);
      setActive(0);
      return;
    }
    // Close first: many commands open another overlay, or need the page's focus back.
    closeOverlay();
    command.run();
  }

  return (
    <Modal label="Command palette">
      <input
        data-autofocus
        autoComplete="off"
        role="combobox"
        aria-expanded
        aria-controls="palette-options"
        aria-activedescendant={commands.length ? `palette-option-${at}` : undefined}
        aria-label={scope === 'tags' ? 'Jump to a tag' : scope === 'lists' ? 'Jump to a list' : 'Ask, jump or run a command'}
        placeholder={scope === 'tags' ? 'Jump to a tag…' : scope === 'lists' ? 'Jump to a list…' : 'Ask, go to a view or tag, run an action, or type / for commands'}
        value={text}
        onChange={(e) => (setText(e.target.value), setActive(0))}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || (e.ctrlKey && e.key === 'n')) {
            e.preventDefault();
            setActive((at + 1) % Math.max(commands.length, 1));
          }
          else if (e.key === 'ArrowUp' || (e.ctrlKey && e.key === 'p')) {
            e.preventDefault();
            setActive((at - 1 + commands.length) % Math.max(commands.length, 1));
          }
          else if (e.key === 'Enter') {
            e.preventDefault();
            execute(commands[at]);
          }
        }}
        className="h-12 w-full border-b border-line bg-transparent px-4 text-base outline-none placeholder:text-ink-3"
      />
      <ul id="palette-options" role="listbox" aria-label="Results" className="max-h-[56vh] overflow-y-auto py-1">
        {commands.length === 0 && <li className="px-4 py-3 text-sm text-ink-3">{scope === 'lists' ? 'No lists yet.' : scope === 'tags' ? 'No tag matches.' : 'Nothing matches.'}</li>}
        {commands.map((c, i) => (
          <li key={i} role="presentation">
            {(i === 0 || commands[i - 1].section !== c.section) && <div className="px-4 pb-0.5 pt-2 text-xs font-semibold text-ink-3">{c.section}</div>}
            <div id={`palette-option-${i}`} role="option" aria-selected={i === at} onMouseMove={() => i !== at && setActive(i)} onClick={() => execute(c)} className={`flex cursor-pointer items-center gap-3 px-4 py-1.5 text-sm ${i === at ? 'bg-accent-soft' : ''}`}>
              <span className="min-w-0 flex-1">
                <span className="block truncate">{c.label}</span>
                {c.detail && <span className="block truncate text-xs text-ink-3">{c.detail}</span>}
              </span>
              {c.count != null && <span className="text-xs tabular-nums text-ink-3">{c.count.toLocaleString()}</span>}
              {c.keys && <Keys keys={c.keys} />}
            </div>
          </li>
        ))}
      </ul>
    </Modal>
  );
}
