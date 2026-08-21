import { tr } from "../i18n";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, holeFile } from "../api";
import { usePageChrome } from "../pageChrome";
import { useAuth } from "../auth";
import { formatDateTime } from "../lib/formatTime";
import { KontoDialog, type MailKonto, type MailIdentity } from "../components/MailKontenPanel";
import {
  Area, ConfirmDialog, Dialog, DialogFuss, INPUT_VALUE, Etikett, Field, Fehlerzeile,
  IconButton, Button, BUTTON, Listing, ListingLeer, ListenLine, Reiter, Zeilenknopf, BUTTON_TEXT} from "../components/ui";

/**
 * Das Postfach.
 *
 * Drei Spalten wie in jedem Mail-Programm, und aus demselben Grund: Ordner ändern sich
 * selten, die Liste oft, die Nachricht bei jedem Klick. Was Traccoon dazugibt, steht am
 * Ende einer Mail und an jedem Anhang — die **Aktionen**: ein Knopf startet einen Ablauf und
 * legt Konto, Ordner, UID und den gewählten Anhang in dessen Kontext. „Anhang nach
 * Paperless" ist damit kein Sonderfall im Code, sondern ein Ablauf im Editor.
 */
interface Header {
  uid: number; subject: string; from: string; date: string; size: number;
  seen: boolean; flagged: boolean; answered: boolean; has_attachment: boolean;
}
interface Adresse { name: string; addr: string }
interface Attachment { index: number; filename: string; content_type: string; size: number }
interface Message {
  uid: number; folder: string; subject: string; from: Adresse[]; to: Adresse[]; cc: Adresse[];
  reply_to: Adresse[];
  date: string; message_id: string; text: string; html: string; remote_images: boolean;
  attachments: Attachment[]; seen: boolean; flagged: boolean;
}
interface Folder {
  name: string; display: string; level: number; parent: string; delimiter: string;
  special: string; unseen: number; total: number;
}
interface Action { definition_id: number; key: string; name: string; description: string; scope: string }

const SONDER: Record<string, string> = {
  sent: "📤", drafts: "📝", trash: "🗑", junk: "🚫", archive: "📦",
};

