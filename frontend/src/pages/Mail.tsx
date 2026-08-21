import { tr } from "../i18n";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, fetchFile } from "../api";
import { usePageChrome } from "../pageChrome";
import { useAuth } from "../auth";
import { formatDateTime } from "../lib/formatTime";
import { AccountDialog, type MailAccount, type MailIdentity } from "../components/MailAccountsPanel";
import {
  Area, ConfirmDialog, Dialog, DialogFoot, INPUT_VALUE, Tag, Field, Errorrow,
  IconButton, Button, BUTTON, Listing, ListingEmpty, ListenLine, Tab, Rowbutton, BUTTON_TEXT} from "../components/ui";

/**
 * Das Postfach.
 *
 * Three columns like in every mail program, and for the same reason: folders change rarely,
 * the list often, the message with every click. What Traccoon adds sits at the end of a mail
 * and on every attachment — the **actions**: a button starts a flow and puts account, folder,
 * UID and the chosen attachment into its context. "Attachment to Paperless" is thereby no
 * special case in the code but a flow in the editor.
 */
interface Header {
  uid: number; subject: string; from: string; date: string; size: number;
  seen: boolean; flagged: boolean; answered: boolean; has_attachment: boolean;
}
interface Address { name: string; addr: string }
interface Attachment { index: number; filename: string; content_type: string; size: number }
interface Message {
  uid: number; folder: string; subject: string; from: Address[]; to: Address[]; cc: Address[];
  reply_to: Address[];
  date: string; message_id: string; text: string; html: string; remote_images: boolean;
  attachments: Attachment[]; seen: boolean; flagged: boolean;
}
interface Folder {
  name: string; display: string; level: number; parent: string; delimiter: string;
  special: string; unseen: number; total: number;
}
interface Action { definition_id: number; key: string; name: string; description: string; scope: string }

const SPECIAL: Record<string, string> = {
  sent: "📤", drafts: "📝", trash: "🗑", junk: "🚫", archive: "📦",
};

