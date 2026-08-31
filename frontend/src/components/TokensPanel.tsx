import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { tr } from "../i18n";
import { formatDate, formatDateTime } from "../lib/formatTime";
import {
  Actions, Area, BUTTON, Button, ConfirmDialog, Dialog, DialogFoot, Errorrow, Field, ICON,
  INPUT_VALUE, IconButton, Listing, ListingEmpty, ListHeader, ListRow, Tag,
} from "./ui";

/**
 * Personal access tokens: what a long lived client logs in with.
 *
 * A JWT is right for this browser and wrong for a client that runs for months: it expires
 * after twelve hours, renewing it needs the password, and the only way to take one back
 * (changing the password) kills every session of this person everywhere. A token here does
 * none of those three.
 *
 * The one rule the interface has to carry: **the secret is shown exactly once.** It exists
 * nowhere on the server afterwards, only its hash. So the dialog after the creation is not a
 * confirmation one clicks away, it is the only chance to copy the string.
 */
interface Token {
  id: number;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  expired: boolean;
}

/** What a scope reaches, in the words of the person choosing it. */
const SCOPES: [string, string, string][] = [
  ["assistant", "tokens.scope_assistant", "tokens.scope_assistant_hint"],
  ["tickets", "tokens.scope_tickets", "tokens.scope_tickets_hint"],
  ["series_ingest", "tokens.scope_series_ingest", "tokens.scope_series_ingest_hint"],
  ["full", "tokens.scope_full", "tokens.scope_full_hint"],
];

const COLUMNS = "sm:grid-cols-[minmax(0,2fr)_minmax(0,2fr)_7rem_7rem_auto]";