export default function Mail() {
  usePageChrome("Mail", []);
  const qc = useQueryClient();
  const { user } = useAuth();
  const [kontoId, setKontoId] = useState<number | null>(null);
  const [folder, setFolder] = useState("INBOX");
  const [uid, setUid] = useState<number | null>(null);
  const [suche, setSuche] = useState("");
  const [question, setQuestion] = useState("");
  const [err, setErr] = useState("");
  const [verfassen, setVerfassen] = useState<null | Record<string, string>>(null);
  const [settings, setSettings] = useState<MailKonto | null>(null);

  const { data: konten } = useQuery({
    queryKey: ["mail-accounts"], queryFn: () => api.get<MailKonto[]>("/mailbox/accounts") });
  // Damit man sieht, wo Post liegt, ohne hineinzugehen. Selten abgefragt: dahinter steckt je
  // Postfach eine IMAP-Verbindung.
  const { data: ungelesen } = useQuery({
    queryKey: ["mail-unread"],
    queryFn: () => api.get<{ accounts: { account_id: number; unseen: number | null }[] }>(
      "/mailbox/unread"),
    // Beim Zurückwechseln in den Tab sofort nachsehen: wer eine Minute weg war, will nicht
    // bis zur nächsten Runde warten. Global ist das aus, für Post ist es richtig.
    refetchInterval: 60_000, refetchOnWindowFocus: true, retry: false,
  });
  useEffect(() => {
    if (kontoId !== null || !konten?.length) return;
    // Zuletzt geöffnetes Postfach zuerst — es steht am Menschen und gilt deshalb auch nach
    // einer neuen Anmeldung und auf einem anderen Rechner.
    const gemerkt = konten.find((k) => k.id === user?.mail_last_account_id);
    setKontoId((gemerkt || konten.find((k) => k.enabled) || konten[0]).id);
  }, [konten, kontoId, user]);

  const kontoWechseln = (id: number) => {
    setKontoId(id);
    setFolder("INBOX");
    setUid(null);
    api.post(`/mailbox/accounts/${id}/last`, {}).catch(() => {/* Merken ist kein Muss */});
  };

  const { data: folderListing } = useQuery({
    queryKey: ["mail-folders", kontoId], enabled: !!kontoId,
    queryFn: () => api.get<Folder[]>(`/mailbox/accounts/${kontoId}/folders?counts=true`),
    refetchInterval: 60_000, refetchOnWindowFocus: true,
  });

  if (!konten?.length) {
    return (
      <Area hinweis="Noch kein Postfach hinterlegt.">
        <p className="text-sm text-muted">
          Konten und Identitäten stehen im Konto unter <b>Mail-Konten</b>.
        </p>
      </Area>
    );
  }

  return (
    <div className="space-y-3">
      <Fehlerzeile text={err} />
      {/* Eine Zeile für alles, was zum Postfach gehört: welches, seine Einstellungen, und
          die einzige Handlung, die nicht von einer Nachricht ausgeht. */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">Postfach</span>
        <select value={kontoId ?? ""} onChange={(e) => kontoWechseln(Number(e.target.value))}
          className={`${INPUT_VALUE} max-w-[16rem]`}>
          {konten.map((k) => {
            const open = ungelesen?.accounts.find((a) => a.account_id === k.id)?.unseen;
            return (
              <option key={k.id} value={k.id}>
                {k.name}{k.enabled ? "" : " (aus)"}{open ? ` — ${open} neu` : ""}
              </option>
            );
          })}
        </select>
        <IconButton icon="⟳" titel="Jetzt nachsehen"
          onClick={() => {
            qc.invalidateQueries({ queryKey: ["mail-unread"] });
            qc.invalidateQueries({ queryKey: ["mail-folders"] });
            qc.invalidateQueries({ queryKey: ["mail-list"] });
          }} />
        <IconButton icon="⚙" titel="Einstellungen dieses Postfachs"
          onClick={() => setSettings(konten.find((k) => k.id === kontoId) || null)} />
        {/* Die anderen Postfächer mit neuer Post — sichtbar, ohne das Auswahlfeld zu öffnen,
            und ein Klick springt hin. Wer nichts liegen hat, taucht hier nicht auf: eine
            Reihe von Nullen wäre keine Auskunft, sondern Tapete. */}
        {konten.filter((k) => {
          const open = ungelesen?.accounts.find((a) => a.account_id === k.id)?.unseen;
          return k.id !== kontoId && !!open;
        }).map((k) => (
          <button key={k.id} onClick={() => kontoWechseln(k.id)}
            title={`Zu „${k.name}" wechseln`}
            className="flex shrink-0 items-center gap-1.5 rounded border border-brand/40 bg-brand/15 px-2 py-1 text-xs text-brand transition-colors hover:bg-brand/25">
            {k.name}
            <span className="rounded-full bg-brand px-1.5 text-[11px] text-white tabular-nums">
              {ungelesen?.accounts.find((a) => a.account_id === k.id)?.unseen}
            </span>
          </button>
        ))}
        {/* Die Suche gehört zum Postfach, nicht zur Liste darunter: sie gilt für den ganzen
            Ordner und bleibt auch dann sichtbar, wenn rechts eine Nachricht offen ist. */}
        <form onSubmit={(e) => { e.preventDefault(); setSuche(question); setUid(null); }}
              className="flex min-w-0 flex-1 items-center gap-2">
          <input value={question} onChange={(e) => setQuestion(e.target.value)}
            placeholder="Suchen (Volltext)" className={`${INPUT_VALUE} min-w-0 max-w-md flex-1`} />
          {suche && (
            <Zeilenknopf onClick={() => { setQuestion(""); setSuche(""); }}>
              zurücksetzen
            </Zeilenknopf>
          )}
        </form>
        <button onClick={() => setVerfassen({})}
          className={BUTTON.haupt}>
          ✉️ Verfassen
        </button>
      </div>

      {/* Ab `sm` nebeneinander: die Ordnerspalte braucht keine 300 px, und untereinander
          schiebt sie die Nachrichtenliste unter den Bildschirmrand — genau das, wofür man
          ein Mail-Programm nicht aufmacht. */}
      {/* Zwei Zustände, eine Anordnung: ohne geöffnete Mail liegt die Liste rechts und darf
          breit sein. Ist eine Mail offen, rückt die Liste unter die Ordner — man bleibt in
          der Übersicht, springt zur nächsten Mail und liest daneben weiter, statt zwischen
          zwei Ansichten hin und her zu wechseln. */}
      <div className="flex flex-col gap-3 sm:flex-row">
        <div className={`sm:shrink-0 ${uid === null ? "sm:w-48 lg:w-56" : "sm:w-72 lg:w-80"}`}>
          <div className="space-y-3">
            <Area>
              <FolderBaum ordner={folderListing} aktiv={folder}
                onWaehlen={(n) => { setFolder(n); setUid(null); setSuche(""); setQuestion(""); }} />
              {/* Handgriffe am GEWÄHLTEN Ordner. Sie stehen unter dem Baum und nicht in
                  jeder Zeile: gebraucht werden sie selten, und ein Löschknopf neben jedem
                  Ordner ist ein Löschknopf zu viel. */}
              {kontoId && (
                <FolderHandgriffe kontoId={kontoId} ordner={folder}
                  onGeloescht={() => { setFolder("INBOX"); setUid(null); }}
                  onFehler={setErr} />
              )}
            </Area>
            {uid !== null && (
              <MessagesListing kontoId={kontoId!} ordner={folder} suche={suche}
                onOeffnen={setUid} onFehler={setErr} offen={uid} schmal />
            )}
          </div>
        </div>

        <div className="min-w-0 flex-1 space-y-3">
          {uid === null ? (
            <MessagesListing kontoId={kontoId!} ordner={folder} suche={suche}
              onOeffnen={setUid} onFehler={setErr} />
          ) : (
            <Leseansicht kontoId={kontoId!} konto={konten.find((k) => k.id === kontoId)}
              ordner={folder} uid={uid} onZurueck={() => setUid(null)}
              onAntworten={(f) => setVerfassen(f)} onFehler={setErr} />
          )}
        </div>
      </div>

      {verfassen && kontoId && (
        <VerfassenDialog kontoId={kontoId} start={verfassen} onClose={() => setVerfassen(null)}
          onFehler={setErr} />
      )}
      {settings && (
        <KontoSettings konto={settings} onClose={() => setSettings(null)}
          onFehler={setErr} />
      )}
    </div>
  );
}

/**
 * Die Einstellungen des offenen Postfachs — derselbe Dialog wie im Konto.
 *
 * Zweimal zu bauen hieße, ihn zweimal zu pflegen: Ordner, Kennwörter und das Archiv-Muster
 * gehören zusammen, egal von welcher Seite man sie aufruft.
 */
function KontoSettings({ konto, onClose, onFehler: onError }: {
  konto: MailKonto; onClose: () => void; onFehler: (m: string) => void;
}) {
  const qc = useQueryClient();
  const speichern = useMutation({
    mutationFn: (f: any) => api.put(`/mailbox/accounts/${konto.id}`, f),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mail-accounts"] });
      qc.invalidateQueries({ queryKey: ["mail-folders"] });
      onClose();
    },
    onError: (e) => onError(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen"),
  });
  return (
    <KontoDialog start={{ ...konto, imap_password: "", smtp_password: "" } as any}
      fehler="" laeuft={speichern.isPending} onClose={onClose}
      onSpeichern={(f) => speichern.mutate(f)} />
  );
}

/**
 * Die Ordner als Baum — mit Einrückung, Aufklappen und Ungelesen-Zahl.
 *
 * Eine flache Liste reicht, solange jemand fünf Ordner hat. Bei einem gewachsenen Postfach
 * mit Archiv nach Jahren, Projekten und Verteilern ist sie eine Wand: Man sucht den Ordner,
 * den man sucht, und findet ihn zwischen dreißig gleich aussehenden Zeilen nicht wieder.
 * Zugeklappt wird der Zweig, nicht der Zugriff — ein Klick auf den Elternordner öffnet ihn
 * trotzdem.
 */
function FolderBaum({ ordner: folder, aktiv, onWaehlen }: {
  ordner: Folder[] | undefined; aktiv: string; onWaehlen: (name: string) => void;
}) {
  // Zugeklappt beginnen: ein gewachsenes Postfach hat Archive nach Jahren und Verteiler nach
  // Absendern, und die will man beim Öffnen nicht alle sehen. Was man täglich braucht, sind
  // die sechs Sonderordner — der Rest ist einen Klick entfernt.
  const [auf, setAuf] = useState<Set<string>>(new Set());
  if (!folder) return <Listing><ListingLeer>Ordner werden geladen…</ListingLeer></Listing>;

  const hatKinder = (o: Folder) => folder.some((k) => k.parent === o.name);
  /**
   * Ungelesenes eines Zweiges: der Ordner selbst und alles darunter.
   *
   * Ohne das bliebe ein zugeklappter Zweig stumm — man sähe „Archive" ohne Zahl und wüsste
   * nicht, dass in `Archive/2026/08` etwas Ungelesenes liegt. Deshalb steht am zugeklappten
   * Ordner die Summe und am aufgeklappten nur das Eigene: sonst zählte man dieselbe
   * Nachricht in jeder Ebene noch einmal.
   */
  const sum_total = (o: Folder): number => folder
    .filter((k) => k.parent === o.name)
    .reduce((zahl, k) => zahl + sum_total(k), o.unseen || 0);
  // Sichtbar ist, wessen Vorfahren alle aufgeklappt sind. Der aktive Ordner bleibt es immer
  // — sonst verschwände unter den Füßen, worin man gerade liest.
  const visible = (o: Folder) => {
    if (o.name === aktiv || !o.parent) return true;
    let eltern = o.parent;
    while (eltern) {
      if (!auf.has(eltern)) return false;
      eltern = folder.find((k) => k.name === eltern)?.parent || "";
    }
    return true;
  };
  const umschalten = (name: string) => {
    const fresh = new Set(auf);
    fresh.has(name) ? fresh.delete(name) : fresh.add(name);
    setAuf(fresh);
  };

  return (
    <Listing>
      {folder.filter(visible).map((o) => (
        <ListenLine key={o.name} dicht onClick={() => onWaehlen(o.name)}>
          {/* Feste Spalten statt Flex mit Platzhaltern: nur so steht das Ordnersymbol jeder
              Zeile an derselben Stelle, egal ob davor ein Klapp-Pfeil sitzt oder nicht. */}
          <div className="grid grid-cols-[0.75rem_1.25rem_minmax(0,1fr)_auto] items-center gap-1.5"
               style={{ paddingLeft: `${o.level * 0.85}rem` }}>
            {hatKinder(o) ? (
              <button onClick={(e) => { e.stopPropagation(); umschalten(o.name); }}
                className={BUTTON_TEXT.neben}
                title={auf.has(o.name) ? "zuklappen" : "aufklappen"}>
                {auf.has(o.name) ? "▼" : "▶"}
              </button>
            ) : <span />}
            <span className="text-center leading-none">{SONDER[o.special] || "📁"}</span>
            <span className={`min-w-0 truncate ${
              o.name === aktiv ? "font-medium text-brand"
                : sum_total(o) ? "font-medium text-ink" : ""}`}>
              {o.display}
            </span>
            {(() => {
              const zu = hatKinder(o) && !auf.has(o.name);
              const zahl = zu ? sum_total(o) : o.unseen;
              if (!zahl) return <span />;
              // Zugeklappt und nur in den Kindern etwas: die Zahl gehört dem Zweig, nicht dem
              // Ordner — leiser dargestellt, damit man den Unterschied sieht.
              const nurKinder = zu && !o.unseen;
              return (
                <Etikett farbe={nurKinder ? "neutral" : "brand"}
                  titel={nurKinder ? "in Unterordnern" : "ungelesen"}>
                  {zahl}
                </Etikett>
              );
            })()}
          </div>
        </ListenLine>
      ))}
    </Listing>
  );
}

/** Was man mit einem ganzen Ordner tun kann — beides mit Rückfrage, beides selten. */
function FolderHandgriffe({ kontoId, ordner: folder, onGeloescht, onFehler: onError }: {
  kontoId: number; ordner: string; onGeloescht: () => void; onFehler: (m: string) => void;
}) {
  const qc = useQueryClient();
  const [question, setQuestion] = useState<"gelesen" | "loeschen" | null>(null);
  const [notice, setNotice] = useState("");
  const schiefgelaufen = (was: string) => (e: unknown) =>
    onError(e instanceof ApiError ? e.message : `${was} fehlgeschlagen`);
  const auffrischen = () => {
    qc.invalidateQueries({ queryKey: ["mail-folders"] });
    qc.invalidateQueries({ queryKey: ["mail-list"] });
  };

  const gelesen = useMutation({
    mutationFn: () => api.post<{ marked: number }>(
      `/mailbox/accounts/${kontoId}/folders/read-all`, { folder: folder }),
    onSuccess: (r) => {
      setQuestion(null);
      setNotice(r.marked ? `${r.marked} Nachrichten als gelesen markiert` : "Nichts war ungelesen");
      auffrischen();
    },
    onError: (e) => { setQuestion(null); schiefgelaufen("Markieren")(e); },
  });
  const remove = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${kontoId}/folders/delete`, { folder: folder }),
    onSuccess: () => { setQuestion(null); auffrischen(); onGeloescht(); },
    onError: (e) => { setQuestion(null); schiefgelaufen("Löschen")(e); },
  });

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Zeilenknopf onClick={() => setQuestion("gelesen")}>✓ Alle gelesen</Zeilenknopf>
        <Zeilenknopf gefahr onClick={() => setQuestion("loeschen")}>🗑 Ordner löschen</Zeilenknopf>
      </div>
      {notice && <div className="text-xs text-green-400">{notice}</div>}

      {question === "gelesen" && (
        <ConfirmDialog
          titel="Alle als gelesen markieren?"
          text={`Alles Ungelesene in „${folder}" wird auf gelesen gesetzt.`}
          hinweis="Rückgängig geht das nur Nachricht für Nachricht."
          gefahr={false} bestaetigenText="Markieren" laeuft={gelesen.isPending}
          onClose={() => setQuestion(null)} onBestaetigen={() => gelesen.mutate()} />
      )}
      {question === "loeschen" && (
        <ConfirmDialog
          titel={`Ordner „${folder}" löschen?`}
          text="Der Ordner und alles darin verschwindet — auf dem Server, nicht nur hier."
          hinweis="Das ist endgültig. Sonderordner (Posteingang, Gesendet, Entwürfe, Papierkorb, Spam) sind geschützt."
          bestaetigenText="Endgültig löschen" laeuft={remove.isPending}
          onClose={() => setQuestion(null)} onBestaetigen={() => remove.mutate()} />
      )}
    </>
  );
}

