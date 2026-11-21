// The small floating panel shown on X and LinkedIn pages. Plain DOM inside a
// shadow root, so the host page's styles and ours do not affect each other.

export interface PanelButton {
  label: string;
  onClick: () => void;
}

export interface Panel {
  show(visible: boolean): void;
  setLines(lines: string[]): void;
  setButtons(buttons: PanelButton[]): void;
}

const STYLE = `
  .box { position: fixed; right: 16px; bottom: 16px; z-index: 2147483647; width: 250px;
    font: 13px/1.4 system-ui, sans-serif; color: #111; background: #fff;
    border: 1px solid #ccc; border-radius: 8px; padding: 10px 12px; box-shadow: 0 2px 10px rgba(0,0,0,.2); }
  .title { font-weight: 600; margin-bottom: 4px; }
  .line { overflow-wrap: anywhere; }
  .buttons { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
  button { font: inherit; padding: 3px 10px; border: 1px solid #888; border-radius: 6px; background: #f4f4f4; cursor: pointer; }
`;

export function createPanel(title: string): Panel {
  const host = document.createElement('div');
  const root = host.attachShadow({ mode: 'closed' });
  const style = document.createElement('style');
  style.textContent = STYLE;
  const box = document.createElement('div');
  box.className = 'box';
  const titleEl = document.createElement('div');
  titleEl.className = 'title';
  titleEl.textContent = title;
  const linesEl = document.createElement('div');
  const buttonsEl = document.createElement('div');
  buttonsEl.className = 'buttons';
  box.append(titleEl, linesEl, buttonsEl);
  root.append(style, box);

  let lastButtons = '';
  return {
    show(visible) {
      if (visible && !host.isConnected) document.documentElement.append(host);
      if (!visible && host.isConnected) host.remove();
    },
    setLines(lines) {
      linesEl.replaceChildren(
        ...lines.map((text) => {
          const line = document.createElement('div');
          line.className = 'line';
          line.textContent = text;
          return line;
        }),
      );
    },
    setButtons(buttons) {
      // Rebuilt only when the set of buttons changes, so a click is never lost to a re-render.
      const key = buttons.map((b) => b.label).join('|');
      if (key === lastButtons) return;
      lastButtons = key;
      buttonsEl.replaceChildren(
        ...buttons.map((b) => {
          const el = document.createElement('button');
          el.textContent = b.label;
          el.addEventListener('click', b.onClick);
          return el;
        }),
      );
    },
  };
}
