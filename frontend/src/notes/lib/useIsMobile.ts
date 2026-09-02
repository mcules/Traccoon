import { useEffect, useState } from 'react';

/** Phone-sized touch layout breakpoint (matches the CSS `@media (max-width: 768px)`). */
export const MOBILE_QUERY = '(max-width: 768px)';

/** The same question outside React, for the store and one-off decisions. */
export const isNarrow = (): boolean =>
  typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches;

/** Reactive `true` when the viewport is phone-sized. Drives drawer-based mobile UI. */
export function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia(MOBILE_QUERY);
    const onChange = () => setMobile(mq.matches);
    onChange();
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return mobile;
}