/**
 * HTML einer fremden Mail anzeigen, ohne ihr das Fenster zu überlassen.
 *
 * Drei Schlösser übereinander: der Server hat schon gesäubert (nh3), der Rahmen hier ist ein
 * `sandbox`-iframe ohne Skriptrecht, und eine Inhaltsrichtlinie im Dokument selbst lässt
 * nichts nachladen. Fernbilder hängen als `data-fern` in der Mail und werden erst auf Klick
 * zu `src` — ein geladenes Bild ist eine Rückmeldung an den Absender, dass gelesen wurde.
 */
function HtmlAnsicht({ html, fernbilder }: { html: string; fernbilder: boolean }) {
  const [bilder, setBilder] = useState(false);
  const inhalt = bilder ? html.replace(/data-fern="/g, 'src="') : html;
  const richtlinie = "default-src 'none'; style-src 'unsafe-inline'; font-src data:; "
    + (bilder ? "img-src data: https:;" : "img-src data:;");
  const dokument = `<!doctype html><html><head>
      <meta http-equiv="Content-Security-Policy" content="${richtlinie}">
      <base target="_blank">
      <style>
        body { font: 14px/1.5 system-ui, sans-serif; color: #c9d1d9; background: #0d1117;
               margin: 12px; word-break: break-word; }
        a { color: #58a6ff; } img { max-width: 100%; height: auto; }
        table { max-width: 100%; } blockquote { border-left: 2px solid #30363d;
               margin: 0; padding-left: 12px; color: #8b949e; }
      </style></head><body>${inhalt}</body></html>`;

  return (
    <div className="space-y-2">
      {fernbilder && !bilder && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          Bilder von fremden Servern wurden nicht geladen — das würde dem Absender verraten,
          dass du die Mail gelesen hast.
          <Zeilenknopf onClick={() => setBilder(true)}>Bilder laden</Zeilenknopf>
        </div>
      )}
      <iframe
        title="Nachricht"
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        srcDoc={dokument}
        className="h-[60vh] w-full rounded border border-line bg-surface"
      />
    </div>
  );
}

