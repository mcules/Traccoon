import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { tr } from "../i18n";
import {
  Actions, Area, Dialog, DialogFoot, INPUT_VALUE, Tag, Field, Errorrow, ICON, IconButton,
  Listing, ListingEmpty, ListenLine, DeleteDialog, Rowbutton, State, Tab, BUTTON } from "./ui";

/**
 * Mail accounts and their identities.
 *
 * They belong on the account page and not into the settings: a mailbox is no resource agents
 * work with but the mail of a person. Passwords never come back from the server — an empty
 * field therefore means "unchanged" and not "delete".
 */
export interface MailAccount {
  id: number; name: string; enabled: boolean;
  imap_host: string; imap_port: number; imap_ssl: boolean; imap_user: string;
  smtp_host: string; smtp_port: number; smtp_security: string; smtp_user: string;
  folder_sent: string; folder_drafts: string; folder_trash: string; folder_junk: string;
  folder_archive: string; archive_mode: string; archive_pattern: string;
  mcp_enabled: boolean; mcp_ignore_folders: string[]; mcp_tools: string[];
  mcp_instructions: string;
  imap_password_set: boolean; smtp_password_set: boolean; auth_type: string;
}

export interface MailIdentity {
  id: number; account_id: number; display_name: string; email: string;
  reply_to: string; signature: string; is_default: boolean;
}

const EMPTY = {
  name: "", enabled: true,
  imap_host: "", imap_port: 993, imap_ssl: true, imap_user: "", imap_password: "",
  smtp_host: "", smtp_port: 587, smtp_security: "starttls", smtp_user: "", smtp_password: "",
  folder_sent: "Sent", folder_drafts: "Drafts", folder_trash: "Trash", folder_junk: "Junk",
  folder_archive: "Archive", archive_mode: "folder", archive_pattern: "Archive/{jahr}",
  mcp_enabled: false, mcp_ignore_folders: [] as string[], mcp_tools: [] as string[],
  mcp_instructions: "",
};