export default function Mail() {
  usePageChrome("Mail", []);
  const qc = useQueryClient();
  const { user } = useAuth();
  const [accountId, setAccountId] = useState<number | null>(null);
  const [folder, setFolder] = useState("INBOX");
  const [uid, setUid] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [question, setQuestion] = useState("");
  const [err, setErr] = useState("");
  const [compose, setCompose] = useState<null | Record<string, string>>(null);
  const [settings, setSettings] = useState<MailAccount | null>(null);

  const { data: accounts } = useQuery({
    queryKey: ["mail-accounts"], queryFn: () => api.get<MailAccount[]>("/mailbox/accounts") });
  // So one can see where mail waits without going in. Asked rarely: behind it sits one
  // Postfach eine IMAP-Verbindung.
  const { data: unread } = useQuery({
    queryKey: ["mail-unread"],
    queryFn: () => api.get<{ accounts: { account_id: number; unseen: number | null }[] }>(
      "/mailbox/unread"),
    // Look again immediately when switching back into the tab: whoever was away for a minute
    // does not want to wait for the next round. Globally that is off, for mail it is right.
    refetchInterval: 60_000, refetchOnWindowFocus: true, retry: false,
  });
  useEffect(() => {
    if (accountId !== null || !accounts?.length) return;
    // The mailbox opened last comes first — it is stored on the person and therefore applies
    // after a new login and on another machine as well.
    const noted = accounts.find((k) => k.id === user?.mail_last_account_id);
    setAccountId((noted || accounts.find((k) => k.enabled) || accounts[0]).id);
  }, [accounts, accountId, user]);

  const accountSwitch = (id: number) => {
    setAccountId(id);
    setFolder("INBOX");
    setUid(null);
    api.post(`/mailbox/accounts/${id}/last`, {}).catch(() => {/* Remembering is no must */});
  };

  const { data: folderListing } = useQuery({
    queryKey: ["mail-folders", accountId], enabled: !!accountId,
    queryFn: () => api.get<Folder[]>(`/mailbox/accounts/${accountId}/folders?counts=true`),
    refetchInterval: 60_000, refetchOnWindowFocus: true,
  });

  if (!accounts?.length) {
    return (
      <Area hint="Noch kein Postfach hinterlegt.">
        <p className="text-sm text-muted">
          Konten und Identitäten stehen im Konto unter <b>Mail-Konten</b>.
        </p>
      </Area>
    );
  }

  return (
    <div className="space-y-3">
      <Errorrow text={err} />
      {/* Eine Zeile für alles, was zum Postfach gehört: welches, seine Einstellungen, und
          the only action that does not start from a message. */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">Postfach</span>
        <select value={accountId ?? ""} onChange={(e) => accountSwitch(Number(e.target.value))}
          className={`${INPUT_VALUE} max-w-[16rem]`}>
          {accounts.map((k) => {
            const open = unread?.accounts.find((a) => a.account_id === k.id)?.unseen;
            return (
              <option key={k.id} value={k.id}>
                {k.name}{k.enabled ? "" : " (aus)"}{open ? ` — ${open} neu` : ""}
              </option>
            );
          })}
        </select>
        <IconButton icon="⟳" title="Jetzt nachsehen"
          onClick={() => {
            qc.invalidateQueries({ queryKey: ["mail-unread"] });
            qc.invalidateQueries({ queryKey: ["mail-folders"] });
            qc.invalidateQueries({ queryKey: ["mail-list"] });
          }} />
        <IconButton icon="⚙" title="Einstellungen dieses Postfachs"
          onClick={() => setSettings(accounts.find((k) => k.id === accountId) || null)} />
        {/* Die anderen Postfächer mit neuer Post — sichtbar, ohne das Auswahlfeld zu öffnen,
            und ein Klick springt hin. Wer nichts liegen hat, taucht hier nicht auf: eine
            row of zeroes would be no information but wallpaper. */}
        {accounts.filter((k) => {
          const open = unread?.accounts.find((a) => a.account_id === k.id)?.unseen;
          return k.id !== accountId && !!open;
        }).map((k) => (
          <button key={k.id} onClick={() => accountSwitch(k.id)}
            title={`Zu „${k.name}" wechseln`}
            className="flex shrink-0 items-center gap-1.5 rounded border border-brand/40 bg-brand/15 px-2 py-1 text-xs text-brand transition-colors hover:bg-brand/25">
            {k.name}
            <span className="rounded-full bg-brand px-1.5 text-[11px] text-white tabular-nums">
              {unread?.accounts.find((a) => a.account_id === k.id)?.unseen}
            </span>
          </button>
        ))}
        {/* Die Suche gehört zum Postfach, nicht zur Liste darunter: sie gilt für den ganzen
            folder and stays visible even when a message is open on the right. */}
        <form onSubmit={(e) => { e.preventDefault(); setSearch(question); setUid(null); }}
              className="flex min-w-0 flex-1 items-center gap-2">
          <input value={question} onChange={(e) => setQuestion(e.target.value)}
            placeholder="Suchen (Volltext)" className={`${INPUT_VALUE} min-w-0 max-w-md flex-1`} />
          {search && (
            <Rowbutton onClick={() => { setQuestion(""); setSearch(""); }}>
              zurücksetzen
            </Rowbutton>
          )}
        </form>
        <button onClick={() => setCompose({})}
          className={BUTTON.primary}>
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
              <FolderTree folder={folderListing} active={folder}
                onChoose={(n) => { setFolder(n); setUid(null); setSearch(""); setQuestion(""); }} />
              {/* Handgriffe am GEWÄHLTEN Ordner. Sie stehen unter dem Baum und nicht in
                  jeder Zeile: gebraucht werden sie selten, und ein Löschknopf neben jedem
                  folder one delete button is one too many. */}
              {accountId && (
                <FolderHandgrips accountId={accountId} folder={folder}
                  onDeleted={() => { setFolder("INBOX"); setUid(null); }}
                  onError={setErr} />
              )}
            </Area>
            {uid !== null && (
              <MessagesListing accountId={accountId!} folder={folder} search={search}
                onOpen={setUid} onError={setErr} open={uid} narrow />
            )}
          </div>
        </div>

        <div className="min-w-0 flex-1 space-y-3">
          {uid === null ? (
            <MessagesListing accountId={accountId!} folder={folder} search={search}
              onOpen={setUid} onError={setErr} />
          ) : (
            <Readview accountId={accountId!} account={accounts.find((k) => k.id === accountId)}
              folder={folder} uid={uid} onBack={() => setUid(null)}
              onReplies={(f) => setCompose(f)} onError={setErr} />
          )}
        </div>
      </div>

      {compose && accountId && (
        <ComposeDialog accountId={accountId} start={compose} onClose={() => setCompose(null)}
          onError={setErr} />
      )}
      {settings && (
        <AccountSettings account={settings} onClose={() => setSettings(null)}
          onError={setErr} />
      )}
    </div>
  );
}

/**
 * The settings of the open mailbox — the same dialog as in the account.
 *
 * Building it twice would mean maintaining it twice: folders, passwords and the archive
 * pattern belong together, no matter from which side one opens them.
 */
function AccountSettings({ account, onClose, onError: onError }: {
  account: MailAccount; onClose: () => void; onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (f: any) => api.put(`/mailbox/accounts/${account.id}`, f),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mail-accounts"] });
      qc.invalidateQueries({ queryKey: ["mail-folders"] });
      onClose();
    },
    onError: (e) => onError(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen"),
  });
  return (
    <AccountDialog start={{ ...account, imap_password: "", smtp_password: "" } as any}
      error="" runs={save.isPending} onClose={onClose}
      onSave={(f) => save.mutate(f)} />
  );
}