export default function TokensPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [dialog, setDialog] = useState(false);
  const [fresh, setFresh] = useState<{ token: string; name: string } | null>(null);
  const [revoke, setRevoke] = useState<Token | null>(null);

  const { data: tokens } = useQuery({
    queryKey: ["me-tokens"], queryFn: () => api.get<Token[]>("/me/tokens"),
  });
  const inv = () => qc.invalidateQueries({ queryKey: ["me-tokens"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  const create = useMutation({
    mutationFn: (f: { name: string; scopes: string[]; expires_in_days: number | null }) =>
      api.post<Token & { token: string }>("/me/tokens", f),
    onSuccess: (row) => {
      setErr(""); setDialog(false); inv();
      setFresh({ token: row.token, name: row.name });
    },
    onError: fail,
  });
  const kill = useMutation({
    mutationFn: (id: number) => api.del(`/me/tokens/${id}`),
    onSuccess: () => { setRevoke(null); inv(); }, onError: fail,
  });

  return (
    <Area title={tr("tokens.title")} hint={tr("tokens.intro")}>
      <Errorrow text={err} />

      <Listing>
        <ListHeader columns={COLUMNS}>
          <span>{tr("tokens.name")}</span>
          <span>{tr("tokens.scopes")}</span>
          <span>{tr("tokens.last_used")}</span>
          <span>{tr("tokens.expiry")}</span>
          <span />
        </ListHeader>
        {tokens?.map((t) => {
          // A revoked or expired token stays in the list: the record of what once had access
          // is exactly the thing one looks for afterwards. Dimmed, so it is visibly out of
          // service without disappearing.
          const dead = !!t.revoked_at || t.expired;
          return (
            <ListRow key={t.id} columns={COLUMNS} dimmed={dead}>
              <div className="min-w-0">
                <div className="truncate font-medium text-ink">{t.name}</div>
                <div className="truncate font-mono text-[11px] text-muted">
                  trc_{t.prefix}…
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-1">
                {t.scopes.map((s) => (
                  <Tag key={s} color={s === "full" ? "yellow" : "blue"}>
                    {tr(`tokens.scope_${s}`)}
                  </Tag>
                ))}
                {t.revoked_at && <Tag color="red">{tr("tokens.revoked")}</Tag>}
                {!t.revoked_at && t.expired && <Tag color="red">{tr("tokens.expired")}</Tag>}
              </div>
              <span className="text-xs text-muted"
                title={t.last_used_at ? formatDateTime(t.last_used_at) : undefined}>
                {t.last_used_at ? formatDate(t.last_used_at) : tr("tokens.never_used")}
              </span>
              <span className="text-xs text-muted"
                title={tr("tokens.created_on", { date: formatDateTime(t.created_at) })}>
                {t.expires_at ? formatDate(t.expires_at) : tr("tokens.no_expiry")}
              </span>
              <Actions>
                <IconButton icon={ICON.remove} title={tr("tokens.revoke")} danger
                  disabled={!!t.revoked_at} onClick={() => setRevoke(t)} />
              </Actions>
            </ListRow>
          );
        })}
        {tokens?.length === 0 && <ListingEmpty>{tr("tokens.none_yet")}</ListingEmpty>}
      </Listing>

      <button onClick={() => { setErr(""); setDialog(true); }} className={BUTTON.primary}>
        {ICON.fresh} {tr("tokens.new")}
      </button>

      {dialog && (
        <CreateDialog runs={create.isPending} onClose={() => setDialog(false)}
          onSave={(f) => create.mutate(f)} />
      )}
      {fresh && <ShowOnce token={fresh.token} name={fresh.name} onClose={() => setFresh(null)} />}
      {revoke && (
        <ConfirmDialog title={tr("tokens.revoke")}
          text={tr("tokens.really_revoke", { name: revoke.name })}
          hint={tr("tokens.revoke_hint")} confirmText={tr("tokens.revoke")}
          runs={kill.isPending} onClose={() => setRevoke(null)}
          onConfirm={() => kill.mutate(revoke.id)} />
      )}
    </Area>
  );
}

function CreateDialog({ runs, onClose, onSave }: {
  runs: boolean; onClose: () => void;
  onSave: (f: { name: string; scopes: string[]; expires_in_days: number | null }) => void;
}) {
  const [name, setName] = useState("");
  const [chosen, setChosen] = useState<string[]>(["assistant"]);
  const [days, setDays] = useState("");

  const toggle = (key: string) =>
    setChosen(chosen.includes(key) ? chosen.filter((s) => s !== key) : [...chosen, key]);
  const number = days.trim() ? Number(days) : null;
  const badDays = number !== null && (!Number.isInteger(number) || number < 1 || number > 3650);

  return (
    // `hold`: the name is typed in here, and Escape is pressed faster while writing than one
    // thinks.
    <Dialog title={tr("tokens.new")} onClose={onClose} hold foot={
      <DialogFoot onCancel={onClose} runs={runs} saveText={tr("tokens.create")}
        disabled={!name.trim() || chosen.length === 0 || badDays}
        onSave={() => onSave({ name: name.trim(), scopes: chosen, expires_in_days: number })} />
    }>
      <div className="space-y-4">
        <Field label={tr("tokens.name")} hint={tr("tokens.name_hint")}>
          <input value={name} onChange={(e) => setName(e.target.value)} autoFocus
            placeholder={tr("tokens.name_placeholder")} className={INPUT_VALUE} />
        </Field>

        <div>
          <span className="text-xs font-medium text-muted">{tr("tokens.scopes")}</span>
          <div className="mt-1 space-y-2">
            {SCOPES.map(([key, label, hint]) => (
              <label key={key} className="flex cursor-pointer items-start gap-2">
                <input type="checkbox" checked={chosen.includes(key)}
                  onChange={() => toggle(key)} className="mt-0.5" />
                <span className="min-w-0">
                  <span className="block text-sm text-ink">{tr(label)}</span>
                  <span className="block text-[11px] text-muted">{tr(hint)}</span>
                </span>
              </label>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-muted">{tr("tokens.deny_by_default")}</p>
        </div>

        <Field label={tr("tokens.expiry_days")} hint={tr("tokens.expiry_hint")}>
          <input value={days} onChange={(e) => setDays(e.target.value)} inputMode="numeric"
            placeholder={tr("tokens.no_expiry")} className={INPUT_VALUE} />
        </Field>
        {badDays && (
          <div className="text-xs text-amber-300">{tr("tokens.expiry_out_of_range")}</div>
        )}
      </div>
    </Dialog>
  );
}

/**
 * The token, once.
 *
 * `hold`: a click beside it would cost the string, and there is no second chance to read it
 * anywhere. The only way out is the button that says one has copied it.
 */
function ShowOnce({ token, name, onClose }: {
  token: string; name: string; onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
    } catch {
      // Without clipboard rights (an insecure origin, a locked-down browser) the field is
      // still selectable, so nothing is lost.
      setCopied(false);
    }
  };
  return (
    <Dialog title={tr("tokens.created_title", { name })} onClose={onClose} hold foot={
      <Button variant="primary" onClick={onClose}>{tr("tokens.copied_close")}</Button>
    }>
      <div className="space-y-3">
        <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
          {tr("tokens.shown_once")}
        </div>
        <textarea readOnly value={token} rows={2} onFocus={(e) => e.currentTarget.select()}
          className={`${INPUT_VALUE} resize-none break-all font-mono text-xs`} />
        <Button variant="secondary" onClick={copy} state={copied ? "good" : "open"}>
          {tr("tokens.copy")}
        </Button>
      </div>
    </Dialog>
  );
}
