import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { tr } from "../i18n";
import {
  Aktionen, Bereich, Dialog, DialogFuss, EINGABE, Etikett, Feld, Fehlerzeile, ICON, IconKnopf,
  Liste, ListeLeer, ListenZeile, LoeschDialog, Zeilenknopf, Zustand, Reiter, KNOPF } from "./ui";

/**
 * Mail-Konten und ihre Identitäten.
 *
 * Sie gehören auf die Konto-Seite und nicht in die Einstellungen: ein Postfach ist keine
 * Ressource, mit der Agenten arbeiten, sondern die Post einer Person. Kennwörter kommen vom
 * Server nie zurück — ein leeres Feld heißt deshalb „unverändert" und nicht „löschen".
 */
export interface MailKonto {
  id: number; name: string; enabled: boolean;
  imap_host: string; imap_port: number; imap_ssl: boolean; imap_user: string;
  smtp_host: string; smtp_port: number; smtp_security: string; smtp_user: string;
  folder_sent: string; folder_drafts: string; folder_trash: string; folder_junk: string;
  folder_archive: string; archive_mode: string; archive_pattern: string;
  mcp_enabled: boolean; mcp_ignore_folders: string[]; mcp_tools: string[];
  mcp_instructions: string;
  imap_password_set: boolean; smtp_password_set: boolean; auth_type: string;
}

export interface MailIdentitaet {
  id: number; account_id: number; display_name: string; email: string;
  reply_to: string; signature: string; is_default: boolean;
}

const LEER = {
  name: "", enabled: true,
  imap_host: "", imap_port: 993, imap_ssl: true, imap_user: "", imap_password: "",
  smtp_host: "", smtp_port: 587, smtp_security: "starttls", smtp_user: "", smtp_password: "",
  folder_sent: "Sent", folder_drafts: "Drafts", folder_trash: "Trash", folder_junk: "Junk",
  folder_archive: "Archive", archive_mode: "folder", archive_pattern: "Archive/{jahr}",
  mcp_enabled: false, mcp_ignore_folders: [] as string[], mcp_tools: [] as string[],
  mcp_instructions: "",
};