function MessagesListing({ kontoId, ordner: folder, suche, onOeffnen: onOpen_it, onFehler: onError,
                           offen: open, schmal = false }: {
  kontoId: number; ordner: string; suche: string;
  onOeffnen: (uid: number) => void; onFehler: (m: string) => void;
  offen?: number; schmal?: boolean;
}) {
  const [page, setPage] = useState(0);
  const limit = 50;
  useEffect(() => { setPage(0); }, [folder, suche]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["mail-list", kontoId, folder, suche, page],
    queryFn: () => api.get<{ total: number; messages: Header[] }>(
      `/mailbox/accounts/${kontoId}/messages?folder=${encodeURIComponent(folder)}`
      + `&q=${encodeURIComponent(suche)}&offset=${page * limit}&limit=${limit}`),
    // Not before an account is picked: `kontoId` is null on the first render, and the
    // request went out as `accounts/null/messages` — a 422 on every visit to the page.
    enabled: !!kontoId,
    // Neue Post soll in der Liste auftauchen, nicht nur im Zähler daneben.
    refetchInterval: 60_000, refetchOnWindowFocus: true,
  });
  useEffect(() => {
    if (error) onError(error instanceof ApiError ? error.message : "Postfach nicht erreichbar");
  }, [error]);

  return (
    <Area
      titel={folder}
      werkzeuge={<>
        {suche && <Etikett farbe="brand">Suche: {suche}</Etikett>}
        <div className="flex-1" />
        <span className="text-xs text-muted">
          {data?.total ?? 0} {suche ? "Treffer" : "Nachrichten"}
        </span>
      </>}
    >
      {/* Schmal heißt: die Liste steht neben der geöffneten Mail und scrollt für sich. Ohne
          eigene Höhe würde die Seite so lang wie das Postfach. */}
      <div className={schmal ? "max-h-[55vh] overflow-y-auto" : ""}>
      <Listing>
        {data?.messages.map((m) => (
          <ListenLine key={m.uid} dicht={schmal} onClick={() => onOpen_it(m.uid)}>
            <div className={`flex flex-wrap items-baseline gap-x-3 gap-y-1 ${
              m.uid === open ? "text-brand" : ""}`}>
              <span className={`min-w-0 flex-1 truncate ${
                m.uid === open ? "font-medium" : m.seen ? "text-ink" : "font-semibold text-ink"}`}>
                {m.subject || "(kein Betreff)"}
              </span>
              {!m.seen && <Etikett farbe="brand">neu</Etikett>}
              {m.has_attachment && <span title="hat einen Anhang">📎</span>}
              {m.flagged && <span title="markiert">⭐</span>}
              {m.answered && <span title="beantwortet">↩</span>}
              <span className="shrink-0 text-xs text-muted">{formatDateTime(m.date)}</span>
            </div>
            <div className="mt-0.5 truncate text-xs text-muted">{m.from}</div>
          </ListenLine>
        ))}
        {isLoading && <ListingLeer>Wird geladen…</ListingLeer>}
        {!isLoading && !data?.messages.length && <ListingLeer>Nichts in diesem Ordner.</ListingLeer>}
      </Listing>
      </div>
      {(data?.total ?? 0) > limit && (
        <div className="flex items-center gap-2">
          <Zeilenknopf onClick={() => setPage(Math.max(0, page - 1))}>← neuer</Zeilenknopf>
          <span className="text-xs text-muted">
            {page * limit + 1}–{Math.min((page + 1) * limit, data!.total)} von {data!.total}
          </span>
          <Zeilenknopf onClick={() => setPage(page + 1)}>älter →</Zeilenknopf>
        </div>
      )}
    </Area>
  );
}

