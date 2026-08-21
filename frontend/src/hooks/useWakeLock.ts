// Keeps the screen awake as long as the office runs as a wall screen.
//
// ── Why this file lies here and not in `components/office/` ──────────────────────────────────
//
// `tools/office-check.mjs` assigns every unknown `.ts` **directly** in `office/` to layer 0
// fail-closed (`layerOf`), and layer 0 has to pass the purity grep: no clock, no randomness,
// **no browser environment**. A `navigator.wakeLock` would fail there. Softening the checker
// for it would be exactly the precedent `PIXEL-CONTRACT.md` wants to avoid, so the hook lives
// outside, in `src/hooks/`.
//
// ── What the wake lock can and cannot do ────────────────────────────────────────────────────
//
// It prevents the screen from dimming; it starts **no** full screen. Full screen needs a user
// gesture (`requestFullscreen()` fails silently otherwise), which is what the ⛶ button in the
// kiosk is for, and in practice the wall starts as `chromium --kiosk` anyway.
//
// The lock is released **automatically** by the browser as soon as the document is hidden
// (tab change, screen lock). It is therefore not enough to request it once: on every
// `visibilitychange` back to visible it is requested anew. Without that the wall screen is a
// normal screen with a screensaver again after the first tab change.

import { useEffect } from "react";

/** The part of the wake lock API this hook uses. Declared by hand instead of pulled from
 *  `lib.dom`: the types are present or not depending on the TypeScript version, and a build
 *  that depends on the library version is exactly the kind of surprise nobody is looking
 *  for. Access happens over exactly **one** reinterpretation, below. */
interface WakeLockSentinelLike {
  released: boolean;
  release(): Promise<void>;
}
interface WakeLockLike {
  request(kind: "screen"): Promise<WakeLockSentinelLike>;
}

/**
 * Keeps the screen awake as long as `aktiv` holds.
 *
 * Errors are not an error here: `request()` throws in insecure contexts (no HTTPS), when the
 * permission is missing or when the document is not visible right now. A wall screen writing
 * an exception into the console because of that is still a working wall screen; a wall screen
 * turning white because of it is not.
 */
export function useWakeLock(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const wl = (navigator as unknown as { wakeLock?: WakeLockLike }).wakeLock;
    if (!wl) return;                              // Merkmalsprüfung: Firefox/Safari-Altstand

    let release = false;
    let block: WakeLockSentinelLike | null = null;

    const anfordern = async () => {
      if (release || block !== null || document.visibilityState !== "visible") return;
      try {
        block = await wl.request("screen");
        // The effect can have been cleaned up during the `await`, and then the freshly
        // fetched lock belongs to nobody and is released again immediately.
        if (release) { void block.release().catch(() => {}); block = null; }
      } catch {
        block = null;
      }
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        block = null;                            // beim Verstecken hat der Browser sie gelöst
        void anfordern();
      }
    };

    void anfordern();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      release = true;
      document.removeEventListener("visibilitychange", onVisibility);
      void block?.release().catch(() => {});
      block = null;
    };
  }, [active]);
}
