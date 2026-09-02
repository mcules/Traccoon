import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useStore, type ContextMenuItem } from '../lib/store';
import Icon from './Icon';

function MenuList({ items, onClose }: { items: ContextMenuItem[]; onClose: () => void }) {
  const [openSub, setOpenSub] = useState<number | null>(null);
  return (
    <>
      {items.map((it, i) =>
        it.separator ? (
          <div key={i} className="context-sep" />
        ) : (
          <div
            key={i}
            className={`context-item ${it.danger ? 'danger' : ''} ${it.submenu ? 'has-sub' : ''}`}
            onMouseEnter={() => setOpenSub(it.submenu ? i : null)}
            onClick={(e) => {
              if (it.submenu) {
                e.stopPropagation();
                // A touch device never fires mouseenter, which left every
                // submenu (Format, Paragraph, Insert…) unreachable there.
                // Tapping the row opens it instead.
                setOpenSub(openSub === i ? null : i);
                return;
              }
              it.onClick?.();
              onClose();
            }}
          >
            {it.icon && <Icon name={it.icon} size={15} />}
            <span className="ctx-label">{it.label}</span>
            {it.submenu && <Icon name="chevron-right" size={14} className="ctx-arrow" />}
            {it.submenu && openSub === i && (
              <div className="context-menu submenu">
                <MenuList items={it.submenu} onClose={onClose} />
              </div>
            )}
          </div>
        ),
      )}
    </>
  );
}

export default function ContextMenu() {
  const menu = useStore((s) => s.contextMenu);
  const close = useStore((s) => s.closeContextMenu);

  useEffect(() => {
    if (!menu) return;
    const onClick = () => close();
    const onEsc = (e: KeyboardEvent) => e.key === 'Escape' && close();
    // Attach the outside-click listener on the NEXT tick. A menu opened by a
    // left-click (e.g. the Files header sort button) is otherwise closed instantly
    // by the very click that opened it — that click keeps bubbling to window after
    // React commits this effect, so the listener would fire on it. (Right-click
    // menus were unaffected: a `contextmenu` event never fires a `click`.)
    const t = window.setTimeout(() => window.addEventListener('click', onClick), 0);
    window.addEventListener('keydown', onEsc);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener('click', onClick);
      window.removeEventListener('keydown', onEsc);
    };
  }, [menu, close]);

  if (!menu) return null;
  return <PositionedMenu key={`${menu.x},${menu.y}`} menu={menu} onClose={close} />;
}

const MARGIN = 8;

/**
 * The menu, put where it fits.
 *
 * Guessing the height from the number of rows was close enough on a desktop
 * and wrong on a phone, where a row is three times as tall for a finger: an
 * eleven-item menu was estimated at a third of its size, so the bottom of it —
 * "Delete", among others — sat below the edge of the screen with nothing to
 * suggest it was there. So it is measured instead: rendered once at the touch
 * point, then moved to where it actually fits. A menu taller than the screen
 * gets the full height and scrolls.
 */
function PositionedMenu({
  menu,
  onClose,
}: {
  menu: { x: number; y: number; items: ContextMenuItem[] };
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const place = () => {
      const { offsetWidth: w, offsetHeight: h } = el;
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      setPos({
        left: Math.max(MARGIN, Math.min(menu.x, vw - w - MARGIN)),
        // Above the finger when there is no room below, which is what a menu
        // opened near the bottom edge has to do to stay whole.
        top: Math.max(MARGIN, Math.min(menu.y, vh - h - MARGIN)),
      });
    };
    place();
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [menu.x, menu.y, menu.items]);

  return (
    <div
      ref={ref}
      className="context-menu"
      // Invisible for the one frame it takes to measure: showing it at the
      // touch point first and moving it afterwards would read as a jump.
      style={pos ? { left: pos.left, top: pos.top } : { left: menu.x, top: menu.y, visibility: 'hidden' }}
      onClick={(e) => e.stopPropagation()}
    >
      <MenuList items={menu.items} onClose={onClose} />
    </div>
  );
}