/**
 * The folders as a tree — with indentation, expanding and unread counts.
 *
 * A flat list is enough as long as somebody has five folders. With a grown mailbox that
 * archives by year, project and mailing list it is a wall: one looks for the folder one is
 * looking for and cannot find it again among thirty identical-looking rows. What is collapsed
 * is the branch, not the access — a click on the parent folder opens it
 * trotzdem.
 */
function FolderTree({ folder: folder, active, onChoose }: {
  folder: Folder[] | undefined; active: string; onChoose: (name: string) => void;
}) {
  // Start collapsed: a grown mailbox has archives by year and mailing lists by sender, and one
  // does not want to see all of those when opening it. What one needs daily are the six
  // special folders — the rest is one click away.
  const [on, setOn] = useState<Set<string>>(new Set());
  if (!folder) return <Listing><ListingEmpty>Ordner werden geladen…</ListingEmpty></Listing>;

  const hasChildren = (o: Folder) => folder.some((k) => k.parent === o.name);
  /**
   * Unread of a branch: the folder itself and everything below it.
   *
   * Without that a collapsed branch would stay mute — one would see "Archive" without a count
   * and would not know that something unread lies in `Archive/2026/08`. That is why the sum
   * stands on the collapsed folder and only its own on the expanded one: otherwise one would
   * count the same message again at every level.
   */
  const sum_total = (o: Folder): number => folder
    .filter((k) => k.parent === o.name)
    .reduce((number, k) => number + sum_total(k), o.unseen || 0);
  // Visible is whatever has all its ancestors expanded. The active folder always stays so —
  // otherwise what one is currently reading in would vanish from under one's feet.
  const visible = (o: Folder) => {
    if (o.name === active || !o.parent) return true;
    let parent = o.parent;
    while (parent) {
      if (!on.has(parent)) return false;
      parent = folder.find((k) => k.name === parent)?.parent || "";
    }
    return true;
  };
  const toggle = (name: string) => {
    const fresh = new Set(on);
    fresh.has(name) ? fresh.delete(name) : fresh.add(name);
    setOn(fresh);
  };

  return (
    <Listing>
      {folder.filter(visible).map((o) => (
        <ListenLine key={o.name} dense onClick={() => onChoose(o.name)}>
          {/* Feste Spalten statt Flex mit Platzhaltern: nur so steht das Ordnersymbol jeder
              line in the same place, whether or not a fold arrow sits in front of it. */}
          <div className="grid grid-cols-[0.75rem_1.25rem_minmax(0,1fr)_auto] items-center gap-1.5"
               style={{ paddingLeft: `${o.level * 0.85}rem` }}>
            {hasChildren(o) ? (
              <button onClick={(e) => { e.stopPropagation(); toggle(o.name); }}
                className={BUTTON_TEXT.secondary}
                title={on.has(o.name) ? "zuklappen" : "aufklappen"}>
                {on.has(o.name) ? "▼" : "▶"}
              </button>
            ) : <span />}
            <span className="text-center leading-none">{SPECIAL[o.special] || "📁"}</span>
            <span className={`min-w-0 truncate ${
              o.name === active ? "font-medium text-brand"
                : sum_total(o) ? "font-medium text-ink" : ""}`}>
              {o.display}
            </span>
            {(() => {
              const to = hasChildren(o) && !on.has(o.name);
              const number = to ? sum_total(o) : o.unseen;
              if (!number) return <span />;
              // Collapsed and something only in the children: the count belongs to the branch,
              // not to the folder — shown more quietly so one sees the difference.
              const onlyChildren = to && !o.unseen;
              return (
                <Tag color={onlyChildren ? "neutral" : "brand"}
                  title={onlyChildren ? "in Unterordnern" : "ungelesen"}>
                  {number}
                </Tag>
              );
            })()}
          </div>
        </ListenLine>
      ))}
    </Listing>
  );
}