export default function MailKontenPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [dialog, setDialog] = useState<(typeof LEER & { id?: number }) | null>(null);
  const [loeschKonto, setLoeschKonto] = useState<MailKonto | null>(null);

  const { data: konten } = useQuery({
    queryKey: ["mail-accounts"], queryFn: () => api.get<MailKonto[]>("/mailbox/accounts") });
  const inv = () => qc.invalidateQueries({ queryKey: ["mail-accounts"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const speichern = useMutation({
    mutationFn: (f: typeof LEER & { id?: number }) =>
      f.id ? api.put(`/mailbox/accounts/${f.id}`, f) : api.post("/mailbox/accounts", f),
    onSuccess: () => { setErr(""); setDialog(null); inv(); }, onError: fail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/mailbox/accounts/${id}`),
    onSuccess: () => { setLoeschKonto(null); inv(); }, onError: fail,
  });

  return (
    <Bereich hinweis="Postfächer, die du hier liest und aus denen du schreibst. Zugang, Ordner, Identitäten und der Verbindungstest stehen im Dialog hinter dem Stift; das Kennwort wird verschlüsselt abgelegt und nie wieder angezeigt.">
      <Fehlerzeile text={err} />

      <Liste>
        {konten?.map((k) => (
          <ListenZeile key={k.id} gedimmt={!k.enabled}>
            <div className="flex flex-wrap items-center gap-2">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-ink">{k.name}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span className="truncate font-mono">{k.imap_user || k.imap_host}</span>
                  {!k.smtp_host && <Etikett farbe="gelb">nur lesen</Etikett>}
                  {!k.imap_password_set && <Etikett farbe="rot">kein Kennwort</Etikett>}
                </div>
              </div>
              {k.enabled
                ? <Zustand farbe="gruen" text="aktiv" />
                : <Zustand farbe="grau" text="aus" />}
              <Aktionen>
                <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")}
                  onClick={() => { setErr(""); setDialog({ ...LEER, ...k, imap_password: "", smtp_password: "" }); }} />
                <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                  onClick={() => setLoeschKonto(k)} />
              </Aktionen>
            </div>
          </ListenZeile>
        ))}
        {konten?.length === 0 && <ListeLeer>Noch kein Postfach hinterlegt.</ListeLeer>}
      </Liste>

      <button onClick={() => { setErr(""); setDialog({ ...LEER }); }}
        className={KNOPF.haupt}>
        {ICON.neu} Postfach hinzufügen
      </button>

      <McpZugang onFehler={fail} />

      {dialog && (
        <KontoDialog start={dialog} laeuft={speichern.isPending} fehler={err}
          onClose={() => setDialog(null)} onSpeichern={(f) => speichern.mutate(f)} />
      )}
      {loeschKonto && (
        <LoeschDialog was={loeschKonto.name} laeuft={loeschen.isPending}
          hinweis="Das Postfach selbst bleibt unberührt — nur der Zugang hier verschwindet."
          onClose={() => setLoeschKonto(null)}
          onLoeschen={() => loeschen.mutate(loeschKonto.id)} />
      )}
    </Bereich>
  );
}

export function KontoDialog({ start, fehler, laeuft, onClose, onSpeichern }: {
  start: typeof LEER & { id?: number }; fehler: string; laeuft: boolean;
  onClose: () => void; onSpeichern: (f: typeof LEER & { id?: number }) => void;
}) {
  const [f, setF] = useState(start);
  const [pruefung, setPruefung] = useState("");
  const [err, setErr] = useState("");
  const [teil, setTeil] = useState<"empfang" | "senden" | "ordner" | "identitaeten"
    | "agenten">("empfang");
  const setzen = (teil: Partial<typeof LEER>) => setF({ ...f, ...teil });
  const testen = useMutation({
    mutationFn: () => api.post<{ imap: string; smtp: string }>(
      `/mailbox/accounts/${start.id}/test`, {}),
    onSuccess: (r) => setPruefung(`IMAP: ${r.imap || "—"} · SMTP: ${r.smtp || "—"}`),
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Prüfen fehlgeschlagen"),
  });
  // Die Ordner des Kontos selbst — aber erst, wenn es eines gibt: bei einem neuen Postfach
  // ist noch keine Verbindung möglich, und ein leeres Auswahlfeld wäre schlechter als ein
  // Textfeld, in das man den Namen tippen kann.
  const { data: ordner } = useQuery({
    queryKey: ["mail-folders", start.id],
    queryFn: () => api.get<{ name: string; display: string; level: number }[]>(
      `/mailbox/accounts/${start.id}/folders`),
    enabled: !!start.id,
    retry: false,
  });
  return (
    <Dialog breit titel={f.id ? `Postfach ${f.name}` : "Postfach hinzufügen"} onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} laeuft={laeuft}
        deaktiviert={!f.name.trim() || !f.imap_host.trim()}
        onSpeichern={() => onSpeichern(f)} />}>
      <Fehlerzeile text={fehler || err} />
      <div className="space-y-4">
        {/* Name und Schalter stehen über dem Menü: sie gehören zu keinem der vier Teile,
            sondern zum Postfach als Ganzem. */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-48 flex-1">
            <Feld label="Name" hinweis="Kurzname in der Oberfläche und in Abläufen (privat, vorstand).">
              <input value={f.name} onChange={(e) => setzen({ name: e.target.value })}
                placeholder="privat" className={EINGABE} />
            </Feld>
          </div>
          <label className="flex items-center gap-2 pb-1.5 text-sm text-muted">
            <input type="checkbox" checked={f.enabled}
              onChange={(e) => setzen({ enabled: e.target.checked })} />
            Aktiv
          </label>
        </div>

        <div className="flex flex-col gap-4 sm:flex-row">
          <Reiter senkrecht aktiv={teil} onWaehlen={setTeil} auswahl={[
            ["empfang", "📥 Empfang"],
            ["senden", "📤 Senden"],
            ["ordner", "📁 Ordner"],
            ["identitaeten", "👤 Identitäten"],
            ["agenten", "🤖 Agenten"],
          ]} />

          <div className="min-w-0 flex-1 space-y-4">
        {teil === "empfang" && (<>
        <div className="grid gap-2 sm:grid-cols-2">
          <Feld label="Server"><input value={f.imap_host} className={EINGABE}
            onChange={(e) => setzen({ imap_host: e.target.value })} placeholder="imap.example.org" /></Feld>
          <Feld label="Port"><input type="number" value={f.imap_port} className={EINGABE}
            onChange={(e) => setzen({ imap_port: Number(e.target.value) })} /></Feld>
          <Feld label="Benutzer"><input value={f.imap_user} className={EINGABE}
            onChange={(e) => setzen({ imap_user: e.target.value })} /></Feld>
          <Feld label="Kennwort" hinweis={start.id ? "Leer lassen heißt: unverändert." : ""}>
            <input type="password" value={f.imap_password} className={EINGABE}
              onChange={(e) => setzen({ imap_password: e.target.value })} /></Feld>
          <label className="flex items-center gap-2 text-sm text-muted">
            <input type="checkbox" checked={f.imap_ssl}
              onChange={(e) => setzen({ imap_ssl: e.target.checked })} />
            Verschlüsselt (SSL/TLS)
          </label>
        </div>
        </>)}

        {teil === "senden" && (<>
        <div className="grid gap-2 sm:grid-cols-2">
          <Feld label="Server"><input value={f.smtp_host} className={EINGABE}
            onChange={(e) => setzen({ smtp_host: e.target.value })} placeholder="smtp.example.org" /></Feld>
          <Feld label="Port"><input type="number" value={f.smtp_port} className={EINGABE}
            onChange={(e) => setzen({ smtp_port: Number(e.target.value) })} /></Feld>
          <Feld label="Benutzer"><input value={f.smtp_user} className={EINGABE}
            onChange={(e) => setzen({ smtp_user: e.target.value })} /></Feld>
          <Feld label="Kennwort" hinweis={start.id ? "Leer lassen heißt: unverändert." : ""}>
            <input type="password" value={f.smtp_password} className={EINGABE}
              onChange={(e) => setzen({ smtp_password: e.target.value })} /></Feld>
          <Feld label="Verschlüsselung"
            hinweis={'587 rüstet auf (STARTTLS), 465 ist von Anfang an verschlüsselt. '
              + 'Passt beides nicht zusammen, meldet der Server „wrong version number".'}>
            <select value={f.smtp_security} className={EINGABE}
              onChange={(e) => {
                const art = e.target.value;
                // Den Port mitziehen, solange er der übliche der anderen Variante ist: wer
                // die Verschlüsselung umstellt, meint fast immer auch den passenden Port —
                // und ein eigens eingetragener Port (2525 …) bleibt unangetastet.
                const port = art === "ssl" && f.smtp_port === 587 ? 465
                  : art === "starttls" && f.smtp_port === 465 ? 587
                    : f.smtp_port;
                setzen({ smtp_security: art, smtp_port: port });
              }}>
              <option value="starttls">STARTTLS (587)</option>
              <option value="ssl">SSL/TLS (465)</option>
              <option value="none">ohne (nur im Haus)</option>
            </select>
          </Feld>
        </div>

        </>)}

        {teil === "ordner" && (<>
        {!start.id && (
          <p className="text-xs text-muted">
            Nach dem Speichern kannst du die Ordner aus dem Postfach auswählen — bis dahin
            stehen hier die üblichen Namen.
          </p>
        )}
        <div className="grid gap-2 sm:grid-cols-2">
          <OrdnerFeld label="Gesendet" wert={f.folder_sent} ordner={ordner}
            onWaehlen={(v) => setzen({ folder_sent: v })} />
          <OrdnerFeld label="Entwürfe" wert={f.folder_drafts} ordner={ordner}
            onWaehlen={(v) => setzen({ folder_drafts: v })} />
          <OrdnerFeld label="Papierkorb" wert={f.folder_trash} ordner={ordner}
            onWaehlen={(v) => setzen({ folder_trash: v })} />
          <OrdnerFeld label="Spam" wert={f.folder_junk} ordner={ordner}
            hinweis={'Ziel des Knopfes „Spam" — ohne Ordner erscheint der Knopf nicht.'}
            onWaehlen={(v) => setzen({ folder_junk: v })} />
        </div>

        <div className="text-xs font-medium uppercase tracking-wider text-muted/70">Archiv</div>
        <Feld label="Aufteilung"
          hinweis={'Ziel des Knopfes „Archivieren" — ohne Ziel erscheint der Knopf nicht.'}>
          <select value={f.archive_mode} className={EINGABE}
            onChange={(e) => setzen({ archive_mode: e.target.value })}>
            <option value="folder">Ein Ordner für alles</option>
            <option value="pattern">Nach Muster aufteilen (Jahr, Monat …)</option>
          </select>
        </Feld>
        {f.archive_mode === "folder" ? (
          <OrdnerFeld label="Archiv-Ordner" wert={f.folder_archive} ordner={ordner}
            onWaehlen={(v) => setzen({ folder_archive: v })} />
        ) : (
          <MusterFeld kontoId={start.id} wert={f.archive_pattern}
            onAendern={(v) => setzen({ archive_pattern: v })} />
        )}

        </>)}

        {teil === "agenten" && (
          <AgentenFreigabe f={f} setzen={setzen} ordner={ordner} />
        )}

        {teil === "identitaeten" && (
          start.id ? (
            <Identitaeten kontoId={start.id}
              onFehler={(e) => setErr(e instanceof ApiError ? e.message : "Fehler")} />
          ) : (
            <p className="text-sm text-muted">
              Identitäten gibt es, sobald das Postfach gespeichert ist — sie hängen daran.
            </p>
          )
        )}
          </div>
        </div>

        {start.id && (
          <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
            <Zeilenknopf onClick={() => { setErr(""); testen.mutate(); }}>
              {testen.isPending ? "prüft…" : "🔌 IMAP und SMTP prüfen"}
            </Zeilenknopf>
            {/* Der Test benutzt, was gespeichert ist — nicht, was gerade im Formular steht.
                Anders ginge es nicht, ohne halbfertige Zugangsdaten zum Server zu schicken. */}
            <span className="text-xs text-muted">
              {pruefung || "prüft den gespeicherten Stand"}
            </span>
          </div>
        )}
      </div>
    </Dialog>
  );
}

/**
 * Der Zugang, über den Agenten die freigegebenen Postfächer erreichen.
 *
 * Ein Token je Person, nicht je Postfach: Wer es hat, sieht genau das, was an den einzelnen
 * Postfächern freigegeben ist — die Feinheiten stehen dort und nicht hier. Angezeigt wird es
 * genau einmal; ein zweites Mal könnte es nur, wer es speichert, und dann wäre es kein
 * Geheimnis mehr, sondern eine Kopie.
 */
function McpZugang({ onFehler }: { onFehler: (e: unknown) => void }) {
  const qc = useQueryClient();
  const [frisch, setFrisch] = useState("");
  const { data: stand } = useQuery({
    queryKey: ["mcp-status"],
    queryFn: () => api.get<{ token_set: boolean; fingerprint: string }>("/mailbox/mcp-status"),
  });
  const inv = () => qc.invalidateQueries({ queryKey: ["mcp-status"] });
  const erzeugen = useMutation({
    mutationFn: () => api.post<{ token: string }>("/mailbox/mcp-token", {}),
    onSuccess: (r) => { setFrisch(r.token); inv(); }, onError: onFehler,
  });
  const loeschen = useMutation({
    mutationFn: () => api.del("/mailbox/mcp-token"),
    onSuccess: () => { setFrisch(""); inv(); }, onError: onFehler,
  });
  const adresse = `${location.origin}/api/mcp/mail`;

  return (
    <div className="mt-4 space-y-2 border-t border-line pt-4">
      <div className="text-sm font-medium text-ink">Zugang für Agenten (MCP)</div>
      <p className="text-xs text-muted">
        Diese Adresse trägt man in MCPJungle (oder einen anderen Client) ein, mit dem Token
        als <code>Authorization: Bearer …</code>. Freigegeben ist nur, was am jeweiligen
        Postfach unter <b>Agenten</b> steht.
      </p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded bg-surface px-1.5 py-0.5 text-xs">
          {adresse}
        </code>
        <IconKnopf icon={ICON.kopieren} titel="Adresse kopieren"
          onClick={() => navigator.clipboard?.writeText(adresse)} />
      </div>
      {frisch ? (
        <div className="space-y-1 rounded border border-amber-500/30 bg-amber-500/10 p-2">
          <div className="text-xs text-amber-300">
            Einmalig sichtbar — jetzt kopieren, danach nur noch neu erzeugbar.
          </div>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate text-xs text-ink">{frisch}</code>
            <IconKnopf icon={ICON.kopieren} titel="Token kopieren"
              onClick={() => navigator.clipboard?.writeText(frisch)} />
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Zeilenknopf onClick={() => erzeugen.mutate()}>
            {stand?.token_set ? "Neues Token erzeugen" : "Token erzeugen"}
          </Zeilenknopf>
          {stand?.token_set && (
            <>
              <Etikett farbe="gruen">Token gesetzt · {stand.fingerprint}</Etikett>
              <Zeilenknopf gefahr onClick={() => loeschen.mutate()}>Zugang sperren</Zeilenknopf>
            </>
          )}
          {stand?.token_set && (
            <span className="text-xs text-muted">
              Ein neues Token macht das alte sofort ungültig.
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Was Agenten von diesem Postfach sehen und tun dürfen.
 *
 * Die Voreinstellung ist: nichts. Ein Postfach ist die Post eines Menschen und kein
 * Datenbestand — deshalb wird je Werkzeug einzeln freigegeben statt „Zugriff ja/nein", und
 * Lesen, Umsortieren und Senden stehen als drei getrennte Gruppen da und nicht als Stufen
 * einer Leiter.
 */
function AgentenFreigabe({ f, setzen, ordner }: {
  f: typeof LEER & { id?: number };
  setzen: (teil: Partial<typeof LEER>) => void;
  ordner: { name: string; display: string; level: number }[] | undefined;
}) {
  const [neuesMuster, setNeuesMuster] = useState("");
  const { data: katalog } = useQuery({
    queryKey: ["mcp-tools"],
    queryFn: () => api.get<{ name: string; kind: string; description: string; always: boolean }[]>(
      "/mailbox/mcp-tools"),
    staleTime: 60 * 60_000,
  });

  const GRUPPE: Record<string, string> = {
    lesen: "Lesen", aendern: "Umsortieren", senden: "Senden",
  };
  const umschalten = (name: string) => {
    const drin = f.mcp_tools.includes(name);
    setzen({ mcp_tools: drin ? f.mcp_tools.filter((t) => t !== name)
                             : [...f.mcp_tools, name] });
  };
  const musterWeg = (m: string) =>
    setzen({ mcp_ignore_folders: f.mcp_ignore_folders.filter((x) => x !== m) });
  const musterDazu = (m: string) => {
    const wert = m.trim();
    if (wert && !f.mcp_ignore_folders.includes(wert)) {
      setzen({ mcp_ignore_folders: [...f.mcp_ignore_folders, wert] });
    }
    setNeuesMuster("");
  };

  return (
    <div className="space-y-4">
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={f.mcp_enabled}
          onChange={(e) => setzen({ mcp_enabled: e.target.checked })} />
        Dieses Postfach für Agenten freigeben
      </label>
      <p className="text-xs text-muted">
        Agenten erreichen es über den MCP-Zugang deines Kontos (Konto → Mail-Konten, unten).
        Ohne Häkchen existiert das Postfach für sie nicht.
      </p>

      {f.mcp_enabled && (<>
        <div className="text-xs font-medium uppercase tracking-wider text-muted/70">
          Anweisungen
        </div>
        <Feld label="Was ein Agent über dieses Postfach wissen muss"
          hinweis="Wird beim Verbinden gelesen, also bevor das erste Werkzeug läuft — und steht zusätzlich an jedem Postfach in der Übersicht.">
          <textarea value={f.mcp_instructions} rows={5} className={`${EINGABE} text-xs`}
            placeholder={"Vereinspostfach des Vorstands. Sachlich und in Sie-Form antworten.\n"
              + "Nichts ohne Rückfrage senden. Rechnungen gehören ins Archiv, nicht in den Papierkorb."}
            onChange={(e) => setzen({ mcp_instructions: e.target.value })} />
        </Feld>

        <div className="text-xs font-medium uppercase tracking-wider text-muted/70">
          Werkzeuge
        </div>
        {["lesen", "aendern", "senden"].map((art) => (
          <div key={art} className="space-y-1">
            <div className="text-xs font-medium text-ink">{GRUPPE[art]}</div>
            {(katalog || []).filter((w) => w.kind === art && !w.always).map((w) => (
              <label key={w.name} className="flex items-start gap-2 text-sm text-muted">
                <input type="checkbox" className="mt-1"
                  checked={f.mcp_tools.includes(w.name)}
                  onChange={() => umschalten(w.name)} />
                <span>
                  <code className="text-ink">{w.name}</code>
                  <span className="ml-2 text-xs">{w.description}</span>
                </span>
              </label>
            ))}
          </div>
        ))}

        <div className="text-xs font-medium uppercase tracking-wider text-muted/70">
          Ordner ausblenden
        </div>
        <p className="text-xs text-muted">
          Was hier steht, gibt es für Agenten nicht — weder in der Ordnerliste noch in einer
          Suche, und verschieben können sie auch nichts dorthin. Muster mit <code>*</code>
          sind erlaubt (<code>Privat*</code>).
        </p>
        <Liste>
          {f.mcp_ignore_folders.map((m) => (
            <ListenZeile key={m} dicht>
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate">{m}</code>
                <Zeilenknopf gefahr onClick={() => musterWeg(m)}>Entfernen</Zeilenknopf>
              </div>
            </ListenZeile>
          ))}
          {!f.mcp_ignore_folders.length && <ListeLeer>Nichts ausgeblendet.</ListeLeer>}
        </Liste>
        <div className="flex flex-wrap items-center gap-2">
          <select value="" className={`${EINGABE} max-w-xs`}
            onChange={(e) => e.target.value && musterDazu(e.target.value)}>
            <option value="">Ordner wählen…</option>
            {(ordner || []).map((o) => (
              <option key={o.name} value={o.name}>
                {"\u00a0".repeat(o.level * 2)}{o.display}
              </option>
            ))}
          </select>
          <input value={neuesMuster} placeholder="oder Muster: Privat*"
            className={`${EINGABE} max-w-xs font-mono`}
            onChange={(e) => setNeuesMuster(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); musterDazu(neuesMuster); } }} />
          <Zeilenknopf onClick={() => musterDazu(neuesMuster)}>Hinzufügen</Zeilenknopf>
        </div>
      </>)}
    </div>
  );
}

/**
 * Das Archiv-Muster, mit Vorschau beim Tippen.
 *
 * Gefüllt wird es aus dem Datum DER MAIL, nicht aus dem heutigen: eine Rechnung von 2023
 * gehört auch 2026 noch ins Jahr 2023. Der Schrägstrich trennt die Ebenen, egal wie der
 * Server das intern macht — das rechnet der Server um.
 */
function MusterFeld({ kontoId, wert, onAendern }: {
  kontoId?: number; wert: string; onAendern: (v: string) => void;
}) {
  const [vorschau, setVorschau] = useState("");
  useEffect(() => {
    if (!kontoId || !wert) { setVorschau(""); return; }
    let abgebrochen = false;
    const id = setTimeout(() => {
      api.post<{ folder: string }>(`/mailbox/accounts/${kontoId}/archive-preview`,
                                   { archive_pattern: wert })
        .then((r) => { if (!abgebrochen) setVorschau(r.folder); })
        .catch(() => { if (!abgebrochen) setVorschau(""); });
    }, 300);
    return () => { abgebrochen = true; clearTimeout(id); };
  }, [kontoId, wert]);

  return (
    <div className="space-y-2">
      <Feld label="Muster" hinweis="Schrägstrich trennt Ebenen. Beispiel: Archive/{jahr}/{monat}">
        <input value={wert} className={`${EINGABE} font-mono`}
          onChange={(e) => onAendern(e.target.value)} placeholder="Archive/{jahr}" />
      </Feld>
      {vorschau && (
        <p className="text-xs text-muted">
          Eine Mail von heute landet in <code className="text-brand">{vorschau}</code>.
          Fehlende Ordner werden angelegt.
        </p>
      )}
      <p className="text-[11px] text-muted">
        Platzhalter: <code>{"{jahr}"}</code> <code>{"{jahr_kurz}"}</code>{" "}
        <code>{"{monat}"}</code> <code>{"{monatsname}"}</code> <code>{"{tag}"}</code>{" "}
        <code>{"{quartal}"}</code> <code>{"{kw}"}</code> <code>{"{absender}"}</code>{" "}
        <code>{"{absender_domain}"}</code>
      </p>
    </div>
  );
}

/**
 * Ein Ordner des Kontos — als Auswahl, sobald das Postfach erreichbar ist.
 *
 * Tippen hieße raten: Ordner heißen je nach Anbieter `Sent`, `Gesendet`, `INBOX.Sent` oder
 * `[Gmail]/Gesendet`, und ein Tippfehler fällt erst auf, wenn eine gesendete Mail nicht im
 * eigenen Postfach auftaucht. Steht die Verbindung noch nicht (neues Konto, falsches
 * Kennwort), bleibt das Textfeld — besser als ein leeres Auswahlfeld.
 */
function OrdnerFeld({ label, hinweis, wert, ordner, onWaehlen }: {
  label: string; hinweis?: string; wert: string;
  ordner: { name: string; display: string; level: number }[] | undefined;
  onWaehlen: (v: string) => void;
}) {
  if (!ordner?.length) {
    return (
      <Feld label={label} hinweis={hinweis}>
        <input value={wert} className={EINGABE} onChange={(e) => onWaehlen(e.target.value)} />
      </Feld>
    );
  }
  // Ein eingetragener Ordner, den es (nicht mehr) gibt, bleibt sichtbar statt still
  // verlorenzugehen — sonst ändert schon das Öffnen des Dialogs die Einstellung.
  const unbekannt = wert && !ordner.some((o) => o.name === wert);
  return (
    <Feld label={label} hinweis={hinweis}>
      <select value={wert} className={EINGABE} onChange={(e) => onWaehlen(e.target.value)}>
        <option value="">— keiner —</option>
        {unbekannt && <option value={wert}>{wert} (nicht gefunden)</option>}
        {ordner.map((o) => (
          <option key={o.name} value={o.name}>
            {"\u00a0".repeat(o.level * 2)}{o.display}
          </option>
        ))}
      </select>
    </Feld>
  );
}

/** Identitäten eines Kontos: wer als Absender auftritt. */
function Identitaeten({ kontoId, onFehler }: { kontoId: number; onFehler: (e: unknown) => void }) {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Partial<MailIdentitaet> | null>(null);
  const { data } = useQuery({
    queryKey: ["mail-identities", kontoId],
    queryFn: () => api.get<MailIdentitaet[]>(`/mailbox/accounts/${kontoId}/identities`),
  });
  const inv = () => qc.invalidateQueries({ queryKey: ["mail-identities", kontoId] });
  const speichern = useMutation({
    mutationFn: (i: Partial<MailIdentitaet>) => i.id
      ? api.put(`/mailbox/identities/${i.id}`, i)
      : api.post(`/mailbox/accounts/${kontoId}/identities`, i),
    onSuccess: () => { setDialog(null); inv(); }, onError: onFehler,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/mailbox/identities/${id}`),
    onSuccess: inv, onError: onFehler,
  });

  return (
    <div className="mt-2 border-t border-line pt-2.5">
      <Liste>
        {data?.map((i) => (
          <ListenZeile key={i.id}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 truncate">
                <span className="text-ink">{i.display_name || i.email}</span>
                {i.display_name && <span className="ml-2 text-xs text-muted">{i.email}</span>}
              </span>
              {i.is_default && <Etikett farbe="brand">Vorgabe</Etikett>}
              <Aktionen>
                <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")}
                  onClick={() => setDialog(i)} />
                <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                  onClick={() => loeschen.mutate(i.id)} />
              </Aktionen>
            </div>
          </ListenZeile>
        ))}
        {data?.length === 0 && <ListeLeer>Noch keine Identität — ohne sie kannst du nicht senden.</ListeLeer>}
      </Liste>
      <button onClick={() => setDialog({ email: "", display_name: "", is_default: !data?.length })}
        className="mt-2 rounded border border-line px-2 py-1 text-xs text-muted hover:border-brand hover:text-ink">
        + Identität
      </button>

      {dialog && (
        <Dialog titel={dialog.id ? "Identität" : "Identität anlegen"} onClose={() => setDialog(null)}
          fuss={<DialogFuss onAbbrechen={() => setDialog(null)} laeuft={speichern.isPending}
            deaktiviert={!dialog.email?.trim()} onSpeichern={() => speichern.mutate(dialog)} />}>
          <div className="space-y-3">
            <Feld label="Absender-Adresse"><input value={dialog.email || ""} className={EINGABE}
              onChange={(e) => setDialog({ ...dialog, email: e.target.value })} /></Feld>
            <Feld label="Angezeigter Name"><input value={dialog.display_name || ""} className={EINGABE}
              onChange={(e) => setDialog({ ...dialog, display_name: e.target.value })} /></Feld>
            <Feld label="Antwort an" hinweis="Leer lassen, wenn Antworten an die Absender-Adresse gehen sollen.">
              <input value={dialog.reply_to || ""} className={EINGABE}
                onChange={(e) => setDialog({ ...dialog, reply_to: e.target.value })} /></Feld>
            <Feld label="Signatur">
              <textarea value={dialog.signature || ""} rows={4} className={`${EINGABE} font-mono text-xs`}
                onChange={(e) => setDialog({ ...dialog, signature: e.target.value })} /></Feld>
            <label className="flex items-center gap-2 text-sm text-muted">
              <input type="checkbox" checked={!!dialog.is_default}
                onChange={(e) => setDialog({ ...dialog, is_default: e.target.checked })} />
              Vorgabe für dieses Konto
            </label>
          </div>
        </Dialog>
      )}
    </div>
  );
}
