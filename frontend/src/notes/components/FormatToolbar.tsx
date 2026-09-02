import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { getCommand, runCommand } from '../lib/commands';
import Icon from './Icon';
import type { IconName } from './Icon';
import {
  fmtInline,
  fmtChecklist,
  fmtPrefixLines,
  fmtInsert,
  fmtLink,
  fmtIndent,
  fmtOutdent,
  fmtUndo,
  fmtRedo,
} from '../lib/activeEditor';

type Btn = { icon: IconName; title: string; run: () => void };

/** Icon for a command id, where the toolbar has one. */
const ICONS: Record<string, IconName> = {
  'daily-notes': 'calendar',
  'editor:undo': 'undo',
  'editor:redo': 'redo',
  'editor:insert-wikilink': 'brackets',
  'editor:insert-embed': 'image',
  'editor:insert-tag': 'hash',
  'editor:attach-file': 'paperclip',
  'editor:set-heading': 'heading',
  'editor:toggle-bold': 'bold',
  'editor:toggle-italics': 'italic',
  'editor:toggle-strikethrough': 'strikethrough',
  'editor:toggle-highlight': 'highlighter',
  'editor:toggle-code': 'code',
  'editor:toggle-blockquote': 'quote',
  'editor:insert-link': 'link',
  'editor:toggle-bullet-list': 'list',
  'editor:toggle-numbered-list': 'list-ordered',
  'editor:toggle-checklist-status': 'check-square',
  'editor:indent-list': 'indent-increase',
  'editor:unindent-list': 'indent-decrease',
  'templates:insert-template': 'file-text',
};

/** What the toolbar shows when the vault says nothing. */
const FALLBACK = [
  'editor:set-heading',
  'editor:toggle-bold',
  'editor:toggle-italics',
  'editor:toggle-bullet-list',
  'editor:toggle-checklist-status',
  'editor:toggle-blockquote',
  'editor:insert-wikilink',
  'editor:insert-link',
  'editor:toggle-code',
  'editor:insert-tag',
  'editor:indent-list',
  'editor:unindent-list',
  'editor:undo',
  'editor:redo',
];

/**
 * The buttons the vault itself lists.
 *
 * `app.json` records the phone toolbar as an ordered list of command ids —
 * the same ids the command registry uses. Reading it means the web toolbar and
 * the mobile app show the same buttons in the same order, without a second
 * place to maintain. Ids this app has no command for are simply skipped.
 */
function Buttons({ size, ids }: { size: number; ids: string[] }) {
  const cmds = ids
    .map((id) => ({ id, cmd: getCommand(id) }))
    .filter((x): x is { id: string; cmd: NonNullable<ReturnType<typeof getCommand>> } => !!x.cmd);
  return (
    <>
      {cmds.map(({ id, cmd }) => (
        <button
          key={id}
          title={cmd.name}
          // Keep the editor focused / the keyboard open when a button is pressed.
          onPointerDown={(e) => e.preventDefault()}
          onClick={() => runCommand(id)}
        >
          <Icon name={ICONS[id] ?? 'chevron-right'} size={size} />
        </button>
      ))}
    </>
  );
}

/**
 * Markdown formatting toolbar (in the shape it had on a phone). Two layouts:
 * - mobile: fixed above the on-screen keyboard (anchored via the visual viewport,
 *   since iOS Safari doesn't reflow fixed elements for the keyboard).
 * - desktop: an in-flow strip rendered right under the view header.
 */
export default function FormatToolbar({ mobile = false }: { mobile?: boolean }) {
  const [bottom, setBottom] = useState(0);
  const [ids, setIds] = useState<string[]>(FALLBACK);

  useEffect(() => {
    api
      .vaultConfig()
      .then((c) => setIds(c.app.mobileToolbarCommands.length ? c.app.mobileToolbarCommands : FALLBACK))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!mobile) return;
    const vv = window.visualViewport;
    if (!vv) return;
    const update = () => {
      // Gap between the layout-viewport bottom and the visual-viewport bottom =
      // height of the on-screen keyboard (0 when hidden).
      const gap = window.innerHeight - (vv.height + vv.offsetTop);
      setBottom(Math.max(0, gap));
    };
    update();
    vv.addEventListener('resize', update);
    vv.addEventListener('scroll', update);
    return () => {
      vv.removeEventListener('resize', update);
      vv.removeEventListener('scroll', update);
    };
  }, [mobile]);

  if (mobile) {
    return (
      <div className="mobile-toolbar" style={{ bottom }}>
        <Buttons size={20} ids={ids} />
      </div>
    );
  }
  return (
    <div className="format-toolbar">
      <Buttons size={16} ids={ids} />
    </div>
  );
}
