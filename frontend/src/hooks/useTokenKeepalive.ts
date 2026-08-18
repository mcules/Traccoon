// Extends the access token as long as a tab stands open for a long time.
//
// ── Why it is needed ────────────────────────────────────────────────────────────────────────
//
// `jwt_expire_minutes` is 720, and `src/api.ts` does `setToken(null)` plus a hard redirect to
// `/login` on a 401. Without renewal the wall screen is therefore a login form after twelve
// hours at the latest, and nobody stands in front of it at night. The endpoint behind it
// (`POST /auth/refresh`) extends exclusively an **existing** session and gives no new right;
// it checks `status` and `password_changed_at` exactly like every other call.
//
// The call goes over `/auth/…`, and exactly that path is exempt from the hard 401 redirect in
// `src/api.ts`. A failed refresh therefore does not throw the user out; they simply stay on
// the old token, and the next real call decides.
//
// Useful for every long open tab, not only for the kiosk, which is why it is a hook of its
// own and not a line in the office.

import { useEffect } from "react";
import { api, setToken } from "../api";

/** Every six hours. With a runtime of twelve hours that is twice the air before it gets
 *  tight, so a missed renewal (network gone, backend restarting) costs nothing. */
export const KEEPALIVE_MS = 6 * 60 * 60 * 1000;

/**
 * Renews the token on mounting and afterwards on the beat.
 *
 * On mounting as well: a tab coming back from an eleven hour old token would otherwise have
 * one hour left and then the same login mask.
 */
export function useTokenKeepalive(aktiv: boolean, intervallMs: number = KEEPALIVE_MS): void {
  useEffect(() => {
    if (!aktiv) return;
    let entlassen = false;

    const erneuern = async () => {
      try {
        const r = await api.post<{ access_token?: string }>("/auth/refresh");
        if (!entlassen && r?.access_token) setToken(r.access_token);
      } catch {
        // Expired, account deactivated or the backend momentarily gone: there is nothing to
        // heal here. The next ordinary call then runs into the regular 401 handling.
      }
    };

    void erneuern();
    const timer = window.setInterval(() => { void erneuern(); }, intervallMs);
    return () => { entlassen = true; window.clearInterval(timer); };
  }, [aktiv, intervallMs]);
}