/** What one can do with a whole folder — both with a confirmation, both rare. */
function FolderHandgrips({ accountId, folder: folder, onDeleted, onError: onError }: {
  accountId: number; folder: string; onDeleted: () => void; onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const [question, setQuestion] = useState<"gelesen" | "loeschen" | null>(null);
  const [notice, setNotice] = useState("");
  const gonewrong = (was: string) => (e: unknown) =>
    onError(e instanceof ApiError ? e.message : `${was} fehlgeschlagen`);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["mail-folders"] });
    qc.invalidateQueries({ queryKey: ["mail-list"] });
  };

  const read = useMutation({
    mutationFn: () => api.post<{ marked: number }>(
      `/mailbox/accounts/${accountId}/folders/read-all`, { folder: folder }),
    onSuccess: (r) => {
      setQuestion(null);
      setNotice(r.marked ? `${r.marked} Nachrichten als gelesen markiert` : "Nichts war ungelesen");
      refresh();
    },
    onError: (e) => { setQuestion(null); gonewrong("Markieren")(e); },
  });
  const remove = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${accountId}/folders/delete`, { folder: folder }),
    onSuccess: () => { setQuestion(null); refresh(); onDeleted(); },
    onError: (e) => { setQuestion(null); gonewrong("Löschen")(e); },
  });

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Rowbutton onClick={() => setQuestion("gelesen")}>✓ Alle gelesen</Rowbutton>
        <Rowbutton danger onClick={() => setQuestion("loeschen")}>🗑 Ordner löschen</Rowbutton>
      </div>
      {notice && <div className="text-xs text-green-400">{notice}</div>}

      {question === "gelesen" && (
        <ConfirmDialog
          title="Alle als gelesen markieren?"
          text={`Alles Ungelesene in „${folder}" wird auf gelesen gesetzt.`}
          hint="Rückgängig geht das nur Nachricht für Nachricht."
          danger={false} confirmText="Markieren" runs={read.isPending}
          onClose={() => setQuestion(null)} onConfirm={() => read.mutate()} />
      )}
      {question === "loeschen" && (
        <ConfirmDialog
          title={`Ordner „${folder}" löschen?`}
          text="Der Ordner und alles darin verschwindet — auf dem Server, nicht nur hier."
          hint="Das ist endgültig. Sonderordner (Posteingang, Gesendet, Entwürfe, Papierkorb, Spam) sind geschützt."
          confirmText="Endgültig löschen" runs={remove.isPending}
          onClose={() => setQuestion(null)} onConfirm={() => remove.mutate()} />
      )}
    </>
  );
}

/**
 * Show the HTML of a foreign mail without handing it the window.
 *
 * Three locks on top of each other: the server has already cleaned up (nh3), the frame here is
 * a `sandbox` iframe without script rights, and a content policy in the document itself lets
 * nothing be loaded. Remote images hang in the mail as `data-fern` and become `src` only on a
 * click — a loaded image is a signal back to the sender that it was read.
 */
function HtmlView({ html, remoteimages }: { html: string; remoteimages: boolean }) {
  const [images, setImages] = useState(false);
  const content = images ? html.replace(/data-fern="/g, 'src="') : html;
  const policy = "default-src 'none'; style-src 'unsafe-inline'; font-src data:; "
    + (images ? "img-src data: https:;" : "img-src data:;");
  const document = `<!doctype html><html><head>
      <meta http-equiv="Content-Security-Policy" content="${policy}">
      <base target="_blank">
      <style>
        body { font: 14px/1.5 system-ui, sans-serif; color: #c9d1d9; background: #0d1117;
               margin: 12px; word-break: break-word; }
        a { color: #58a6ff; } img { max-width: 100%; height: auto; }
        table { max-width: 100%; } blockquote { border-left: 2px solid #30363d;
               margin: 0; padding-left: 12px; color: #8b949e; }
      </style></head><body>${content}</body></html>`;

  return (
    <div className="space-y-2">
      {remoteimages && !images && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          Bilder von fremden Servern wurden nicht geladen — das würde dem Absender verraten,
          dass du die Mail gelesen hast.
          <Rowbutton onClick={() => setImages(true)}>Bilder laden</Rowbutton>
        </div>
      )}
      <iframe
        title="Nachricht"
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        srcDoc={document}
        className="h-[60vh] w-full rounded border border-line bg-surface"
      />
    </div>
  );
}

