import { useEffect } from 'react';
import { useStore } from '../lib/store';
import SearchPanel from './SearchPanel';
import Icon from './Icon';

/**
 * Search on a phone, as its own screen.
 *
 * The same panel as on the desktop, but not squeezed into a drawer that is
 * mostly file tree: a result line needs the width to show the sentence a word
 * was found in, which is the part that decides whether it is the right note.
 * Opening a result closes the screen — searching is a way to get somewhere.
 */
export default function MobileSearch() {
  const open = useStore((s) => s.mobileSearchOpen);
  const setOpen = useStore((s) => s.setMobileSearch);
  const activePath = useStore((s) => s.activePath);

  useEffect(() => {
    if (open) setOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePath]);

  if (!open) return null;

  return (
    <div className="mobile-search">
      <div className="ms-head">
        <button className="tool-btn" title="Zurück" onClick={() => setOpen(false)}>
          <Icon name="arrow-left" size={18} />
        </button>
        <div className="ms-title">Suchen</div>
      </div>
      <SearchPanel />
    </div>
  );
}