export default function MailAccountsPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [dialog, setDialog] = useState<(typeof EMPTY & { id?: number }) | null>(null);
  const [deleteAccount, setDeleteAccount] = useState<MailAccount | null>(null);

  const { data: accounts } = useQuery({
    queryKey: ["mail-accounts"], queryFn: () => api.get<MailAccount[]>("/mailbox/accounts") });
  const inv = () => qc.invalidateQueries({ queryKey: ["mail-accounts"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const save = useMutation({
    mutationFn: (f: typeof EMPTY & { id?: number }) =>
      f.id ? api.put(`/mailbox/accounts/${f.id}`, f) : api.post("/mailbox/accounts", f),
    onSuccess: () => { setErr(""); setDialog(null); inv(); }, onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/mailbox/accounts/${id}`),
    onSuccess: () => { setDeleteAccount(null); inv(); }, onError: fail,
  });

  return (
    <Area hint="Postfächer, die du hier liest und aus denen du schreibst. Zugang, Ordner, Identitäten und der Verbindungstest stehen im Dialog hinter dem Stift; das Kennwort wird verschlüsselt abgelegt und nie wieder angezeigt.">
      <Errorrow text={err} />

      <Listing>
        {accounts?.map((k) => (
          <ListenLine key={k.id} dimmed={!k.enabled}>
            <div className="flex flex-wrap items-center gap-2">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-ink">{k.name}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span className="truncate font-mono">{k.imap_user || k.imap_host}</span>
                  {!k.smtp_host && <Tag color="yellow">nur lesen</Tag>}
                  {!k.imap_password_set && <Tag color="red">kein Kennwort</Tag>}
                </div>
              </div>
              {k.enabled
                ? <State color="green" text="aktiv" />
                : <State color="grey" text="aus" />}
              <Actions>
                <IconButton icon={ICON.edit} title={tr("common.edit")}
                  onClick={() => { setErr(""); setDialog({ ...EMPTY, ...k, imap_password: "", smtp_password: "" }); }} />
                <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                  onClick={() => setDeleteAccount(k)} />
              </Actions>
            </div>
          </ListenLine>
        ))}
        {accounts?.length === 0 && <ListingEmpty>Noch kein Postfach hinterlegt.</ListingEmpty>}
      </Listing>

      <button onClick={() => { setErr(""); setDialog({ ...EMPTY }); }}
        className={BUTTON.primary}>
        {ICON.fresh} Postfach hinzufügen
      </button>

      <McpAccess onError={fail} />

      {dialog && (
        <AccountDialog start={dialog} runs={save.isPending} error={err}
          onClose={() => setDialog(null)} onSave={(f) => save.mutate(f)} />
      )}
      {deleteAccount && (
        <DeleteDialog was={deleteAccount.name} runs={remove.isPending}
          hint="Das Postfach selbst bleibt unberührt — nur der Zugang hier verschwindet."
          onClose={() => setDeleteAccount(null)}
          onDelete={() => remove.mutate(deleteAccount.id)} />
      )}
    </Area>
  );
}

export function AccountDialog({ start, error: error, runs: running, onClose, onSave }: {
  start: typeof EMPTY & { id?: number }; error: string; runs: boolean;
  onClose: () => void; onSave: (f: typeof EMPTY & { id?: number }) => void;
}) {
  const [f, setF] = useState(start);
  const [check, setCheck] = useState("");
  const [err, setErr] = useState("");
  const [part, setPart] = useState<"empfang" | "senden" | "ordner" | "identitaeten"
    | "agenten">("empfang");
  const set = (part: Partial<typeof EMPTY>) => setF({ ...f, ...part });
  const testing = useMutation({
    mutationFn: () => api.post<{ imap: string; smtp: string }>(
      `/mailbox/accounts/${start.id}/test`, {}),
    onSuccess: (r) => setCheck(`IMAP: ${r.imap || "—"} · SMTP: ${r.smtp || "—"}`),
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Prüfen fehlgeschlagen"),
  });
  // The folders of the account itself — but only once there is one: with a new mailbox no
  // connection is possible yet, and an empty select would be worse than a text field one can
  // type the name into.
  const { data: folder } = useQuery({
    queryKey: ["mail-folders", start.id],
    queryFn: () => api.get<{ name: string; display: string; level: number }[]>(
      `/mailbox/accounts/${start.id}/folders`),
    enabled: !!start.id,
    retry: false,
  });
  return (
    <Dialog wide title={f.id ? `Postfach ${f.name}` : "Postfach hinzufügen"} onClose={onClose}
      foot={<DialogFoot onCancel={onClose} runs={running}
        disabled={!f.name.trim() || !f.imap_host.trim()}
        onSave={() => onSave(f)} />}>
      <Errorrow text={error || err} />
      <div className="space-y-4">
        {/* Name und Schalter stehen über dem Menü: sie gehören zu keinem der vier Teile,
            sondern zum Postfach als Ganzem. */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-48 flex-1">
            <Field label="Name" hint="Kurzname in der Oberfläche und in Abläufen (privat, vorstand).">
              <input value={f.name} onChange={(e) => set({ name: e.target.value })}
                placeholder="privat" className={INPUT_VALUE} />
            </Field>
          </div>
          <label className="flex items-center gap-2 pb-1.5 text-sm text-muted">
            <input type="checkbox" checked={f.enabled}
              onChange={(e) => set({ enabled: e.target.checked })} />
            Aktiv
          </label>
        </div>

        <div className="flex flex-col gap-4 sm:flex-row">
          <Tab vertical active={part} onChoose={setPart} selection={[
            ["empfang", "📥 Empfang"],
            ["senden", "📤 Senden"],
            ["ordner", "📁 Ordner"],
            ["identitaeten", "👤 Identitäten"],
            ["agenten", "🤖 Agenten"],
          ]} />

          <div className="min-w-0 flex-1 space-y-4">
        {part === "empfang" && (<>
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="Server"><input value={f.imap_host} className={INPUT_VALUE}
            onChange={(e) => set({ imap_host: e.target.value })} placeholder="imap.example.org" /></Field>
          <Field label="Port"><input type="number" value={f.imap_port} className={INPUT_VALUE}
            onChange={(e) => set({ imap_port: Number(e.target.value) })} /></Field>
          <Field label="Benutzer"><input value={f.imap_user} className={INPUT_VALUE}
            onChange={(e) => set({ imap_user: e.target.value })} /></Field>
          <Field label="Kennwort" hint={start.id ? "Leer lassen heißt: unverändert." : ""}>
            <input type="password" value={f.imap_password} className={INPUT_VALUE}
              onChange={(e) => set({ imap_password: e.target.value })} /></Field>
          <label className="flex items-center gap-2 text-sm text-muted">
            <input type="checkbox" checked={f.imap_ssl}
              onChange={(e) => set({ imap_ssl: e.target.checked })} />
            Verschlüsselt (SSL/TLS)
          </label>
        </div>
        </>)}

        {part === "senden" && (<>
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="Server"><input value={f.smtp_host} className={INPUT_VALUE}
            onChange={(e) => set({ smtp_host: e.target.value })} placeholder="smtp.example.org" /></Field>
          <Field label="Port"><input type="number" value={f.smtp_port} className={INPUT_VALUE}
            onChange={(e) => set({ smtp_port: Number(e.target.value) })} /></Field>
          <Field label="Benutzer"><input value={f.smtp_user} className={INPUT_VALUE}
            onChange={(e) => set({ smtp_user: e.target.value })} /></Field>
          <Field label="Kennwort" hint={start.id ? "Leer lassen heißt: unverändert." : ""}>
            <input type="password" value={f.smtp_password} className={INPUT_VALUE}
              onChange={(e) => set({ smtp_password: e.target.value })} /></Field>
          <Field label="Verschlüsselung"
            hint={'587 rüstet auf (STARTTLS), 465 ist von Anfang an verschlüsselt. '
              + 'Passt beides nicht zusammen, meldet der Server „wrong version number".'}>
            <select value={f.smtp_security} className={INPUT_VALUE}
              onChange={(e) => {
                const art = e.target.value;
                // Pull the port along as long as it is the usual one of the other variant:
                // whoever switches the encryption almost always means the matching port too —
                // und ein eigens eingetragener Port (2525 …) bleibt unangetastet.
                const port = art === "ssl" && f.smtp_port === 587 ? 465
                  : art === "starttls" && f.smtp_port === 465 ? 587
                    : f.smtp_port;
                set({ smtp_security: art, smtp_port: port });
              }}>
              <option value="starttls">STARTTLS (587)</option>
              <option value="ssl">SSL/TLS (465)</option>
              <option value="none">ohne (nur im Haus)</option>
            </select>
          </Field>
        </div>

        </>)}

        {part === "ordner" && (<>
        {!start.id && (
          <p className="text-xs text-muted">
            Nach dem Speichern kannst du die Ordner aus dem Postfach auswählen — bis dahin
            stehen hier die üblichen Namen.
          </p>
        )}
        <div className="grid gap-2 sm:grid-cols-2">
          <FolderField label="Gesendet" value={f.folder_sent} folder={folder}
            onChoose={(v) => set({ folder_sent: v })} />
          <FolderField label="Entwürfe" value={f.folder_drafts} folder={folder}
            onChoose={(v) => set({ folder_drafts: v })} />
          <FolderField label="Papierkorb" value={f.folder_trash} folder={folder}
            onChoose={(v) => set({ folder_trash: v })} />
          <FolderField label="Spam" value={f.folder_junk} folder={folder}
            hint={'Ziel des Knopfes „Spam" — ohne Ordner erscheint der Knopf nicht.'}
            onChoose={(v) => set({ folder_junk: v })} />
        </div>

        <div className="text-xs font-medium uppercase tracking-wider text-muted/70">Archiv</div>
        <Field label="Aufteilung"
          hint={'Ziel des Knopfes „Archivieren" — ohne Ziel erscheint der Knopf nicht.'}>
          <select value={f.archive_mode} className={INPUT_VALUE}
            onChange={(e) => set({ archive_mode: e.target.value })}>
            <option value="folder">Ein Ordner für alles</option>
            <option value="pattern">Nach Muster aufteilen (Jahr, Monat …)</option>
          </select>
        </Field>
        {f.archive_mode === "folder" ? (
          <FolderField label="Archiv-Ordner" value={f.folder_archive} folder={folder}
            onChoose={(v) => set({ folder_archive: v })} />
        ) : (
          <PatternField accountId={start.id} value={f.archive_pattern}
            onChange={(v) => set({ archive_pattern: v })} />
        )}

        </>)}

        {part === "agenten" && (
          <AgentsGrant f={f} set={set} folder={folder} />
        )}

        {part === "identitaeten" && (
          start.id ? (
            <Identities accountId={start.id}
              onError={(e) => setErr(e instanceof ApiError ? e.message : "Fehler")} />
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
            <Rowbutton onClick={() => { setErr(""); testing.mutate(); }}>
              {testing.isPending ? "prüft…" : "🔌 IMAP und SMTP prüfen"}
            </Rowbutton>
            {/* Der Test benutzt, was gespeichert ist — nicht, was gerade im Formular steht.
                Anything else would mean sending half-finished credentials to the server. */}
            <span className="text-xs text-muted">
              {check || "prüft den gespeicherten Stand"}
            </span>
          </div>
        )}
      </div>
    </Dialog>
  );
}

/**
 * The access agents reach the released mailboxes through.
 *
 * One token per person, not per mailbox: whoever has it sees exactly what is released on the
 * individual mailboxes — the details stand there and not here. It is shown exactly once; a
 * second time only whoever stores it could, and then it would be no
 * Geheimnis mehr, sondern eine Kopie.
 */
function McpAccess({ onError: onError }: { onError: (e: unknown) => void }) {
  const qc = useQueryClient();
  const [fresh, setFresh] = useState("");
  const { data: state } = useQuery({
    queryKey: ["mcp-status"],
    queryFn: () => api.get<{ token_set: boolean; fingerprint: string }>("/mailbox/mcp-status"),
  });
  const inv = () => qc.invalidateQueries({ queryKey: ["mcp-status"] });
  const create = useMutation({
    mutationFn: () => api.post<{ token: string }>("/mailbox/mcp-token", {}),
    onSuccess: (r) => { setFresh(r.token); inv(); }, onError: onError,
  });
  const remove = useMutation({
    mutationFn: () => api.del("/mailbox/mcp-token"),
    onSuccess: () => { setFresh(""); inv(); }, onError: onError,
  });
  const address = `${location.origin}/api/mcp/mail`;

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
          {address}
        </code>
        <IconButton icon={ICON.copy} title="Adresse kopieren"
          onClick={() => navigator.clipboard?.writeText(address)} />
      </div>
      {fresh ? (
        <div className="space-y-1 rounded border border-amber-500/30 bg-amber-500/10 p-2">
          <div className="text-xs text-amber-300">
            Einmalig sichtbar — jetzt kopieren, danach nur noch neu erzeugbar.
          </div>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate text-xs text-ink">{fresh}</code>
            <IconButton icon={ICON.copy} title="Token kopieren"
              onClick={() => navigator.clipboard?.writeText(fresh)} />
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Rowbutton onClick={() => create.mutate()}>
            {state?.token_set ? "Neues Token erzeugen" : "Token erzeugen"}
          </Rowbutton>
          {state?.token_set && (
            <>
              <Tag color="green">Token gesetzt · {state.fingerprint}</Tag>
              <Rowbutton danger onClick={() => remove.mutate()}>Zugang sperren</Rowbutton>
            </>
          )}
          {state?.token_set && (
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
 * What agents may see and do with this mailbox.
 *
 * The default is: nothing. A mailbox is the mail of a person and no data store — which is why
 * release happens per tool instead of "access yes/no", and reading, refiling and sending stand
 * there as three separate groups and not as levels
 * einer Leiter.
 */
function AgentsGrant({ f, set: set, folder: folder }: {
  f: typeof EMPTY & { id?: number };
  set: (part: Partial<typeof EMPTY>) => void;
  folder: { name: string; display: string; level: number }[] | undefined;
}) {
  const [newPattern, setNewPattern] = useState("");
  const { data: catalog } = useQuery({
    queryKey: ["mcp-tools"],
    queryFn: () => api.get<{ name: string; kind: string; description: string; always: boolean }[]>(
      "/mailbox/mcp-tools"),
    staleTime: 60 * 60_000,
  });

  const GROUP: Record<string, string> = {
    lesen: "Lesen", change: "Umsortieren", send: "Senden",
  };
  const toggle = (name: string) => {
    const inside = f.mcp_tools.includes(name);
    set({ mcp_tools: inside ? f.mcp_tools.filter((t) => t !== name)
                             : [...f.mcp_tools, name] });
  };
  const patternPath = (m: string) =>
    set({ mcp_ignore_folders: f.mcp_ignore_folders.filter((x) => x !== m) });
  const patternHint = (m: string) => {
    const value = m.trim();
    if (value && !f.mcp_ignore_folders.includes(value)) {
      set({ mcp_ignore_folders: [...f.mcp_ignore_folders, value] });
    }
    setNewPattern("");
  };

  return (
    <div className="space-y-4">
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={f.mcp_enabled}
          onChange={(e) => set({ mcp_enabled: e.target.checked })} />
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
        <Field label="Was ein Agent über dieses Postfach wissen muss"
          hint="Wird beim Verbinden gelesen, also bevor das erste Werkzeug läuft — und steht zusätzlich an jedem Postfach in der Übersicht.">
          <textarea value={f.mcp_instructions} rows={5} className={`${INPUT_VALUE} text-xs`}
            placeholder={"Vereinspostfach des Vorstands. Sachlich und in Sie-Form antworten.\n"
              + "Nichts ohne Rückfrage senden. Rechnungen gehören ins Archiv, nicht in den Papierkorb."}
            onChange={(e) => set({ mcp_instructions: e.target.value })} />
        </Field>

        <div className="text-xs font-medium uppercase tracking-wider text-muted/70">
          Werkzeuge
        </div>
        {["lesen", "aendern", "senden"].map((group) => (
          <div key={group} className="space-y-1">
            <div className="text-xs font-medium text-ink">{GROUP[group]}</div>
            {(catalog || []).filter((w) => w.kind === group && !w.always).map((w) => (
              <label key={w.name} className="flex items-start gap-2 text-sm text-muted">
                <input type="checkbox" className="mt-1"
                  checked={f.mcp_tools.includes(w.name)}
                  onChange={() => toggle(w.name)} />
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
        <Listing>
          {f.mcp_ignore_folders.map((m) => (
            <ListenLine key={m} dense>
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate">{m}</code>
                <Rowbutton danger onClick={() => patternPath(m)}>Entfernen</Rowbutton>
              </div>
            </ListenLine>
          ))}
          {!f.mcp_ignore_folders.length && <ListingEmpty>Nichts ausgeblendet.</ListingEmpty>}
        </Listing>
        <div className="flex flex-wrap items-center gap-2">
          <select value="" className={`${INPUT_VALUE} max-w-xs`}
            onChange={(e) => e.target.value && patternHint(e.target.value)}>
            <option value="">Ordner wählen…</option>
            {(folder || []).map((o) => (
              <option key={o.name} value={o.name}>
                {"\u00a0".repeat(o.level * 2)}{o.display}
              </option>
            ))}
          </select>
          <input value={newPattern} placeholder="oder Muster: Privat*"
            className={`${INPUT_VALUE} max-w-xs font-mono`}
            onChange={(e) => setNewPattern(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); patternHint(newPattern); } }} />
          <Rowbutton onClick={() => patternHint(newPattern)}>Hinzufügen</Rowbutton>
        </div>
      </>)}
    </div>
  );
}

/**
 * The archive pattern, with a preview while typing.
 *
 * It is filled from the date OF THE MAIL, not from today's: an invoice from 2023 still belongs
 * in the year 2023 in 2026. The slash separates the levels, no matter how the server does that
 * internally — the server converts it.
 */
function PatternField({ accountId, value: value, onChange: onUpdate }: {
  accountId?: number; value: string; onChange: (v: string) => void;
}) {
  const [preview, setPreview] = useState("");
  useEffect(() => {
    if (!accountId || !value) { setPreview(""); return; }
    let aborted = false;
    const id = setTimeout(() => {
      api.post<{ folder: string }>(`/mailbox/accounts/${accountId}/archive-preview`,
                                   { archive_pattern: value })
        .then((r) => { if (!aborted) setPreview(r.folder); })
        .catch(() => { if (!aborted) setPreview(""); });
    }, 300);
    return () => { aborted = true; clearTimeout(id); };
  }, [accountId, value]);

  return (
    <div className="space-y-2">
      <Field label="Muster" hint="Schrägstrich trennt Ebenen. Beispiel: Archive/{jahr}/{monat}">
        <input value={value} className={`${INPUT_VALUE} font-mono`}
          onChange={(e) => onUpdate(e.target.value)} placeholder="Archive/{jahr}" />
      </Field>
      {preview && (
        <p className="text-xs text-muted">
          Eine Mail von heute landet in <code className="text-brand">{preview}</code>.
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
 * A folder of the account — as a select, as soon as the mailbox is reachable.
 *
 * Typing would mean guessing: depending on the provider folders are called `Sent`, `Gesendet`,
 * `INBOX.Sent` or `[Gmail]/Gesendet`, and a typo is noticed only when a sent mail does not turn
 * up in one's own mailbox. If the connection does not stand yet (a new account, a wrong
 * Kennwort), bleibt das Textfeld — besser als ein leeres Auswahlfeld.
 */
function FolderField({ label, hint: hint, value: value, folder: folder, onChoose }: {
  label: string; hint?: string; value: string;
  folder: { name: string; display: string; level: number }[] | undefined;
  onChoose: (v: string) => void;
}) {
  if (!folder?.length) {
    return (
      <Field label={label} hint={hint}>
        <input value={value} className={INPUT_VALUE} onChange={(e) => onChoose(e.target.value)} />
      </Field>
    );
  }
  // An entered folder that does (no longer) exist stays visible instead of getting lost
  // quietly — otherwise merely opening the dialog would change the setting.
  const unknown = value && !folder.some((o) => o.name === value);
  return (
    <Field label={label} hint={hint}>
      <select value={value} className={INPUT_VALUE} onChange={(e) => onChoose(e.target.value)}>
        <option value="">— keiner —</option>
        {unknown && <option value={value}>{value} (nicht gefunden)</option>}
        {folder.map((o) => (
          <option key={o.name} value={o.name}>
            {"\u00a0".repeat(o.level * 2)}{o.display}
          </option>
        ))}
      </select>
    </Field>
  );
}

/** Identitäten eines Kontos: wer als Absender auftritt. */
function Identities({ accountId, onError: onError }: { accountId: number; onError: (e: unknown) => void }) {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Partial<MailIdentity> | null>(null);
  const { data } = useQuery({
    queryKey: ["mail-identities", accountId],
    queryFn: () => api.get<MailIdentity[]>(`/mailbox/accounts/${accountId}/identities`),
  });
  const inv = () => qc.invalidateQueries({ queryKey: ["mail-identities", accountId] });
  const save = useMutation({
    mutationFn: (i: Partial<MailIdentity>) => i.id
      ? api.put(`/mailbox/identities/${i.id}`, i)
      : api.post(`/mailbox/accounts/${accountId}/identities`, i),
    onSuccess: () => { setDialog(null); inv(); }, onError: onError,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/mailbox/identities/${id}`),
    onSuccess: inv, onError: onError,
  });

  return (
    <div className="mt-2 border-t border-line pt-2.5">
      <Listing>
        {data?.map((i) => (
          <ListenLine key={i.id}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 truncate">
                <span className="text-ink">{i.display_name || i.email}</span>
                {i.display_name && <span className="ml-2 text-xs text-muted">{i.email}</span>}
              </span>
              {i.is_default && <Tag color="brand">Vorgabe</Tag>}
              <Actions>
                <IconButton icon={ICON.edit} title={tr("common.edit")}
                  onClick={() => setDialog(i)} />
                <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                  onClick={() => remove.mutate(i.id)} />
              </Actions>
            </div>
          </ListenLine>
        ))}
        {data?.length === 0 && <ListingEmpty>Noch keine Identität — ohne sie kannst du nicht senden.</ListingEmpty>}
      </Listing>
      <button onClick={() => setDialog({ email: "", display_name: "", is_default: !data?.length })}
        className="mt-2 rounded border border-line px-2 py-1 text-xs text-muted hover:border-brand hover:text-ink">
        + Identität
      </button>

      {dialog && (
        <Dialog title={dialog.id ? "Identität" : "Identität anlegen"} onClose={() => setDialog(null)}
          foot={<DialogFoot onCancel={() => setDialog(null)} runs={save.isPending}
            disabled={!dialog.email?.trim()} onSave={() => save.mutate(dialog)} />}>
          <div className="space-y-3">
            <Field label="Absender-Adresse"><input value={dialog.email || ""} className={INPUT_VALUE}
              onChange={(e) => setDialog({ ...dialog, email: e.target.value })} /></Field>
            <Field label="Angezeigter Name"><input value={dialog.display_name || ""} className={INPUT_VALUE}
              onChange={(e) => setDialog({ ...dialog, display_name: e.target.value })} /></Field>
            <Field label="Antwort an" hint="Leer lassen, wenn Antworten an die Absender-Adresse gehen sollen.">
              <input value={dialog.reply_to || ""} className={INPUT_VALUE}
                onChange={(e) => setDialog({ ...dialog, reply_to: e.target.value })} /></Field>
            <Field label="Signatur">
              <textarea value={dialog.signature || ""} rows={4} className={`${INPUT_VALUE} font-mono text-xs`}
                onChange={(e) => setDialog({ ...dialog, signature: e.target.value })} /></Field>
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