function MessagesListing({ accountId, folder: folder, search, onOpen: onOpen_it, onError: onError,
                           open: open, narrow = false }: {
  accountId: number; folder: string; search: string;
  onOpen: (uid: number) => void; onError: (m: string) => void;
  open?: number; narrow?: boolean;
}) {
  const [page, setPage] = useState(0);
  const limit = 50;
  useEffect(() => { setPage(0); }, [folder, search]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["mail-list", accountId, folder, search, page],
    queryFn: () => api.get<{ total: number; messages: Header[] }>(
      `/mailbox/accounts/${accountId}/messages?folder=${encodeURIComponent(folder)}`
      + `&q=${encodeURIComponent(search)}&offset=${page * limit}&limit=${limit}`),
    // Not before an account is picked: `kontoId` is null on the first render, and the
    // request went out as `accounts/null/messages` — a 422 on every visit to the page.
    enabled: !!accountId,
    // New mail should turn up in the list, not only in the counter next to it.
    refetchInterval: 60_000, refetchOnWindowFocus: true,
  });
  useEffect(() => {
    if (error) onError(error instanceof ApiError ? error.message : "Postfach nicht erreichbar");
  }, [error]);

  return (
    <Area
      title={folder}
      tools={<>
        {search && <Tag color="brand">Suche: {search}</Tag>}
        <div className="flex-1" />
        <span className="text-xs text-muted">
          {data?.total ?? 0} {search ? "Treffer" : "Nachrichten"}
        </span>
      </>}
    >
      {/* Schmal heißt: die Liste steht neben der geöffneten Mail und scrollt für sich. Ohne
          its own height would make the page as long as the mailbox. */}
      <div className={narrow ? "max-h-[55vh] overflow-y-auto" : ""}>
      <Listing>
        {data?.messages.map((m) => (
          <ListenLine key={m.uid} dense={narrow} onClick={() => onOpen_it(m.uid)}>
            <div className={`flex flex-wrap items-baseline gap-x-3 gap-y-1 ${
              m.uid === open ? "text-brand" : ""}`}>
              <span className={`min-w-0 flex-1 truncate ${
                m.uid === open ? "font-medium" : m.seen ? "text-ink" : "font-semibold text-ink"}`}>
                {m.subject || "(kein Betreff)"}
              </span>
              {!m.seen && <Tag color="brand">neu</Tag>}
              {m.has_attachment && <span title="hat einen Anhang">📎</span>}
              {m.flagged && <span title="markiert">⭐</span>}
              {m.answered && <span title="beantwortet">↩</span>}
              <span className="shrink-0 text-xs text-muted">{formatDateTime(m.date)}</span>
            </div>
            <div className="mt-0.5 truncate text-xs text-muted">{m.from}</div>
          </ListenLine>
        ))}
        {isLoading && <ListingEmpty>Wird geladen…</ListingEmpty>}
        {!isLoading && !data?.messages.length && <ListingEmpty>Nichts in diesem Ordner.</ListingEmpty>}
      </Listing>
      </div>
      {(data?.total ?? 0) > limit && (
        <div className="flex items-center gap-2">
          <Rowbutton onClick={() => setPage(Math.max(0, page - 1))}>← neuer</Rowbutton>
          <span className="text-xs text-muted">
            {page * limit + 1}–{Math.min((page + 1) * limit, data!.total)} von {data!.total}
          </span>
          <Rowbutton onClick={() => setPage(page + 1)}>älter →</Rowbutton>
        </div>
      )}
    </Area>
  );
}

/**
 * Ein Anhang zum Ansehen.
 *
 * Before, a link to the API address stood there — and the browser does not send along a token
 * it does not know. What arrived was "Not authenticated". Now the file is fetched with the
 * login and shown here; what cannot be shown can still be
 * speichern.
 *
 * The blob address is released again on closing: otherwise every viewed attachment keeps its
 * memory until the page reloads.
 */
function AttachmentDialog({ path: path, attachment: attachment, onClose }: {
  path: string; attachment: Attachment; onClose: () => void;
}) {
  const [source, setSource] = useState("");
  const [kind, setKind] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let address = "";
    let alive = true;
    fetchFile(path)
      .then(async ({ blob, kind: t }) => {
        if (!alive) return;
        setKind(t);
        // Text is read, not embedded: inside a frame it would stand without wrapping and in
        // the font of the page, which is not the one it means.
        if (t.startsWith("text/") || t.includes("json")) setText(await blob.text());
        else {
          address = URL.createObjectURL(blob);
          setSource(address);
        }
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Anhang nicht ladbar"));
    return () => { alive = false; if (address) URL.revokeObjectURL(address); };
  }, [path]);

  const image = kind.startsWith("image/");
  const pdf = kind.includes("pdf");
  return (
    <Dialog wide title={`📎 ${attachment.filename}`} onClose={onClose} foot={
      <>
        <Button onClick={onClose}>{tr("common.close")}</Button>
        {source && (
          <a href={source} download={attachment.filename} className={BUTTON.primary}>
            {tr("mail.save")}
          </a>
        )}
      </>
    }>
      <Errorrow text={error} />
      {!error && !source && !text && (
        <div className="p-6 text-center text-sm text-muted">{tr("common.loading")}</div>
      )}
      {image && source && (
        <img src={source} alt={attachment.filename} className="mx-auto max-h-[70vh] rounded" />
      )}
      {pdf && source && (
        <iframe src={source} title={attachment.filename} className="h-[70vh] w-full rounded bg-white" />
      )}
      {text && (
        <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded bg-surface p-3
          text-xs text-ink">{text}</pre>
      )}
      {source && !image && !pdf && (
        <div className="p-6 text-center text-sm text-muted">
          {tr("mail.no_preview_kind_here", { kind: kind })}
        </div>
      )}
    </Dialog>
  );
}

