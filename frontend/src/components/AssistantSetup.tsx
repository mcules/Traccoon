import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";

interface MailConfig {
  webhook_url: string; secret_set: boolean;
  provider: string; model: string; token_name: string;
  token_options: string[]; model_options: string[]; can_edit: boolean;
}

export default function AssistantSetup() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const { data } = useQuery({ queryKey: ["mail-config"], queryFn: () => api.get<MailConfig>("/assistant/mail-config") });

  const [model, setModel] = useState("");
  const [tokenName, setTokenName] = useState("");
  useEffect(() => { if (data) { setModel(data.model); setTokenName(data.token_name); } }, [data?.model, data?.token_name]);

  const save = useMutation({
    mutationFn: () => api.put("/assistant/mail-config", { provider: data?.provider || "openai", model, token_name: tokenName }),
    onSuccess: () => { setOk("Gespeichert."); setErr(""); qc.invalidateQueries({ queryKey: ["mail-config"] }); },
    onError: (e) => { setOk(""); setErr(e instanceof ApiError ? e.message : "Fehler"); },
  });

  if (!data) return <div className="text-sm text-muted">Lädt…</div>;
  const fullUrl = data.webhook_url.startsWith("/") ? window.location.origin + data.webhook_url : data.webhook_url;

  return (
    <div className="max-w-2xl space-y-4">
      {/* Webhook */}
      <div className="space-y-2 rounded-lg border border-line bg-card p-4">
        <div className="font-medium text-ink">📥 E-Mail-Webhook</div>
        <p className="text-sm text-muted">
          Fester Endpoint (kein normaler Webhook, weil er lokal vorklassifiziert). Richte deinen
          IMAP-Watcher hierauf ein — HMAC-SHA256 im Header <code>X-Webhook-Signature</code>.
        </p>
        <div className="flex items-center gap-2">
          <input readOnly value={fullUrl}
            className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
          <button onClick={() => { navigator.clipboard?.writeText(fullUrl); setOk("URL kopiert."); }}
            className="rounded border border-line px-2 py-1.5 text-sm text-muted hover:text-ink">Kopieren</button>
        </div>
        <div className="text-sm">
          Signatur-Secret: {data.secret_set
            ? <span className="text-green-400">gesetzt ✓</span>
            : <span className="text-red-400">nicht gesetzt</span>} <span className="text-muted">(via .env, MAIL_WEBHOOK_SECRET)</span>
        </div>
      </div>

      {/* Lokale Klassifizierung */}
      <div className="space-y-2 rounded-lg border border-line bg-card p-4">
        <div className="font-medium text-ink">🔒 Lokale Vorklassifizierung</div>
        <p className="text-sm text-muted">
          Ein <b>lokales</b> Modell schwärzt eingehende Mails, bevor etwas an den (externen)
          Assistenten geht. Wird <b>nur</b> hier genutzt — der Assistent selbst läuft auf seinem
          eigenen Modell (siehe unten).
        </p>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <label>Token
            <select value={tokenName} onChange={(e) => setTokenName(e.target.value)} disabled={!data.can_edit}
              className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink disabled:opacity-60">
              {[tokenName, ...data.token_options.filter((t) => t !== tokenName)].map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label>Modell
            <input list="classify-models" value={model} onChange={(e) => setModel(e.target.value)} disabled={!data.can_edit}
              className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink disabled:opacity-60" />
            <datalist id="classify-models">{data.model_options.map((m) => <option key={m} value={m} />)}</datalist>
          </label>
        </div>
        {data.can_edit ? (
          <button onClick={() => save.mutate()} disabled={save.isPending}
            className="rounded bg-brand px-3 py-1.5 text-sm text-white disabled:opacity-50">Speichern</button>
        ) : <p className="text-xs text-muted">Nur ein Admin kann das ändern.</p>}
      </div>

      {/* Assistent-Modell-Hinweis */}
      <div className="rounded-lg border border-line bg-card p-4 text-sm">
        <div className="font-medium text-ink">🤖 Assistent-Modell</div>
        <p className="mt-1 text-muted">
          Das Modell des Assistenten (Provider, Modell, Token) stellst du unter
          <b> Einstellungen → Mein Assistent → „assistent"</b> ein — unabhängig vom lokalen Modell oben.
        </p>
      </div>

      {err && <div className="rounded bg-red-500/10 px-3 py-2 text-sm text-red-400">{err}</div>}
      {ok && <div className="rounded bg-green-600/10 px-3 py-2 text-sm text-green-400">{ok}</div>}
    </div>
  );
}