/**
 * Ein Anhang zum Ansehen.
 *
 * Vorher stand dort ein Link auf die API-Adresse — und der Browser schickt den Token nicht
 * mit, den er nicht kennt. Was ankam, war „Not authenticated". Jetzt wird die Datei mit
 * Anmeldung geholt und hier gezeigt; was sich nicht zeigen lässt, kann man immer noch
 * speichern.
 *
 * Die Blob-Adresse wird beim Schließen wieder freigegeben: Sonst hält jeder angesehene
 * Anhang seinen Speicher, bis die Seite neu lädt.
 */
function AttachmentDialog({ pfad: path, anhang: attachment, onClose }: {
  pfad: string; anhang: Attachment; onClose: () => void;
}) {
  const [source, setSource] = useState("");
  const [kind, setKind] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let adresse = "";
    let lebt = true;
    holeFile(path)
      .then(async ({ blob, typ: t }) => {
        if (!lebt) return;
        setKind(t);
        // Text wird gelesen, nicht eingebettet: In einem Rahmen stünde er ohne Umbruch und
        // mit der Schrift der Seite, die er nicht meint.
        if (t.startsWith("text/") || t.includes("json")) setText(await blob.text());
        else {
          adresse = URL.createObjectURL(blob);
          setSource(adresse);
        }
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Anhang nicht ladbar"));
    return () => { lebt = false; if (adresse) URL.revokeObjectURL(adresse); };
  }, [path]);

  const bild = kind.startsWith("image/");
  const pdf = kind.includes("pdf");
  return (
    <Dialog breit titel={`📎 ${attachment.filename}`} onClose={onClose} fuss={
      <>
        <Button onClick={onClose}>{tr("common.schliessen")}</Button>
        {source && (
          <a href={source} download={attachment.filename} className={BUTTON.haupt}>
            {tr("mail.anhang_speichern")}
          </a>
        )}
      </>
    }>
      <Fehlerzeile text={error} />
      {!error && !source && !text && (
        <div className="p-6 text-center text-sm text-muted">{tr("common.laedt")}</div>
      )}
      {bild && source && (
        <img src={source} alt={attachment.filename} className="mx-auto max-h-[70vh] rounded" />
      )}
      {pdf && source && (
        <iframe src={source} title={attachment.filename} className="h-[70vh] w-full rounded bg-white" />
      )}
      {text && (
        <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded bg-surface p-3
          text-xs text-ink">{text}</pre>
      )}
      {source && !bild && !pdf && (
        <div className="p-6 text-center text-sm text-muted">
          {tr("mail.anhang_keine_vorschau", { typ: kind })}
        </div>
      )}
    </Dialog>
  );
}