function Readview({ accountId, account, folder: folder, uid, onBack: onBack, onReplies, onError: onError }: {
  accountId: number; account: MailAccount | undefined; folder: string; uid: number;
  onBack: () => void; onReplies: (f: Record<string, string>) => void;
  onError: (m: string) => void;
}) {
  const [moveOpen, setMoveOpen] = useState(false);
  const [attachmentOn, setAttachmentOn] = useState<Attachment | null>(null);
  const qc = useQueryClient();
  const [run, setRun] = useState("");
  const [view, setView] = useState<"html" | "text">("html");
  const basis = `/mailbox/accounts/${accountId}/messages/${uid}`;
  const { data: m, error } = useQuery({
    queryKey: ["mail-message", accountId, folder, uid],
    queryFn: () => api.get<Message>(`${basis}?folder=${encodeURIComponent(folder)}`),
  });
  const { data: actions } = useQuery({
    queryKey: ["mail-actions"], queryFn: () => api.get<Action[]>("/mailbox/actions"),
    staleTime: 5 * 60_000,
  });
  // For "Move to…": the same query as the folder column, so from the cache and without a
  // second trip to the mailbox.
  const { data: allFolder } = useQuery({
    queryKey: ["mail-folders", accountId],
    queryFn: () => api.get<Folder[]>(`/mailbox/accounts/${accountId}/folders?counts=true`),
  });
  const { data: identities } = useQuery({
    queryKey: ["mail-identities", accountId],
    queryFn: () => api.get<MailIdentity[]>(`/mailbox/accounts/${accountId}/identities`),
  });

  /**
   * Empfänger einer Antwort.
   *
   * `allen` additionally takes along everyone who was already on it — minus one's own
   * addresses, because answering oneself is the classic one notices only after sending. If a
   * `Reply-To` stands in the mail it takes precedence over the sender: that is exactly what it
   * is there for.
   */
  const answerFields = (all: boolean): Record<string, string> => {
    const own = new Set((identities || []).map((i) => i.email.toLowerCase()));
    const addresses = (listing: Address[] | undefined) =>
      (listing || []).map((a) => a.addr).filter((a) => a && !own.has(a.toLowerCase()));

    const answerAn = addresses(m?.reply_to?.length ? m.reply_to : m?.from);
    const an = all
      ? Array.from(new Set([...answerAn, ...addresses(m?.to)]))
      : answerAn;
    const copy = all ? Array.from(new Set(addresses(m?.cc))) : [];
    return {
      to: an.join(", "),
      cc: copy.join(", "),
      subject: `Re: ${m?.subject || ""}`,
      in_reply_to: m?.message_id || "",
      text: `\n\n> ${(m?.text || "").split("\n").join("\n> ")}`,
      identity: String(matchingIdentity() ?? ""),
    };
  };

  /** Would "Reply all" give more addresses than "Reply"? Only then is the button worth it. */
  const moreRecipient = (): boolean => {
    const own = new Set((identities || []).map((i) => i.email.toLowerCase()));
    const foreign = (listing: Address[] | undefined) =>
      (listing || []).map((a) => a.addr.toLowerCase()).filter((a) => a && !own.has(a));
    const answerAn = new Set(foreign(m?.reply_to?.length ? m.reply_to : m?.from));
    const all = new Set([...answerAn, ...foreign(m?.to), ...foreign(m?.cc)]);
    return all.size > answerAn.size;
  };

  /**
   * The identity one answers under: the one the mail went to.
   *
   * Whoever is written to as the treasurer answers as the treasurer — not under the address
   * that happens to be entered as the default. The search covers all recipient fields of the
   * original message; if nothing is found (a mailing list, an alias that does not exist here),
   * the default of the account stands.
   */
  const matchingIdentity = (): number | undefined => {
    const recipient = [...(m?.to || []), ...(m?.cc || [])]
      .map((a) => a.addr.toLowerCase());
    const hits = (identities || []).find((i) => recipient.includes(i.email.toLowerCase()));
    return hits?.id;
  };
  useEffect(() => {
    if (error) onError(error instanceof ApiError ? error.message : "Nachricht nicht lesbar");
  }, [error]);

  const start = useMutation({
    mutationFn: (v: { definition_id: number; attachment?: number }) =>
      api.post<{ instance_id: number }>(`${basis}/action`, { ...v, folder: folder }),
    onSuccess: (r) => setRun(`Ablauf gestartet (Vorgang ${r.instance_id})`),
    onError: (e) => onError(e instanceof ApiError ? e.message : "Aktion fehlgeschlagen"),
  });
  // All handgrips end the same way: the list and the folder counts no longer hold, and the
  // message is no longer where one was just reading it — so back to the list.
  const after = () => {
    qc.invalidateQueries({ queryKey: ["mail-list"] });
    qc.invalidateQueries({ queryKey: ["mail-folders"] });
    onBack();
  };
  const gonewrong = (was: string) => (e: unknown) =>
    onError(e instanceof ApiError ? e.message : `${was} fehlgeschlagen`);

  const move = useMutation({
    mutationFn: (target: string) => api.post(`${basis}/move`, { folder: folder, target }),
    onSuccess: after, onError: gonewrong("Verschieben"),
  });
  const archive = useMutation({
    mutationFn: () => api.post<{ folder: string }>(`${basis}/archive`, { folder: folder }),
    onSuccess: after, onError: gonewrong("Archivieren"),
  });
  const asSpam = useMutation({
    mutationFn: () => api.post(`${basis}/spam`, { folder: folder }),
    onSuccess: after, onError: gonewrong("Als Spam markieren"),
  });
  const noSpam = useMutation({
    mutationFn: () => api.post(`${basis}/not-spam`, { folder: folder }),
    onSuccess: after, onError: gonewrong("Zurückholen"),
  });
  const remove = useMutation({
    mutationFn: () => api.post(`${basis}/delete`, { folder: folder }),
    onSuccess: after, onError: gonewrong("Löschen"),
  });

  const forMail = (actions || []).filter((a) => a.scope !== "attachment");
  const forAttachment = (actions || []).filter((a) => a.scope === "attachment");

  return (
    <Area
      title={m?.subject || "…"}
      tools={<>
        <Rowbutton onClick={onBack}>← Liste</Rowbutton>
        <Rowbutton onClick={() => onReplies(answerFields(false))}>Antworten</Rowbutton>
        {/* Nur wenn er wirklich etwas anderes tut: gezählt wird, was nach Abzug der eigenen
            Adressen übrig bleibt. Sonst stünde bei einer Mail, die an mich und eine zweite
            own address, a button that does the same as its neighbour. */}
        {moreRecipient() && (
          <Rowbutton onClick={() => onReplies(answerFields(true))}>
            Allen antworten
          </Rowbutton>
        )}
        <Rowbutton onClick={() => onReplies({
          identity: String(matchingIdentity() ?? ""),
          subject: `Fwd: ${m?.subject || ""}`,
          text: `\n\n--- Weitergeleitete Nachricht ---\n`
            + `Von: ${(m?.from || []).map((a) => a.addr).join(", ")}\n`
            + `Datum: ${m?.date || ""}\nBetreff: ${m?.subject || ""}\n\n${m?.text || ""}`,
        })}>Weiterleiten</Rowbutton>
        {/* Archiv und Spam erscheinen nur, wenn am Konto ein Ziel dafür steht — ein Knopf,
            that explains on being pressed that it cannot is none. */}
        {(account?.archive_mode === "pattern" ? account?.archive_pattern : account?.folder_archive) && (
          <Rowbutton onClick={() => archive.mutate()}>📦 Archivieren</Rowbutton>
        )}
        {/* Im Spam-Ordner ist „als Spam markieren" keine Handlung, sondern eine
            repetition. What is missing there is the contradiction. */}
        {account?.folder_junk && (folder === account.folder_junk ? (
          <Rowbutton onClick={() => noSpam.mutate()} title={tr("mail.back_inbox_detection_learns")}>
            ✅ {tr("mail.not_spam")}
          </Rowbutton>
        ) : (
          <Rowbutton onClick={() => asSpam.mutate()}>🚫 Spam</Rowbutton>
        ))}
        <Rowbutton onClick={() => setMoveOpen(true)}>📁 Verschieben</Rowbutton>
        <div className="flex-1" />
        <Rowbutton danger onClick={() => remove.mutate()}>🗑 Löschen</Rowbutton>
      </>}
    >
      {m && (
        <>
          {/* Zwei Zeilen statt vier: wer geschrieben hat und wann, ist die Frage beim
              Öffnen — an wen und in Kopie liest man nur nach, wenn man antwortet. Die volle
              list stands in the tooltip, so that shortening swallows nothing. */}
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
                    <Tag>{Math.max(1, Math.round(a.size / 1024))} kB</Tag>
                    <Rowbutton onClick={() => setAttachmentOn(a)}
                      title={tr("mail.view")}>
                      {tr("mail.view")}
                    </Rowbutton>
                    {forAttachment.map((act) => (
                      <Rowbutton key={act.definition_id}
                        onClick={() => start.mutate({ definition_id: act.definition_id,
                                                        attachment: a.index })}>
                        {act.name}
                      </Rowbutton>
                    ))}
                  </div>
                </ListenLine>
              ))}
            </Listing>
          )}

          {m.html ? (
            <div className="space-y-2">
              <Tab active={view} onChoose={setView} selection={[
                ["html", "Formatiert"], ["text", "Nur Text"],
              ]} />
              {view === "html"
                ? <HtmlView html={m.html} remoteimages={m.remote_images} />
                : <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded border border-line bg-surface p-3 text-sm text-ink">
                    {m.text || "(kein Text)"}
                  </pre>}
            </div>
          ) : (
            <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded border border-line bg-surface p-3 text-sm text-ink">
              {m.text || "(kein Text)"}
            </pre>
          )}

          {forMail.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted">Aktionen:</span>
              {forMail.map((act) => (
                <Rowbutton key={act.definition_id} title={act.description}
                  onClick={() => start.mutate({ definition_id: act.definition_id })}>
                  {act.name}
                </Rowbutton>
              ))}
            </div>
          )}
          {run && <div className="text-xs text-green-400">{run}</div>}
        </>
      )}

      {attachmentOn && (
        <AttachmentDialog
          path={`${basis}/attachments/${attachmentOn.index}?folder=${encodeURIComponent(folder)}`}
          attachment={attachmentOn} onClose={() => setAttachmentOn(null)} />
      )}

      {moveOpen && (
        <Dialog title="Verschieben nach" onClose={() => setMoveOpen(false)}>
          {/* Der Baum wie in der Ordnerspalte, nur ohne Zähler: hier wird gewählt, nicht
              gestöbert. Ein Klick verschiebt und schließt — ein zweiter Knopf „Übernehmen"
              would be a step nobody needs. */}
          <Listing>
            {(allFolder || []).filter((o) => o.name !== folder).map((o) => (
              <ListenLine key={o.name} dense
                onClick={() => { setMoveOpen(false); move.mutate(o.name); }}>
                <div className="flex items-center gap-2"
                     style={{ paddingLeft: `${o.level * 0.85}rem` }}>
                  <span>{SPECIAL[o.special] || "📁"}</span>
                  <span className="min-w-0 flex-1 truncate">{o.display}</span>
                </div>
              </ListenLine>
            ))}
            {!allFolder?.length && <ListingEmpty>Keine weiteren Ordner.</ListingEmpty>}
          </Listing>
        </Dialog>
      )}
    </Area>
  );
}

