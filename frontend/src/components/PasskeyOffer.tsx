import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { tr } from "../i18n";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { BUTTON_SMALL, BUTTON_TEXT } from "./ui";
import { passkeysPossible } from "../passkeys";

/**
 * Asked once: would you like a passkey?
 *
 * The settings page has had the handle from the start, and nobody found it there — a way in
 * that has to be looked for is a way in nobody uses. So the offer comes to the person
 * instead, once, at the top of the page they are on anyway.
 *
 * **Once** is the whole discipline here. A house that keeps suggesting the same thing
 * teaches people to click things away without reading, and then the suggestion that
 * mattered is the one that gets clicked away too. So "no" is kept on the person
 * (`passkey_declined_at`) and not in this browser: whoever declined at the desk is not
 * asked again on the phone.
 *
 * It sets the key up right here rather than pointing at the settings. An offer that ends in
 * "you can find it under Account → Person" is a signpost, not an offer.
 */
export default function PasskeyOffer() {
  const { user, refresh } = useAuth();
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [waiting, setWaiting] = useState(false);

  // Only asked when there is something to ask about: a browser that can do it, an
  // installation that has a domain, and an account with no key yet.
  const moeglich = !!user && !user.passkey_declined && passkeysPossible();
  const { data } = useQuery({
    queryKey: ["my-passkeys"],
    queryFn: () => api.get<{ possible: boolean; keys: unknown[] }>("/me/passkeys"),
    enabled: moeglich,
  });
  const no = useMutation({
    mutationFn: () => api.put("/me/passkey-offer", { value: true }),
    onSuccess: () => refresh(),
  });

  async function yes() {
    setErr("");
    setWaiting(true);
    try {
      const { makePasskey, whereItLives } = await import("../passkeys");
      const options = await api.post<Record<string, any>>("/me/passkeys/options", {});
      const credential = await makePasskey(options);
      await api.post("/me/passkeys", {
        credential, label: tr("passkey_offer.this_device"), kind: whereItLives(credential) });
      qc.invalidateQueries({ queryKey: ["my-passkeys"] });
    } catch (e) {
      // Cancelling the browser dialog is a decision, not a mishap — and it must not close
      // the offer either: whoever hit the wrong button wants to try again.
      const kind = (e as any)?.name;
      if (kind !== "NotAllowedError" && kind !== "AbortError") {
        setErr(e instanceof ApiError ? e.message : tr("passkey_offer.did_not_work"));
      }
    } finally {
      setWaiting(false);
    }
  }

  if (!moeglich || !data?.possible || data.keys.length) return null;

  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border
                    border-brand/40 bg-brand/10 px-3 py-2 text-sm">
      <span className="min-w-0 flex-1 text-ink">
        🔑 {tr("passkey_offer.question")}
        <span className="ml-2 text-xs text-muted">{tr("passkey_offer.hint")}</span>
      </span>
      {err && <span className="text-xs text-red-400">{err}</span>}
      <button className={BUTTON_SMALL.primary} disabled={waiting} onClick={() => void yes()}>
        {tr(waiting ? "passkey_offer.waiting" : "passkey_offer.yes")}
      </button>
      <button className={BUTTON_TEXT.secondary} disabled={no.isPending}
        onClick={() => no.mutate()}>
        {tr("passkey_offer.no")}
      </button>
    </div>
  );
}