function Leseansicht({ kontoId, konto, ordner: folder, uid, onZurueck: onBack, onAntworten, onFehler: onError }: {
  kontoId: number; konto: MailKonto | undefined; ordner: string; uid: number;
  onZurueck: () => void; onAntworten: (f: Record<string, string>) => void;
  onFehler: (m: string) => void;
}) {
  const [moveOpen, setMoveOpen] = useState(false);
  const [attachmentAuf, setAttachmentAuf] = useState<Attachment | null>(null);
  const qc = useQueryClient();
  const [lauf, setLauf] = useState("");
  const [ansicht, setAnsicht] = useState<"html" | "text">("html");
  const basis = `/mailbox/accounts/${kontoId}/messages/${uid}`;
  const { data: m, error } = useQuery({
    queryKey: ["mail-message", kontoId, folder, uid],
    queryFn: () => api.get<Message>(`${basis}?folder=${encodeURIComponent(folder)}`),
  });
  const { data: actions } = useQuery({
    queryKey: ["mail-actions"], queryFn: () => api.get<Action[]>("/mailbox/actions"),
    staleTime: 5 * 60_000,
  });
  // Für „Verschieben nach…": dieselbe Abfrage wie die Ordnerspalte, also aus dem Zwischen-
  // speicher und ohne zweiten Gang zum Postfach.
  const { data: allFolder } = useQuery({
    queryKey: ["mail-folders", kontoId],
    queryFn: () => api.get<Folder[]>(`/mailbox/accounts/${kontoId}/folders?counts=true`),
  });
  const { data: identitaeten } = useQuery({
    queryKey: ["mail-identities", kontoId],
    queryFn: () => api.get<MailIdentity[]>(`/mailbox/accounts/${kontoId}/identities`),
  });

  /**
   * Empfänger einer Antwort.
   *
   * `allen` nimmt zusätzlich alle mit, die schon dabei waren — abzüglich der eigenen
   * Adressen, denn sich selbst zu antworten ist der Klassiker, den man erst nach dem
   * Absenden bemerkt. Steht ein `Reply-To` in der Mail, gilt das vor dem Absender: genau
   * dafür ist es da.
   */
  const answerFields = (allen: boolean): Record<string, string> => {
    const eigene = new Set((identitaeten || []).map((i) => i.email.toLowerCase()));
    const adressen = (listing: Adresse[] | undefined) =>
      (listing || []).map((a) => a.addr).filter((a) => a && !eigene.has(a.toLowerCase()));

    const answerAn = adressen(m?.reply_to?.length ? m.reply_to : m?.from);
    const an = allen
      ? Array.from(new Set([...answerAn, ...adressen(m?.to)]))
      : answerAn;
    const kopie = allen ? Array.from(new Set(adressen(m?.cc))) : [];
    return {
      to: an.join(", "),
      cc: kopie.join(", "),
      subject: `Re: ${m?.subject || ""}`,
      in_reply_to: m?.message_id || "",
      text: `\n\n> ${(m?.text || "").split("\n").join("\n> ")}`,
      identity: String(passendeIdentity() ?? ""),
    };
  };

  /** Gäbe „Allen antworten" mehr Adressen als „Antworten"? Nur dann lohnt der Knopf. */
  const mehrRecipient = (): boolean => {
    const eigene = new Set((identitaeten || []).map((i) => i.email.toLowerCase()));
    const fremde = (listing: Adresse[] | undefined) =>
      (listing || []).map((a) => a.addr.toLowerCase()).filter((a) => a && !eigene.has(a));
    const answerAn = new Set(fremde(m?.reply_to?.length ? m.reply_to : m?.from));
    const all = new Set([...answerAn, ...fremde(m?.to), ...fremde(m?.cc)]);
    return all.size > answerAn.size;
  };

  /**
   * Die Identität, unter der geantwortet wird: die, an die die Mail ging.
   *
   * Wer als Kassenwart angeschrieben wird, antwortet als Kassenwart — nicht unter der
   * Adresse, die zufällig als Vorgabe eingetragen ist. Gesucht wird in allen Empfängerfeldern
   * der ursprünglichen Nachricht; findet sich nichts (Verteiler, Alias, den es hier nicht
   * gibt), bleibt es bei der Vorgabe des Kontos.
   */
  const passendeIdentity = (): number | undefined => {
    const recipient = [...(m?.to || []), ...(m?.cc || [])]
      .map((a) => a.addr.toLowerCase());
    const hits = (identitaeten || []).find((i) => recipient.includes(i.email.toLowerCase()));
    return hits?.id;
  };
  useEffect(() => {
    if (error) onError(error instanceof ApiError ? error.message : "Nachricht nicht lesbar");
  }, [error]);

  const start = useMutation({
    mutationFn: (v: { definition_id: number; attachment?: number }) =>
      api.post<{ instance_id: number }>(`${basis}/action`, { ...v, folder: folder }),
    onSuccess: (r) => setLauf(`Ablauf gestartet (Vorgang ${r.instance_id})`),
    onError: (e) => onError(e instanceof ApiError ? e.message : "Aktion fehlgeschlagen"),
  });
  // Alle Handgriffe enden gleich: Liste und Ordnerzahlen stimmen nicht mehr, und die
  // Nachricht ist nicht mehr da, wo man sie gerade gelesen hat — also zurück zur Liste.
  const danach = () => {
    qc.invalidateQueries({ queryKey: ["mail-list"] });
    qc.invalidateQueries({ queryKey: ["mail-folders"] });
    onBack();
  };
  const schiefgelaufen = (was: string) => (e: unknown) =>
    onError(e instanceof ApiError ? e.message : `${was} fehlgeschlagen`);

  const move = useMutation({
    mutationFn: (target: string) => api.post(`${basis}/move`, { folder: folder, target }),
    onSuccess: danach, onError: schiefgelaufen("Verschieben"),
  });
  const archivieren = useMutation({
    mutationFn: () => api.post<{ folder: string }>(`${basis}/archive`, { folder: folder }),
    onSuccess: danach, onError: schiefgelaufen("Archivieren"),
  });
  const asSpam = useMutation({
    mutationFn: () => api.post(`${basis}/spam`, { folder: folder }),
    onSuccess: danach, onError: schiefgelaufen("Als Spam markieren"),
  });
  const keinSpam = useMutation({
    mutationFn: () => api.post(`${basis}/not-spam`, { folder: folder }),
    onSuccess: danach, onError: schiefgelaufen("Zurückholen"),
  });
  const remove = useMutation({
    mutationFn: () => api.post(`${basis}/delete`, { folder: folder }),
    onSuccess: danach, onError: schiefgelaufen("Löschen"),
  });

  const fuerMail = (actions || []).filter((a) => a.scope !== "attachment");
  const fuerAttachment = (actions || []).filter((a) => a.scope === "attachment");

  return (
    <Area
      titel={m?.subject || "…"}
      werkzeuge={<>
        <Zeilenknopf onClick={onBack}>← Liste</Zeilenknopf>
        <Zeilenknopf onClick={() => onAntworten(answerFields(false))}>Antworten</Zeilenknopf>
        {/* Nur wenn er wirklich etwas anderes tut: gezählt wird, was nach Abzug der eigenen
            Adressen übrig bleibt. Sonst stünde bei einer Mail, die an mich und eine zweite
            eigene Adresse ging, ein Knopf, der dasselbe macht wie sein Nachbar. */}
        {mehrRecipient() && (
          <Zeilenknopf onClick={() => onAntworten(answerFields(true))}>
            Allen antworten
          </Zeilenknopf>
        )}
        <Zeilenknopf onClick={() => onAntworten({
          identity: String(passendeIdentity() ?? ""),
          subject: `Fwd: ${m?.subject || ""}`,
          text: `\n\n--- Weitergeleitete Nachricht ---\n`
            + `Von: ${(m?.from || []).map((a) => a.addr).join(", ")}\n`
            + `Datum: ${m?.date || ""}\nBetreff: ${m?.subject || ""}\n\n${m?.text || ""}`,
        })}>Weiterleiten</Zeilenknopf>
        {/* Archiv und Spam erscheinen nur, wenn am Konto ein Ziel dafür steht — ein Knopf,
            der beim Drücken erklärt, dass er nicht kann, ist keiner. */}
        {(konto?.archive_mode === "pattern" ? konto?.archive_pattern : konto?.folder_archive) && (
          <Zeilenknopf onClick={() => archivieren.mutate()}>📦 Archivieren</Zeilenknopf>
        )}
        {/* Im Spam-Ordner ist „als Spam markieren" keine Handlung, sondern eine
            Wiederholung. Was dort fehlt, ist der Widerspruch. */}
        {konto?.folder_junk && (folder === konto.folder_junk ? (
          <Zeilenknopf onClick={() => keinSpam.mutate()} titel={tr("mail.kein_spam_titel")}>
            ✅ {tr("mail.kein_spam")}
          </Zeilenknopf>
        ) : (
          <Zeilenknopf onClick={() => asSpam.mutate()}>🚫 Spam</Zeilenknopf>
        ))}
        <Zeilenknopf onClick={() => setMoveOpen(true)}>📁 Verschieben</Zeilenknopf>
        <div className="flex-1" />
        <Zeilenknopf gefahr onClick={() => remove.mutate()}>🗑 Löschen</Zeilenknopf>
      </>}
    >
      {m && (
        <>
          {/* Zwei Zeilen statt vier: wer geschrieben hat und wann, ist die Frage beim
              Öffnen — an wen und in Kopie liest man nur nach, wenn man antwortet. Die volle
              Liste steht im Tooltip, damit Kürzen nichts verschluckt. */}
          <div className="space-y-0.5">
            <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
              <span className="font-medium text-ink">
                {m.from[0]?.name || m.from[0]?.addr || "—"}
              </span>
              {m.from[0]?.name && (
                <span className="text-xs text-muted">{m.from[0]?.addr}</span>
              )}
              <div className="flex-1" />
              <span className="shrink-0 text-xs text-muted" title={m.date}>
                {formatDateTime(m.date)}
              </span>
            </div>
            <div className="truncate text-xs text-muted"
                 title={[`An: ${m.to.map((a) => a.addr).join(", ") || "—"}`,
                         m.cc.length ? `Kopie: ${m.cc.map((a) => a.addr).join(", ")}` : ""]
                        .filter(Boolean).join("\n")}>
              An {m.to.map((a) => a.addr).join(", ") || "—"}
              {m.cc.length > 0 && <> · Kopie {m.cc.map((a) => a.addr).join(", ")}</>}
            </div>
          </div>

          {m.attachments.length > 0 && (
            <Listing>
              {m.attachments.map((a) => (
                <ListenLine key={a.index}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="min-w-0 flex-1 truncate">📎 {a.filename}</span>
                    <Etikett>{Math.max(1, Math.round(a.size / 1024))} kB</Etikett>
                    <Zeilenknopf onClick={() => setAttachmentAuf(a)}
                      titel={tr("mail.anhang_ansehen")}>
                      {tr("mail.anhang_ansehen")}
                    </Zeilenknopf>
                    {fuerAttachment.map((akt) => (
                      <Zeilenknopf key={akt.definition_id}
                        onClick={() => start.mutate({ definition_id: akt.definition_id,
                                                        attachment: a.index })}>
                        {akt.name}
                      </Zeilenknopf>
                    ))}
                  </div>
                </ListenLine>
              ))}
            </Listing>
          )}

          {m.html ? (
            <div className="space-y-2">
              <Reiter aktiv={ansicht} onWaehlen={setAnsicht} auswahl={[
                ["html", "Formatiert"], ["text", "Nur Text"],
              ]} />
              {ansicht === "html"
                ? <HtmlAnsicht html={m.html} fernbilder={m.remote_images} />
                : <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded border border-line bg-surface p-3 text-sm text-ink">
                    {m.text || "(kein Text)"}
                  </pre>}
            </div>
          ) : (
            <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded border border-line bg-surface p-3 text-sm text-ink">
              {m.text || "(kein Text)"}
            </pre>
          )}

          {fuerMail.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted">Aktionen:</span>
              {fuerMail.map((akt) => (
                <Zeilenknopf key={akt.definition_id} titel={akt.description}
                  onClick={() => start.mutate({ definition_id: akt.definition_id })}>
                  {akt.name}
                </Zeilenknopf>
              ))}
            </div>
          )}
          {lauf && <div className="text-xs text-green-400">{lauf}</div>}
        </>
      )}

      {attachmentAuf && (
        <AttachmentDialog
          pfad={`${basis}/attachments/${attachmentAuf.index}?folder=${encodeURIComponent(folder)}`}
          anhang={attachmentAuf} onClose={() => setAttachmentAuf(null)} />
      )}

      {moveOpen && (
        <Dialog titel="Verschieben nach" onClose={() => setMoveOpen(false)}>
          {/* Der Baum wie in der Ordnerspalte, nur ohne Zähler: hier wird gewählt, nicht
              gestöbert. Ein Klick verschiebt und schließt — ein zweiter Knopf „Übernehmen"
              wäre ein Schritt, den niemand braucht. */}
          <Listing>
            {(allFolder || []).filter((o) => o.name !== folder).map((o) => (
              <ListenLine key={o.name} dicht
                onClick={() => { setMoveOpen(false); move.mutate(o.name); }}>
                <div className="flex items-center gap-2"
                     style={{ paddingLeft: `${o.level * 0.85}rem` }}>
                  <span>{SONDER[o.special] || "📁"}</span>
                  <span className="min-w-0 flex-1 truncate">{o.display}</span>
                </div>
              </ListenLine>
            ))}
            {!allFolder?.length && <ListingLeer>Keine weiteren Ordner.</ListingLeer>}
          </Listing>
        </Dialog>
      )}
    </Area>
  );
}