function ComposeDialog({ accountId, start, onClose, onError: onError }: {
  accountId: number; start: Record<string, string>; onClose: () => void;
  onError: (m: string) => void;
}) {
  const { data: identities } = useQuery({
    queryKey: ["mail-identities", accountId],
    queryFn: () => api.get<MailIdentity[]>(`/mailbox/accounts/${accountId}/identities`),
  });
  const [identity, setIdentity] = useState<number | null>(null);
  const [f, setF] = useState({
    to: start.to || "", cc: start.cc || "", subject: start.subject || "",
    text: start.text || "", in_reply_to: start.in_reply_to || "",
  });
  const [attachments, setAttachments] = useState<
    { filename: string; content_type: string; data_base64: string; size: number }[]>([]);

  /** Read a file in. Base64 in the browser, because the server builds the message and not the
   *  browser — one place where draft and sending do the same thing. */
  const fileRead = (file: File) => new Promise<string>((done, gonewrong) => {
    const reader = new FileReader();
    reader.onload = () => done(String(reader.result).split(",")[1] || "");
    reader.onerror = () => gonewrong(reader.error);
    reader.readAsDataURL(file);
  });
  useEffect(() => {
    if (identity !== null || !identities?.length) return;
    // Order of the choice: what the caller passes in (the address that was written to),
    // otherwise the default of the account, otherwise the first one.
    const wanted = identities.find((i) => String(i.id) === (start.identity || ""));
    setIdentity((wanted || identities.find((i) => i.is_default) || identities[0]).id);
  }, [identities, identity, start.identity]);

  const base = () => ({
    identity_id: identity,
    to: f.to.split(",").map((s) => s.trim()).filter(Boolean),
    cc: f.cc.split(",").map((s) => s.trim()).filter(Boolean),
    subject: f.subject, text: f.text, in_reply_to: f.in_reply_to,
    attachments: attachments.map(({ filename, content_type, data_base64 }) =>
      ({ filename, content_type, data_base64 })),
  });
  const send = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${accountId}/send`, base()),
    onSuccess: onClose,
    onError: (e) => onError(e instanceof ApiError ? e.message : "Senden fehlgeschlagen"),
  });
  const draft = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${accountId}/draft`, base()),
    onSuccess: onClose,
    onError: (e) => onError(e instanceof ApiError ? e.message : "Entwurf fehlgeschlagen"),
  });

  return (
    // Held in place: whoever is writing a mail otherwise loses half the text on a misplaced
    // click. It is closed through ✕, cancel, draft or send.
    <Dialog wide hold title="Nachricht verfassen" onClose={onClose}
      foot={
        <div className="flex items-center gap-2">
          <Rowbutton onClick={() => draft.mutate()}>Als Entwurf sichern</Rowbutton>
          <div className="flex-1" />
          <DialogFoot onCancel={onClose} runs={send.isPending}
            disabled={!identity || !f.to.trim()} saveText="Senden"
            onSave={() => send.mutate()} />
        </div>
      }>
      <div className="space-y-3">
        {!identities?.length && (
          <Errorrow text="Dieses Konto hat noch keine Identität — ohne sie steht kein Absender fest." />
        )}
        <Field label="Von">
          <select value={identity ?? ""} className={INPUT_VALUE}
            onChange={(e) => setIdentity(Number(e.target.value))}>
            {identities?.map((i) => (
              <option key={i.id} value={i.id}>
                {i.display_name ? `${i.display_name} <${i.email}>` : i.email}
              </option>
            ))}
          </select>
        </Field>
        <Field label="An" hint="Mehrere Adressen mit Komma trennen.">
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
                      <Tag>{Math.max(1, Math.round(a.size / 1024))} kB</Tag>
                      <Rowbutton danger
                        onClick={() => setAttachments(attachments.filter((_, j) => j !== i))}>
                        Entfernen
                      </Rowbutton>
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