function VerfassenDialog({ kontoId, start, onClose, onFehler: onError }: {
  kontoId: number; start: Record<string, string>; onClose: () => void;
  onFehler: (m: string) => void;
}) {
  const { data: identitaeten } = useQuery({
    queryKey: ["mail-identities", kontoId],
    queryFn: () => api.get<MailIdentity[]>(`/mailbox/accounts/${kontoId}/identities`),
  });
  const [identity, setIdentity] = useState<number | null>(null);
  const [f, setF] = useState({
    to: start.to || "", cc: start.cc || "", subject: start.subject || "",
    text: start.text || "", in_reply_to: start.in_reply_to || "",
  });
  const [attachments, setAttachments] = useState<
    { filename: string; content_type: string; data_base64: string; size: number }[]>([]);

  /** Datei einlesen. Base64 im Browser, weil der Server die Nachricht baut und nicht der
   *  Browser — eine Stelle, an der Entwurf und Versand dasselbe tun. */
  const fileRead = (file: File) => new Promise<string>((done, schiefgelaufen) => {
    const leser = new FileReader();
    leser.onload = () => done(String(leser.result).split(",")[1] || "");
    leser.onerror = () => schiefgelaufen(leser.error);
    leser.readAsDataURL(file);
  });
  useEffect(() => {
    if (identity !== null || !identitaeten?.length) return;
    // Reihenfolge der Wahl: was der Aufrufer mitgibt (die angeschriebene Adresse), sonst die
    // Vorgabe des Kontos, sonst die erste.
    const gewuenscht = identitaeten.find((i) => String(i.id) === (start.identity || ""));
    setIdentity((gewuenscht || identitaeten.find((i) => i.is_default) || identitaeten[0]).id);
  }, [identitaeten, identity, start.identity]);

  const rumpf = () => ({
    identity_id: identity,
    to: f.to.split(",").map((s) => s.trim()).filter(Boolean),
    cc: f.cc.split(",").map((s) => s.trim()).filter(Boolean),
    subject: f.subject, text: f.text, in_reply_to: f.in_reply_to,
    attachments: attachments.map(({ filename, content_type, data_base64 }) =>
      ({ filename, content_type, data_base64 })),
  });
  const senden = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${kontoId}/send`, rumpf()),
    onSuccess: onClose,
    onError: (e) => onError(e instanceof ApiError ? e.message : "Senden fehlgeschlagen"),
  });
  const entwurf = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${kontoId}/draft`, rumpf()),
    onSuccess: onClose,
    onError: (e) => onError(e instanceof ApiError ? e.message : "Entwurf fehlgeschlagen"),
  });

  return (
    // Festgehalten: Wer eine Mail schreibt, verliert bei einem danebengegangenen Klick
    // sonst den halben Text. Geschlossen wird über ✕, Abbrechen, Entwurf oder Senden.
    <Dialog breit festhalten titel="Nachricht verfassen" onClose={onClose}
      fuss={
        <div className="flex items-center gap-2">
          <Zeilenknopf onClick={() => entwurf.mutate()}>Als Entwurf sichern</Zeilenknopf>
          <div className="flex-1" />
          <DialogFuss onAbbrechen={onClose} laeuft={senden.isPending}
            deaktiviert={!identity || !f.to.trim()} speichernText="Senden"
            onSpeichern={() => senden.mutate()} />
        </div>
      }>
      <div className="space-y-3">
        {!identitaeten?.length && (
          <Fehlerzeile text="Dieses Konto hat noch keine Identität — ohne sie steht kein Absender fest." />
        )}
        <Field label="Von">
          <select value={identity ?? ""} className={INPUT_VALUE}
            onChange={(e) => setIdentity(Number(e.target.value))}>
            {identitaeten?.map((i) => (
              <option key={i.id} value={i.id}>
                {i.display_name ? `${i.display_name} <${i.email}>` : i.email}
              </option>
            ))}
          </select>
        </Field>
        <Field label="An" hinweis="Mehrere Adressen mit Komma trennen.">
          <input value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} className={INPUT_VALUE} />
        </Field>
        <Field label="Kopie">
          <input value={f.cc} onChange={(e) => setF({ ...f, cc: e.target.value })} className={INPUT_VALUE} />
        </Field>
        <Field label="Betreff">
          <input value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} className={INPUT_VALUE} />
        </Field>
        <Field label="Text">
          <textarea value={f.text} rows={14} className={`${INPUT_VALUE} font-mono text-xs`}
            onChange={(e) => setF({ ...f, text: e.target.value })} />
        </Field>

        <Field label="Anhänge">
          <div className="space-y-2">
            {attachments.length > 0 && (
              <Listing>
                {attachments.map((a, i) => (
                  <ListenLine key={`${a.filename}-${i}`}>
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate">📎 {a.filename}</span>
                      <Etikett>{Math.max(1, Math.round(a.size / 1024))} kB</Etikett>
                      <Zeilenknopf gefahr
                        onClick={() => setAttachments(attachments.filter((_, j) => j !== i))}>
                        Entfernen
                      </Zeilenknopf>
                    </div>
                  </ListenLine>
                ))}
              </Listing>
            )}
            <label className="inline-block cursor-pointer rounded border border-line px-2 py-1 text-xs text-muted hover:border-brand hover:text-ink">
              + Datei anhängen
              <input type="file" multiple className="hidden" onChange={async (e) => {
                const files = Array.from(e.target.files || []);
                const fresh = await Promise.all(files.map(async (d) => ({
                  filename: d.name,
                  content_type: d.type || "application/octet-stream",
                  data_base64: await fileRead(d),
                  size: d.size,
                })));
                setAttachments([...attachments, ...fresh]);
                e.target.value = "";
              }} />
            </label>
          </div>
        </Field>
      </div>
    </Dialog>
  );
}
