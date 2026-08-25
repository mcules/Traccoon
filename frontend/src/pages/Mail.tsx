import { tr } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, fetchFile } from "../api";
import { usePageChrome } from "../pageChrome";
import { useAuth } from "../auth";
import { formatDateTime } from "../lib/formatTime";
import { AccountDialog, type MailAccount, type MailIdentity } from "../components/MailAccountsPanel";
import {
  Area, ConfirmDialog, Dialog, DialogFoot, INPUT_VALUE, Tag, Field, Errorrow,
  IconButton, Button, BUTTON, Listing, ListingEmpty, ListRow, Tab, Rowbutton, BUTTON_TEXT,
  Menu, MenuItem, MenuLine, Splitter} from "../components/ui";

/**
 * The mailbox.
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

/**
 * A mail stands on paper, in both themes.
 *
 * It is the one surface of this house that was not written here: a foreign mail brings its
 * own colours along, and they were chosen for white. On the dark ground of the interface a
 * signature in dark grey becomes unreadable, a logo with a white box around it glows like a
 * hole, and a table with light grey lines disappears altogether. The theme belongs to
 * Traccoon, the mail belongs to whoever wrote it.
 *
 * Paper is the ground, not a rule: a mail that paints its own background does so on top of
 * it. That is why nothing here is `!important`, and why the cleaning keeps the `style`
 * attribute (`_ALLOWED_ATTRIBUTE`) while dropping `<style>` blocks along with the scripts.
 */
const PAPER = "rounded border border-line bg-white p-3 text-sm text-neutral-900";

/** What one can ask of a single folder. `manage` is the way out into the overview. */
type FolderCommand = "read" | "empty" | "child" | "rename" | "delete";

/**
 * How wide the message list stands beside the reading pane.
 *
 * In the browser and not on the person: it is a decision about THIS screen, and the same
 * account on a laptop beside a 34-inch monitor wants two different answers. A number that
 * travelled with the login would be wrong on one of the two every time.
 */
const WIDTH_KEY = "traccoon_mail_list_width";
const WIDTH_STANDARD = 420;

function storedWidth(): number {
  const raw = Number(localStorage.getItem(WIDTH_KEY));
  return raw >= 240 && raw <= 1200 ? raw : WIDTH_STANDARD;
}

export default function Mail() {
  // The mailbox takes the whole window instead of the reading column: three columns beside
  // each other, and the one on the right is a mail somebody laid out for a screen.
  usePageChrome("Mail", [], undefined, "top", true);
  const qc = useQueryClient();
  const { user, refresh: userAgain } = useAuth();
  const [accountId, setAccountId] = useState<number | null>(null);
  const [folder, setFolder] = useState("INBOX");
  const [uid, setUid] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [err, setErr] = useState("");
  // What a folder command did. It stands until the next one, because "127 into the trash" is
  // exactly the sentence one wants to read again a moment later.
  const [notice, setNotice] = useState("");
  const [compose, setCompose] = useState<null | Record<string, string>>(null);
  const [settings, setSettings] = useState<MailAccount | null>(null);
  // The selection belongs to the page, not to the list: the handles above it and the folder
  // change below it both have to know about it.
  const [chosen, setChosen] = useState<number[]>([]);
  const [command, setCommand] = useState<
    { accountId: number; folder: Folder; kind: FolderCommand } | null>(null);
  const [manage, setManage] = useState<number | null>(null);
  const [listWidth, setListWidth] = useState(storedWidth);
  const listColumn = useRef<HTMLDivElement>(null);
  useEffect(() => { localStorage.setItem(WIDTH_KEY, String(listWidth)); }, [listWidth]);

  const { data: accounts } = useQuery({
    queryKey: ["mail-accounts"], queryFn: () => api.get<MailAccount[]>("/mailbox/accounts") });
  // So one can see where mail waits without going in. Asked rarely: behind every mailbox
  // sits an IMAP connection.
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
    // The mailbox opened last comes first, it is stored on the person and therefore applies
    // after a new login and on another machine as well.
    const noted = accounts.find((k) => k.id === user?.mail_last_account_id);
    setAccountId((noted || accounts.find((k) => k.enabled) || accounts[0]).id);
  }, [accounts, accountId, user]);

  /** A folder was clicked. It knows which mailbox it belongs to, so both are set at once. */
  const go = (id: number, name: string) => {
    const switched = id !== accountId;
    setAccountId(id);
    setFolder(name);
    setUid(null);
    setChosen([]);
    setSearch("");
    if (!switched) return;
    // The server notes it, and the browser has to hear about it: leaving the page throws the
    // choice away (the component goes with it), and on coming back the person in the context
    // is the one from the last login.
    api.post(`/mailbox/accounts/${id}/last`, {})
      .then(() => userAgain())
      .catch(() => {/* Remembering is no must */});
  };

  if (!accounts?.length) {
    return (
      <Area hint={tr("mail.no_mailbox_yet")}>
        <p className="text-sm text-muted">
          <span dangerouslySetInnerHTML={{ __html: tr("mail.accounts_in_account") }} />
        </p>
      </Area>
    );
  }

  const account = accounts.find((k) => k.id === accountId);

  return (
    // The page is a frame that ends at the lower edge of the window: what scrolls scrolls
    // inside a column. Below `sm` the columns stack and the page scrolls as a whole, because
    // three scrolling areas on a phone screen would be three windows into a keyhole.
    <div className="flex h-full min-h-0 w-full flex-col gap-3 overflow-y-auto sm:overflow-hidden">
      <Errorrow text={err} />
      {notice && <div className="shrink-0 text-xs text-green-400">{notice}</div>}

      {/* Three columns from `xl` on, as in every mail program: folders change rarely, the list
          often, the message with every click. Below that the reading pane takes the place of
          the list, because two columns of 300 pixels each are two columns nobody can read in.

          There is no bar above them any more. What stood in it belongs to something on the
          page: the mailboxes are the roots of the tree, their handles hang on their row, and
          the search belongs over the list it searches. That was a whole line of screen for
          decisions one makes twice a day. */}
      <div className="flex min-h-0 flex-1 flex-col gap-3 sm:flex-row">
        <div className="flex min-h-0 flex-col sm:w-60 sm:shrink-0">
          <Area fills tools={
            <Button variant="primary" wide onClick={() => setCompose({})}>
              {tr("mail.compose_button")}
            </Button>
          }>
            <AccountTree accounts={accounts} unread={unread?.accounts} accountId={accountId}
              folder={folder} onChoose={go}
              onCommand={(id, o, kind) => setCommand({ accountId: id, folder: o, kind })}
              onManage={setManage}
              onAccountCommand={(k, kind) => {
                if (kind === "settings") { setSettings(k); return; }
                qc.invalidateQueries({ queryKey: ["mail-unread"] });
                qc.invalidateQueries({ queryKey: ["mail-folders", k.id] });
                qc.invalidateQueries({ queryKey: ["mail-list"] });
              }} />
          </Area>
        </div>

        {/* The width is a variable, because it only applies from `xl` on: below that the list
            has the whole place to itself, and a number in pixels would take it away. */}
        <div ref={listColumn}
          style={{ ["--list" as any]: `${listWidth}px` }}
          className={`min-h-0 flex-col xl:flex xl:w-[var(--list)] xl:shrink-0 xl:grow-0 ${
            uid !== null ? "hidden sm:hidden xl:flex" : "flex flex-1"}`}>
          <MessagesListing accountId={accountId!} folder={folder} search={search}
            account={account} onOpen={setUid} onError={setErr} open={uid}
            chosen={chosen} onChosen={setChosen} onSearch={(q) => { setSearch(q); setUid(null); }} />
        </div>

        <Splitter leftOf={listColumn} value={listWidth} onChange={setListWidth}
          min={280} keepRight={420} standard={WIDTH_STANDARD}
          title={tr("mail.drag_the_seam")} />

        <div className={`min-h-0 min-w-0 flex-col xl:flex-1 ${
          uid === null ? "hidden xl:flex" : "flex flex-1"}`}>
          {uid === null ? (
            <Area fills>
              <div className="flex h-full items-center justify-center text-sm text-muted">
                {tr("mail.pick_a_message")}
              </div>
            </Area>
          ) : (
            <Readview accountId={accountId!} account={account}
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
      {command && (
        <FolderCommands accountId={command.accountId}
          account={accounts.find((k) => k.id === command.accountId)}
          folder={command.folder} kind={command.kind} onClose={() => setCommand(null)}
          onGone={() => { setCommand(null); go(command.accountId, "INBOX"); }}
          onEmptied={() => { setUid(null); setChosen([]); }}
          onDone={setNotice} onError={setErr} />
      )}
      {manage !== null && (
        <FolderManagement accountId={manage}
          account={accounts.find((k) => k.id === manage)}
          chosen={manage === accountId ? folder : "INBOX"}
          onClose={() => setManage(null)} onGone={() => go(manage, "INBOX")}
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
    onError: (e) => onError(e instanceof ApiError ? e.message : tr("common.save_failed")),
  });
  return (
    <AccountDialog start={{ ...account, imap_password: "", smtp_password: "" } as any}
      error="" runs={save.isPending} onClose={onClose}
      onSave={(f) => save.mutate(f)} />
  );
}

/**
 * Mailboxes and their folders in one tree.
 *
 * Formerly the mailbox was picked from a dropdown above everything else, and the tree showed
 * the folders of that one. That is one line of screen for a decision one makes twice a day,
 * and it hides the other mailboxes behind a click: whether something is waiting in the second
 * one was a question one had to ask.
 *
 * Here every mailbox is a root, as in Outlook and Thunderbird and for the same reason: the
 * folders belong to a mailbox, so they stand under it. A collapsed mailbox costs nothing but
 * its own line, and it carries its unread count, so one sees where something waits without
 * opening anything.
 *
 * The folders are only fetched for an expanded mailbox: behind every one sits an IMAP
 * connection and one STATUS per folder, and three mailboxes with thirty folders each would be
 * ninety questions for a picture nobody is looking at.
 */
function AccountTree({ accounts, unread, accountId, folder: folder, onChoose, onCommand,
                       onManage, onAccountCommand }: {
  accounts: MailAccount[];
  unread: { account_id: number; unseen: number | null }[] | undefined;
  accountId: number | null; folder: string;
  onChoose: (accountId: number, folder: string) => void;
  onCommand: (accountId: number, folder: Folder, kind: FolderCommand) => void;
  onManage: (accountId: number) => void;
  onAccountCommand: (account: MailAccount, kind: "refresh" | "settings") => void;
}) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  // The mailbox one is standing in is always open. Otherwise a click in the message list
  // would fold away the folder one is reading in.
  useEffect(() => {
    if (accountId === null) return;
    setOpen((old) => (old.has(accountId) ? old : new Set([...old, accountId])));
  }, [accountId]);

  return (
    <Listing>
      {accounts.map((k) => (
        <AccountBranch key={k.id} account={k}
          unseen={unread?.find((a) => a.account_id === k.id)?.unseen ?? null}
          open={open.has(k.id)} active={k.id === accountId}
          folder={k.id === accountId ? folder : ""}
          onToggle={() => setOpen((old) => {
            const fresh = new Set(old);
            fresh.has(k.id) ? fresh.delete(k.id) : fresh.add(k.id);
            return fresh;
          })}
          onChoose={(name) => onChoose(k.id, name)}
          onCommand={(o, kind) => onCommand(k.id, o, kind)}
          onManage={() => onManage(k.id)}
          onAccountCommand={(kind) => onAccountCommand(k, kind)} />
      ))}
    </Listing>
  );
}

/** One mailbox with its folders. Its own component because its folders are its own query. */
function AccountBranch({ account, unseen, open, active, folder: folder, onToggle, onChoose,
                         onCommand, onManage, onAccountCommand }: {
  account: MailAccount; unseen: number | null; open: boolean; active: boolean; folder: string;
  onToggle: () => void; onChoose: (name: string) => void;
  onCommand: (folder: Folder, kind: FolderCommand) => void;
  onManage: () => void;
  onAccountCommand: (kind: "refresh" | "settings") => void;
}) {
  const { data: folders } = useQuery({
    queryKey: ["mail-folders", account.id],
    queryFn: () => api.get<Folder[]>(`/mailbox/accounts/${account.id}/folders?counts=true`),
    enabled: open && account.enabled,
    refetchInterval: 60_000, refetchOnWindowFocus: true,
  });

  return (
    <>
      <ListRow dense onClick={onToggle}>
        <div className="group flex items-center gap-1.5">
          <span className={BUTTON_TEXT.secondary}>{open ? "▼" : "▶"}</span>
          <span className={`min-w-0 flex-1 truncate font-semibold ${
            active ? "text-brand" : "text-ink"}`}>
            {account.name}
          </span>
          {!account.enabled && <Tag>{tr("mail.off_short")}</Tag>}
          {/* A mailbox that does not answer right now says so instead of showing a zero:
              "nothing new" and "I do not know" are two different pieces of information. */}
          {unseen === null && account.enabled
            ? <span className="text-xs text-muted" title={tr("mail.mailbox_unreachable")}>?</span>
            : !!unseen && <Tag color="brand">{unseen}</Tag>}
          <Menu title={tr("mail.mailbox_handles", { name: account.name })} quiet={!active}>
            {(close) => (
              <>
                <MenuItem onClick={() => { close(); onAccountCommand("refresh"); }}>
                  ⟳ {tr("mail.look_now")}
                </MenuItem>
                <MenuItem onClick={() => { close(); onManage(); }}>
                  🗂 {tr("mail.manage_all_folders")}
                </MenuItem>
                <MenuLine />
                <MenuItem onClick={() => { close(); onAccountCommand("settings"); }}>
                  ⚙ {tr("mail.mailbox_settings_short")}
                </MenuItem>
              </>
            )}
          </Menu>
        </div>
      </ListRow>
      {open && (
        <FolderRows folders={folders} account={account} active={folder}
          onChoose={onChoose} onCommand={onCommand} onManage={onManage} />
      )}
    </>
  );
}

/**
 * The folders of one mailbox, with indentation, expanding and unread counts.
 *
 * A flat list is enough as long as somebody has five folders. With a grown mailbox that
 * archives by year, project and mailing list it is a wall: one looks for the folder one is
 * looking for and cannot find it again among thirty identical-looking rows. What is collapsed
 * is the branch, not the access: a click on the parent folder opens it anyway.
 *
 * What one can DO with a folder hangs on a `⋯` in its row, not on the right mouse button:
 * a web page that swallows the browser menu surprises people, and a phone has no right
 * button. The sign keeps quiet until the row is under the pointer, so that thirty folders do
 * not become thirty signs.
 */
function FolderRows({ folders: folder, account, active, onChoose, onCommand, onManage }: {
  folders: Folder[] | undefined; account: MailAccount; active: string;
  onChoose: (name: string) => void;
  onCommand: (folder: Folder, kind: FolderCommand) => void;
  onManage: () => void;
}) {
  // Start collapsed: a grown mailbox has archives by year and mailing lists by sender, and one
  // does not want to see all of those when opening it. What one needs daily are the six
  // special folders, the rest is one click away.
  const [on, setOn] = useState<Set<string>>(new Set());
  if (!folder) {
    return <ListRow dense><span className="pl-6 text-xs text-muted">
      {tr("mail.folders_loading")}</span></ListRow>;
  }

  const hasChildren = (o: Folder) => folder.some((k) => k.parent === o.name);
  /**
   * Unread of a branch: the folder itself and everything below it.
   *
   * Without that a collapsed branch would stay mute: one would see "Archive" without a count
   * and would not know that something unread lies in `Archive/2026/08`. That is why the sum
   * stands on the collapsed folder and only its own on the expanded one, otherwise one would
   * count the same message again at every level.
   */
  const sum_total = (o: Folder): number => folder
    .filter((k) => k.parent === o.name)
    .reduce((number, k) => number + sum_total(k), o.unseen || 0);
  // Visible is whatever has all its ancestors expanded. The active folder always stays so,
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
  /** Which role this folder plays in the account. Those are the ones one must not touch. */
  const role = (o: Folder): string => {
    if (o.name.toUpperCase() === "INBOX") return tr("mail.role_inbox");
    const roles: [string | undefined, string][] = [
      [account.folder_sent, tr("mail.role_sent")], [account.folder_drafts, tr("mail.role_drafts")],
      [account.folder_trash, tr("mail.role_trash")], [account.folder_junk, tr("mail.role_junk")],
      [account.folder_archive, tr("mail.role_archive")]];
    return roles.find(([n]) => n && n === o.name)?.[1] || "";
  };

  return (
    <>
      {folder.filter(visible).map((o) => {
        const fixed = role(o);
        return (
          <ListRow key={o.name} dense onClick={() => onChoose(o.name)}>
            {/* Fixed columns instead of flex with placeholders: only that way does the folder
                icon of every line sit in the same place, whether or not a fold arrow stands
                in front of it. The first column is the indentation under the mailbox. */}
            <div className="group grid grid-cols-[0.75rem_1.25rem_minmax(0,1fr)_auto_auto] items-center gap-1.5"
                 style={{ paddingLeft: `${0.9 + o.level * 0.85}rem` }}>
              {hasChildren(o) ? (
                <button onClick={(e) => { e.stopPropagation(); toggle(o.name); }}
                  className={BUTTON_TEXT.secondary}
                  title={on.has(o.name) ? tr("mail.collapse") : tr("mail.expand")}>
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
                // Collapsed and something only in the children: the count belongs to the
                // branch, not to the folder, shown more quietly so one sees the difference.
                const onlyChildren = to && !o.unseen;
                return (
                  <Tag color={onlyChildren ? "neutral" : "brand"}
                    title={onlyChildren ? tr("mail.in_subfolders") : tr("mail.unread")}>
                    {number}
                  </Tag>
                );
              })()}
              <Menu title={tr("mail.folder_handles", { folder: o.display })}
                    quiet={o.name !== active}>
                {(close) => (
                  <>
                    <MenuItem onClick={() => { close(); onCommand(o, "read"); }}>
                      ✓ {tr("mail.mark_all_read")}
                    </MenuItem>
                    <MenuItem onClick={() => { close(); onCommand(o, "empty"); }}>
                      🧹 {tr("mail.empty_folder_plain")}
                    </MenuItem>
                    <MenuLine />
                    <MenuItem onClick={() => { close(); onCommand(o, "child"); }}>
                      {tr("mail.new_subfolder")}
                    </MenuItem>
                    {/* Switched off, not gone: a handle that is missing looks like one that
                        does not exist, and the reason belongs where one reaches for it. */}
                    <MenuItem disabled={!!fixed} title={fixed ? tr("mail.is_role_folder", { role: fixed }) : undefined}
                      onClick={() => { close(); onCommand(o, "rename"); }}>
                      {tr("mail.rename_dots")}
                    </MenuItem>
                    <MenuItem danger disabled={!!fixed}
                      title={fixed ? tr("mail.is_role_folder", { role: fixed }) : undefined}
                      onClick={() => { close(); onCommand(o, "delete"); }}>
                      {tr("mail.delete_folder_plain")}
                    </MenuItem>
                    <MenuLine />
                    <MenuItem onClick={() => { close(); onManage(); }}>
                      ⚙ {tr("mail.manage_all_folders")}
                    </MenuItem>
                  </>
                )}
              </Menu>
            </div>
          </ListRow>
        );
      })}
    </>
  );
}

/**
 * What was asked of a folder, asked back before it happens.
 *
 * Every one of these is rare and none of them is free: emptying moves a thousand mails,
 * renaming changes a path that other programs have bookmarked. So each one gets its question,
 * and the question says what will happen, not that something will.
 */
function FolderCommands({ accountId, account, folder: folder, kind, onClose, onGone, onEmptied,
                          onDone, onError: onError }: {
  accountId: number; account: MailAccount | undefined; folder: Folder; kind: FolderCommand;
  onClose: () => void; onGone: () => void; onEmptied: () => void;
  onDone: (text: string) => void; onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(kind === "rename" ? folder.display : "");
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["mail-folders"] });
    qc.invalidateQueries({ queryKey: ["mail-list"] });
    qc.invalidateQueries({ queryKey: ["mail-unread"] });
  };
  const gonewrong = (was: string) => (e: unknown) => {
    onClose();
    onError(e instanceof ApiError ? e.message : tr("mail.failed_suffix", { what: was }));
  };

  const read = useMutation({
    mutationFn: () => api.post<{ marked: number }>(
      `/mailbox/accounts/${accountId}/folders/read-all`, { folder: folder.name }),
    onSuccess: (r) => {
      onDone(r.marked ? tr("mail.marked_read_n", { n: r.marked }) : tr("mail.nothing_unread"));
      refresh();
      onClose();
    },
    onError: gonewrong(tr("mail.marking")),
  });
  const empty = useMutation({
    mutationFn: () => api.post<{ deleted: number; target: string }>(
      `/mailbox/accounts/${accountId}/folders/empty`, { folder: folder.name }),
    onSuccess: (r) => {
      onDone(!r.deleted ? tr("mail.folder_was_empty")
        : r.target ? tr("mail.emptied_into", { n: r.deleted, folder: r.target })
                   : tr("mail.emptied_finally", { n: r.deleted }));
      refresh();
      onEmptied();
      onClose();
    },
    onError: gonewrong(tr("mail.emptying")),
  });
  const child = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${accountId}/folders/create`,
                                { name: name.trim(), parent: folder.name }),
    onSuccess: () => { refresh(); onClose(); },
    onError: gonewrong(tr("mail.creating")),
  });
  const rename = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${accountId}/folders/rename`,
                                { folder: folder.name, name: name.trim() }),
    onSuccess: () => { refresh(); onClose(); },
    onError: gonewrong(tr("mail.renaming")),
  });
  const drop = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${accountId}/folders/delete`,
                                { folder: folder.name }),
    onSuccess: () => { refresh(); onGone(); },
    onError: gonewrong(tr("mail.delete")),
  });

  if (kind === "read") {
    return (
      <ConfirmDialog
        title={tr("mail.mark_all_read_q")}
        text={tr("mail.everything_unread_in", { folder: folder.display })}
        hint={tr("mail.undo_message_by_message")}
        danger={false} confirmText={tr("mail.mark")} runs={read.isPending}
        onClose={onClose} onConfirm={() => read.mutate()} />
    );
  }
  if (kind === "empty") {
    // Whether emptying moves or really deletes is the same question as with a single message,
    // and the answer belongs in the safety question, afterwards it is too late for it.
    const finally_ = !account?.folder_trash || folder.name === account.folder_trash;
    return (
      <ConfirmDialog
        title={tr("mail.empty_folder_q", { folder: folder.display })}
        text={finally_ ? tr("mail.empty_finally_text", { folder: folder.display })
                        : tr("mail.empty_into_trash_text",
                             { folder: folder.display, trash: account!.folder_trash })}
        hint={tr("mail.folder_itself_stays")}
        confirmText={finally_ ? tr("mail.delete_finally") : tr("mail.empty")}
        runs={empty.isPending}
        onClose={onClose} onConfirm={() => empty.mutate()} />
    );
  }
  if (kind === "delete") {
    return (
      <ConfirmDialog
        title={tr("mail.delete_folder_q", { folder: folder.display })}
        text={folder.total ? tr("mail.folder_disappears_with", { n: folder.total })
                            : tr("mail.folder_disappears")}
        hint={tr("mail.final_special_protected")}
        confirmText={tr("mail.delete_finally")} runs={drop.isPending}
        onClose={onClose} onConfirm={() => drop.mutate()} />
    );
  }

  const creating = kind === "child";
  const run = creating ? child : rename;
  return (
    <Dialog title={creating ? tr("mail.new_subfolder_in", { folder: folder.display })
                            : tr("mail.rename_folder_q", { folder: folder.display })}
      onClose={onClose}
      foot={<DialogFoot onCancel={onClose} runs={run.isPending} disabled={!name.trim()}
              saveText={creating ? tr("mail.create") : tr("mail.rename")}
              onSave={() => run.mutate()} />}>
      <Field label={tr("mail.folder_name")}
             hint={creating ? tr("mail.below_this_folder", { folder: folder.display })
                            : tr("mail.stays_where_it_hangs")}>
        <input value={name} autoFocus className={INPUT_VALUE}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) run.mutate(); }} />
      </Field>
    </Dialog>
  );
}

/**
 * Creating, renaming, moving and deleting folders.
 *
 * All four in one place, because they are the same question asked four times: where does this
 * folder belong. Renaming and moving are even the same command on IMAP: the name IS the
 * path, which is why one row here carries both the name and the folder above it.
 *
 * The special folders (inbox, sent, drafts, trash, spam, archive) can be seen but not touched:
 * they hang on the buttons of the mailbox, and a renamed trash is a delete button that no
 * longer works. Whoever really wants to move one changes it in the settings of the account
 * first, because there it says which folder plays which role.
 */
function FolderManagement({ accountId, account, chosen, onClose, onGone, onError: onError }: {
  accountId: number; account: MailAccount | undefined; chosen: string;
  onClose: () => void; onGone: () => void; onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [parent, setParent] = useState(chosen === "INBOX" ? "" : chosen);
  const [rename, setRename] = useState<Folder | null>(null);
  const [renameName, setRenameName] = useState("");
  const [renameParent, setRenameParent] = useState("");
  const [remove, setRemove] = useState<Folder | null>(null);

  const { data: folder } = useQuery({
    queryKey: ["mail-folders", accountId],
    queryFn: () => api.get<Folder[]>(`/mailbox/accounts/${accountId}/folders?counts=true`),
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["mail-folders"] });
    qc.invalidateQueries({ queryKey: ["mail-list"] });
  };
  const gonewrong = (was: string) => (e: unknown) =>
    onError(e instanceof ApiError ? e.message : tr("mail.failed_suffix", { what: was }));

  const create = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${accountId}/folders/create`,
                                { name: name.trim(), parent: parent }),
    onSuccess: () => { setName(""); refresh(); },
    onError: gonewrong(tr("mail.creating")),
  });
  const move = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${accountId}/folders/rename`, {
      folder: rename!.name, name: renameName.trim(), parent: renameParent }),
    onSuccess: () => { setRename(null); refresh(); },
    onError: gonewrong(tr("mail.renaming")),
  });
  const drop = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${accountId}/folders/delete`,
                                { folder: remove!.name }),
    onSuccess: () => {
      const gone = remove!.name;
      setRemove(null);
      refresh();
      // What one was standing in is no longer there, and the list beside it would otherwise
      // go on asking a folder that does not exist any more.
      if (gone === chosen) onGone();
    },
    onError: (e) => { setRemove(null); gonewrong(tr("mail.delete"))(e); },
  });

  /** Which folders play a role in this account. Those stay untouched. */
  const role = (o: Folder): string => {
    if (o.name.toUpperCase() === "INBOX") return tr("mail.role_inbox");
    const roles: [string | undefined, string][] = [
      [account?.folder_sent, tr("mail.role_sent")], [account?.folder_drafts, tr("mail.role_drafts")],
      [account?.folder_trash, tr("mail.role_trash")], [account?.folder_junk, tr("mail.role_junk")],
      [account?.folder_archive, tr("mail.role_archive")]];
    return roles.find(([n]) => n && n === o.name)?.[1] || "";
  };

  /** The choice of a parent folder. `""` is the root, a folder does not have to hang below
   *  something. Whoever is being renamed cannot become their own parent. */
  const parentChoice = (without?: string) => (
    <>
      <option value="">{tr("mail.top_level")}</option>
      {(folder || []).filter((o) => !without
                              || (o.name !== without && !o.name.startsWith(without + o.delimiter)))
        .map((o) => (
          <option key={o.name} value={o.name}>{"\u00a0\u00a0".repeat(o.level)}{o.display}</option>
        ))}
    </>
  );

  return (
    <Dialog wide title={tr("mail.manage_folders_title")} onClose={onClose} foot={
      <Button onClick={onClose}>{tr("common.close")}</Button>
    }>
      <div className="space-y-4">
        <Listing>
          {(folder || []).map((o) => {
            const fixed = !!role(o);
            const editing = rename?.name === o.name;
            return (
              <ListRow key={o.name} dense>
                {editing ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <input value={renameName} autoFocus className={`${INPUT_VALUE} max-w-[12rem]`}
                      onChange={(e) => setRenameName(e.target.value)} />
                    <span className="text-xs text-muted">{tr("mail.below")}</span>
                    <select value={renameParent} className={`${INPUT_VALUE} max-w-[14rem]`}
                      onChange={(e) => setRenameParent(e.target.value)}>
                      {parentChoice(o.name)}
                    </select>
                    <div className="flex-1" />
                    <Rowbutton onClick={() => move.mutate()}>{tr("common.save")}</Rowbutton>
                    <Rowbutton onClick={() => setRename(null)}>{tr("common.cancel")}</Rowbutton>
                  </div>
                ) : (
                  <div className="flex flex-wrap items-center gap-2"
                       style={{ paddingLeft: `${o.level * 0.85}rem` }}>
                    <span>{SPECIAL[o.special] || "📁"}</span>
                    <span className="min-w-0 flex-1 truncate">{o.display}</span>
                    <span className="shrink-0 text-xs text-muted">
                      {o.total} {tr("mail.messages")}
                    </span>
                    {fixed ? (
                      <Tag title={tr("mail.role_protected")}>{role(o)}</Tag>
                    ) : (
                      <>
                        <Rowbutton onClick={() => {
                          setRename(o);
                          setRenameName(o.display);
                          setRenameParent(o.parent);
                        }}>{tr("mail.rename")}</Rowbutton>
                        <Rowbutton danger onClick={() => setRemove(o)}>{tr("mail.delete")}</Rowbutton>
                      </>
                    )}
                  </div>
                )}
              </ListRow>
            );
          })}
          {!folder?.length && <ListingEmpty>{tr("mail.folders_loading")}</ListingEmpty>}
        </Listing>

        {/* Creating stands below the list and not in a dialog of its own: one sees while
            typing what is already there, which is exactly the question one has at that
            moment. */}
        <form className="flex flex-wrap items-end gap-2"
              onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
          <div className="min-w-[10rem] flex-1">
            <Field label={tr("mail.new_folder")}>
              <input value={name} onChange={(e) => setName(e.target.value)}
                placeholder={tr("mail.folder_name")} className={INPUT_VALUE} />
            </Field>
          </div>
          <div className="min-w-[10rem] flex-1">
            <Field label={tr("mail.below_folder")}>
              <select value={parent} onChange={(e) => setParent(e.target.value)}
                className={INPUT_VALUE}>
                {parentChoice()}
              </select>
            </Field>
          </div>
          <Button variant="primary" type="submit" disabled={!name.trim()} runs={create.isPending}>
            {tr("mail.create")}
          </Button>
        </form>
      </div>

      {remove && (
        <ConfirmDialog
          title={tr("mail.delete_folder_q", { folder: remove.display })}
          text={remove.total
            ? tr("mail.folder_disappears_with", { n: remove.total })
            : tr("mail.folder_disappears")}
          hint={tr("mail.final_special_protected")}
          confirmText={tr("mail.delete_finally")} runs={drop.isPending}
          onClose={() => setRemove(null)} onConfirm={() => drop.mutate()} />
      )}
    </Dialog>
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
        body { font: 14px/1.5 system-ui, sans-serif; color: #1c1c1c; background: #ffffff;
               margin: 12px; word-break: break-word; }
        a { color: #0645ad; } img { max-width: 100%; height: auto; }
        table { max-width: 100%; } blockquote { border-left: 2px solid #d0d7de;
               margin: 0; padding-left: 12px; color: #57606a; }
      </style></head><body>${content}</body></html>`;

  return (
    <div className="space-y-2">
      {remoteimages && !images && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          {tr("mail.remote_images_blocked")}
          <Rowbutton onClick={() => setImages(true)}>{tr("mail.load_images")}</Rowbutton>
        </div>
      )}
      <iframe
        title={tr("mail.message")}
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        srcDoc={document}
        className="h-[60vh] w-full rounded border border-line bg-white"
      />
    </div>
  );
}

/**
 * The list of a folder, and what one can do with several of them at once.
 *
 * The checkbox is the second way to touch a row: a click still opens the mail, because that
 * is what one comes for. Only when something is ticked does the list turn into a work
 * surface, and then the handles stand where the counter stood, not in a bar that was there
 * all along waiting for a use.
 *
 * Shift takes a range, as in every list in the world. Without it, tidying up thirty
 * newsletters means thirty clicks, which is exactly the moment one stops using a selection.
 */
function MessagesListing({ accountId, folder: folder, search, account, onOpen: onOpen_it,
                           onError: onError, open: open, chosen, onChosen, onSearch }: {
  accountId: number; folder: string; search: string; account: MailAccount | undefined;
  onOpen: (uid: number) => void; onError: (m: string) => void;
  open: number | null; chosen: number[]; onChosen: (uids: number[]) => void;
  onSearch: (q: string) => void;
}) {
  const qc = useQueryClient();
  const [page, setPage] = useState(0);
  const [moveOpen, setMoveOpen] = useState(false);
  // The search field only takes the row when it is wanted. It searches THIS folder, which is
  // why it stands over it and not in a bar above the whole page.
  const [asking, setAsking] = useState(false);
  const [question, setQuestion] = useState(search);
  useEffect(() => { setQuestion(search); if (!search) setAsking(false); }, [search, folder]);
  // Where the last tick sat, for the range that shift asks for.
  const [anchor, setAnchor] = useState<number | null>(null);
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
    if (error) onError(error instanceof ApiError ? error.message : tr("mail.mailbox_unreachable"));
  }, [error]);

  const messages = data?.messages || [];
  const ticked = new Set(chosen);
  const allTicked = messages.length > 0 && messages.every((m) => ticked.has(m.uid));

  const tick = (m: Header, index: number, shift: boolean) => {
    if (shift && anchor !== null) {
      const from = messages.findIndex((k) => k.uid === anchor);
      if (from >= 0) {
        const [a, b] = from < index ? [from, index] : [index, from];
        const range = messages.slice(a, b + 1).map((k) => k.uid);
        // The range follows what the anchor did: ticking it ticks, unticking unticks.
        const set = new Set(chosen);
        const add = !ticked.has(m.uid);
        range.forEach((uid) => (add ? set.add(uid) : set.delete(uid)));
        onChosen([...set]);
        return;
      }
    }
    const set = new Set(chosen);
    ticked.has(m.uid) ? set.delete(m.uid) : set.add(m.uid);
    setAnchor(m.uid);
    onChosen([...set]);
  };

  const after = () => {
    onChosen([]);
    setAnchor(null);
    qc.invalidateQueries({ queryKey: ["mail-list"] });
    qc.invalidateQueries({ queryKey: ["mail-folders"] });
    qc.invalidateQueries({ queryKey: ["mail-unread"] });
  };
  const bulk = useMutation({
    mutationFn: (v: { action: string; target?: string; flag?: string; on?: boolean }) =>
      api.post<{ done: number }>(`/mailbox/accounts/${accountId}/messages/bulk`,
                                  { folder: folder, uids: chosen, ...v }),
    onSuccess: after,
    onError: (e) => onError(e instanceof ApiError ? e.message : tr("mail.action_failed")),
  });

  const archivable = account?.archive_mode === "pattern"
    ? !!account?.archive_pattern : !!account?.folder_archive;

  return (
    <Area
      fills
      // One row above the list, not three. The folder name, the tick for all of them and the
      // count fit beside each other, and every line that goes is a line of mail that stays.
      tools={<>
        {messages.length > 0 && chosen.length > 0 && (
          <input type="checkbox" checked={allTicked} className="h-4 w-4 accent-brand"
            title={tr("mail.choose_all_on_page")} aria-label={tr("mail.choose_all_on_page")}
            onChange={() => onChosen(allTicked ? [] : messages.map((m) => m.uid))} />
        )}
        {asking || search ? (
          // Searching takes the row over, as the selection does: while typing, how many
          // messages the folder holds is not the question of the moment.
          <form className="flex min-w-0 flex-1 items-center gap-2"
                onSubmit={(e) => { e.preventDefault(); onSearch(question.trim()); }}>
            <input value={question} autoFocus placeholder={tr("mail.search_in_folder", { folder })}
              className={`${INPUT_VALUE} min-w-0 flex-1`}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Escape") { setAsking(false); onSearch(""); } }} />
            <Rowbutton onClick={() => onSearch(question.trim())}>{tr("mail.search_go")}</Rowbutton>
            <Rowbutton onClick={() => { setQuestion(""); setAsking(false); onSearch(""); }}>
              {tr("common.close")}
            </Rowbutton>
          </form>
        ) : chosen.length > 0 ? (
        // The selection takes the row over: with something ticked, how many messages the
        // folder holds is not the question of the moment.
        <>
          <Tag color="brand">{tr("mail.n_chosen", { n: chosen.length })}</Tag>
          <Rowbutton onClick={() => bulk.mutate({ action: "flag", flag: "\\Seen", on: true })}>
            {tr("mail.mark_read_short")}
          </Rowbutton>
          <Rowbutton onClick={() => bulk.mutate({ action: "flag", flag: "\\Seen", on: false })}>
            {tr("mail.mark_unread_short")}
          </Rowbutton>
          {archivable && (
            <Rowbutton onClick={() => bulk.mutate({ action: "archive" })}>
              {tr("mail.archive_button")}
            </Rowbutton>
          )}
          <Rowbutton onClick={() => setMoveOpen(true)}>{tr("mail.move_button")}</Rowbutton>
          <Rowbutton danger onClick={() => bulk.mutate({ action: "delete" })}>
            {tr("mail.delete_3")}
          </Rowbutton>
          <div className="flex-1" />
          <Rowbutton onClick={() => { onChosen([]); setAnchor(null); }}>
            {tr("mail.selection_off")}
          </Rowbutton>
        </>
      ) : (
        <>
          {messages.length > 0 && (
            <input type="checkbox" checked={allTicked} className="h-4 w-4 accent-brand"
              title={tr("mail.choose_all_on_page")} aria-label={tr("mail.choose_all_on_page")}
              onChange={() => onChosen(allTicked ? [] : messages.map((m) => m.uid))} />
          )}
          <span className="min-w-0 truncate text-sm font-semibold text-ink">{folder}</span>
          <div className="flex-1" />
          <span className="text-xs text-muted">
            {data?.total ?? 0} {tr("mail.messages")}
          </span>
          <IconButton icon="🔍" title={tr("mail.search_in_folder", { folder })}
            onClick={() => setAsking(true)} />
        </>
      )}
      </>}
    >
      {search && (
        <div className="flex items-center gap-2 text-xs text-muted">
          <Tag color="brand">{tr("mail.search_label")}: {search}</Tag>
          {data?.total ?? 0} {tr("mail.hits")}
        </div>
      )}
      <Listing>
        {messages.map((m, index) => (
          <ListRow key={m.uid} dense onClick={() => onOpen_it(m.uid)}>
            <div className="flex items-start gap-2">
              {/* Its own click target, and it must not open the mail: a tick is a decision
                  about the row, not a way into it. */}
              <input type="checkbox" checked={ticked.has(m.uid)}
                className="mt-1 h-4 w-4 shrink-0 accent-brand"
                onClick={(e) => { e.stopPropagation(); tick(m, index, (e as any).shiftKey); }}
                onChange={() => {/* der Klick oben entscheidet */}} />
              <div className="min-w-0 flex-1">
                <div className={`flex flex-wrap items-baseline gap-x-3 gap-y-1 ${
                  m.uid === open ? "text-brand" : ""}`}>
                  <span className={`min-w-0 flex-1 truncate ${
                    m.uid === open ? "font-medium" : m.seen ? "text-ink" : "font-semibold text-ink"}`}>
                    {m.subject || tr("mail.no_subject")}
                  </span>
                  {!m.seen && <Tag color="brand">{tr("mail.new_short")}</Tag>}
                  {m.has_attachment && <span title={tr("mail.has_attachment")}>📎</span>}
                  {m.flagged && <span title={tr("mail.flagged")}>⭐</span>}
                  {m.answered && <span title={tr("mail.answered")}>↩</span>}
                  <span className="shrink-0 text-xs text-muted">{formatDateTime(m.date)}</span>
                </div>
                <div className="mt-0.5 truncate text-xs text-muted">{m.from}</div>
              </div>
            </div>
          </ListRow>
        ))}
        {isLoading && <ListingEmpty>{tr("mail.loading")}</ListingEmpty>}
        {!isLoading && !messages.length && <ListingEmpty>{tr("mail.nothing_in_folder")}</ListingEmpty>}
      </Listing>
      {(data?.total ?? 0) > limit && (
        <div className="mt-3 flex items-center gap-2">
          <Rowbutton onClick={() => setPage(Math.max(0, page - 1))}>{tr("mail.newer")}</Rowbutton>
          <span className="text-xs text-muted">
            {page * limit + 1}–{Math.min((page + 1) * limit, data!.total)} {tr("mail.of")} {data!.total}
          </span>
          <Rowbutton onClick={() => setPage(page + 1)}>{tr("mail.older")}</Rowbutton>
        </div>
      )}

      {moveOpen && (
        <FolderChoice accountId={accountId} without={folder} onClose={() => setMoveOpen(false)}
          onChoose={(target) => { setMoveOpen(false); bulk.mutate({ action: "move", target }); }} />
      )}
    </Area>
  );
}

/**
 * "Move to…": the tree as in the folder column, only without counters.
 *
 * Here one chooses, one does not browse. A click moves and closes, a second "apply" button
 * would be a step nobody needs.
 */
function FolderChoice({ accountId, without, onClose, onChoose }: {
  accountId: number; without: string; onClose: () => void; onChoose: (target: string) => void;
}) {
  const { data: folder } = useQuery({
    queryKey: ["mail-folders", accountId],
    queryFn: () => api.get<Folder[]>(`/mailbox/accounts/${accountId}/folders?counts=true`),
  });
  return (
    <Dialog title={tr("mail.move_to")} onClose={onClose}>
      <Listing>
        {(folder || []).filter((o) => o.name !== without).map((o) => (
          <ListRow key={o.name} dense onClick={() => onChoose(o.name)}>
            <div className="flex items-center gap-2"
                 style={{ paddingLeft: `${o.level * 0.85}rem` }}>
              <span>{SPECIAL[o.special] || "📁"}</span>
              <span className="min-w-0 flex-1 truncate">{o.display}</span>
            </div>
          </ListRow>
        ))}
        {!folder?.length && <ListingEmpty>{tr("mail.no_further_folders")}</ListingEmpty>}
      </Listing>
    </Dialog>
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
      .catch((e) => setError(e instanceof ApiError ? e.message : tr("mail.attachment_unloadable")));
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
  const { data: identities } = useQuery({
    queryKey: ["mail-identities", accountId],
    queryFn: () => api.get<MailIdentity[]>(`/mailbox/accounts/${accountId}/identities`),
  });

  /**
   * The recipients of a reply.
   *
   * `all` additionally takes along everyone who was already on it — minus one's own
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
    if (error) onError(error instanceof ApiError ? error.message : tr("mail.message_unreadable"));
  }, [error]);

  const start = useMutation({
    mutationFn: (v: { definition_id: number; attachment?: number; all?: boolean }) =>
      api.post<{ instance_id: number; runs: { instance_id: number }[] }>(
        `${basis}/action`, { ...v, folder: folder }),
    // One run per file: the message says how many were started, not which. The details
    // stand in the flows, and twenty numbers here would be no news but a wall.
    onSuccess: (r) => setRun((r.runs?.length || 1) > 1
      ? tr("mail.runs_started", { n: r.runs.length })
      : tr("mail.run_started", { id: r.instance_id })),
    onError: (e) => onError(e instanceof ApiError ? e.message : tr("mail.action_failed")),
  });

  /**
   * Three seconds open means read.
   *
   * Not on opening: whoever clicks through a list and goes straight on has not read anything,
   * and a mail that loses its mark on the way past is one that will never be found again.
   * Three seconds is long enough for a misgrab and short enough not to have to think about it.
   *
   * Only ever once per mail: the answer sets `seen` in the loaded message, so the timer does
   * not start a second time, and the counters beside it hold the old state and have to look
   * again.
   */
  const asRead = useMutation({
    mutationFn: () => api.post(`${basis}/flag`, { folder: folder, flag: "\\Seen", on: true }),
    onSuccess: () => {
      qc.setQueryData<Message>(["mail-message", accountId, folder, uid],
        (old) => (old ? { ...old, seen: true } : old));
      qc.invalidateQueries({ queryKey: ["mail-list"] });
      qc.invalidateQueries({ queryKey: ["mail-folders"] });
      qc.invalidateQueries({ queryKey: ["mail-unread"] });
    },
    // A failed mark is no message: it is repaired by opening it again, and an error banner
    // over something nobody asked for would only be in the way.
    onError: () => {/* quiet */},
  });
  useEffect(() => {
    if (!m || m.seen) return;
    const clock = setTimeout(() => asRead.mutate(), 3000);
    return () => clearTimeout(clock);
  }, [accountId, folder, m?.uid, m?.seen]);
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
    onSuccess: after, onError: gonewrong(tr("mail.move")),
  });
  const archive = useMutation({
    mutationFn: () => api.post<{ folder: string }>(`${basis}/archive`, { folder: folder }),
    onSuccess: after, onError: gonewrong(tr("mail.archive")),
  });
  const asSpam = useMutation({
    mutationFn: () => api.post(`${basis}/spam`, { folder: folder }),
    onSuccess: after, onError: gonewrong(tr("mail.mark_as_spam")),
  });
  const noSpam = useMutation({
    mutationFn: () => api.post(`${basis}/not-spam`, { folder: folder }),
    onSuccess: after, onError: gonewrong(tr("mail.recall")),
  });
  const remove = useMutation({
    mutationFn: () => api.post(`${basis}/delete`, { folder: folder }),
    onSuccess: after, onError: gonewrong(tr("mail.delete_2")),
  });

  const forMail = (actions || []).filter((a) => a.scope !== "attachment");
  const forAttachment = (actions || []).filter((a) => a.scope === "attachment");

  return (
    <Area
      fills
      title={m?.subject || "…"}
      tools={<>
        {/* Back to the list is only a way where the list had to give up its place. In three
            columns it stands beside this one and the button would point at itself. */}
        <span className="xl:hidden"><Rowbutton onClick={onBack}>{tr("mail.back_to_list")}</Rowbutton></span>
        <Rowbutton onClick={() => onReplies(answerFields(false))}>{tr("mail.reply")}</Rowbutton>
        {/* Only when it really does something different: what counts is what is left after
            one's own addresses are taken off. Otherwise a mail addressed to me and a second
            own address, a button that does the same as its neighbour. */}
        {moreRecipient() && (
          <Rowbutton onClick={() => onReplies(answerFields(true))}>
            {tr("mail.reply_all")}
          </Rowbutton>
        )}
        <Rowbutton onClick={() => onReplies({
          identity: String(matchingIdentity() ?? ""),
          subject: `Fwd: ${m?.subject || ""}`,
          text: `\n\n${tr("mail.forwarded_message")}\n`
            + `${tr("mail.from_label")}: ${(m?.from || []).map((a) => a.addr).join(", ")}\n`
            + `${tr("mail.date_label")}: ${m?.date || ""}\n${tr("mail.subject")}: ${m?.subject || ""}\n\n${m?.text || ""}`,
        })}>{tr("mail.forward")}</Rowbutton>
        {/* Archive and spam appear only when the account names a target for them — a button
            that explains on being pressed that it cannot is none. */}
        {(account?.archive_mode === "pattern" ? account?.archive_pattern : account?.folder_archive) && (
          <Rowbutton onClick={() => archive.mutate()}>{tr("mail.archive_button")}</Rowbutton>
        )}
        {/* In the spam folder "mark as spam" is not an action but a
            repetition. What is missing there is the contradiction. */}
        {account?.folder_junk && (folder === account.folder_junk ? (
          <Rowbutton onClick={() => noSpam.mutate()} title={tr("mail.back_inbox_detection_learns")}>
            ✅ {tr("mail.not_spam")}
          </Rowbutton>
        ) : (
          <Rowbutton onClick={() => asSpam.mutate()}>{tr("mail.spam_button")}</Rowbutton>
        ))}
        <Rowbutton onClick={() => setMoveOpen(true)}>{tr("mail.move_button")}</Rowbutton>
        <div className="flex-1" />
        <Rowbutton danger onClick={() => remove.mutate()}>{tr("mail.delete_3")}</Rowbutton>
      </>}
    >
      {m && (
        <>
          {/* Two rows instead of four: who wrote and when is the question on opening — to whom
              and in copy one looks up only when replying. The full
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
                 title={[`${tr("mail.to_label")}: ${m.to.map((a) => a.addr).join(", ") || "—"}`,
                         m.cc.length ? `${tr("mail.cc_label")}: ${m.cc.map((a) => a.addr).join(", ")}` : ""]
                        .filter(Boolean).join("\n")}>
              {tr("mail.to_label")} {m.to.map((a) => a.addr).join(", ") || "—"}
              {m.cc.length > 0 && <> · {tr("mail.cc_label")} {m.cc.map((a) => a.addr).join(", ")}</>}
            </div>
          </div>

          {m.attachments.length > 0 && (
            <div className="space-y-2">
            {/* The same action over all files at once. Only from the second attachment on:
                with one file the row below it says the same thing, and two buttons for one
                click are one too many. */}
            {forAttachment.length > 0 && m.attachments.length > 1 && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted">
                  {tr("mail.all_attachments", { n: m.attachments.length })}
                </span>
                {forAttachment.map((act) => (
                  <Rowbutton key={act.definition_id} title={act.description}
                    onClick={() => start.mutate({ definition_id: act.definition_id, all: true })}>
                    {act.name}
                  </Rowbutton>
                ))}
              </div>
            )}
            <Listing>
              {m.attachments.map((a) => (
                <ListRow key={a.index}>
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
                </ListRow>
              ))}
            </Listing>
            </div>
          )}

          {m.html ? (
            <div className="space-y-2">
              <Tab active={view} onChoose={setView} selection={[
                ["html", tr("mail.formatted")], ["text", tr("mail.text_only")],
              ]} />
              {view === "html"
                ? <HtmlView html={m.html} remoteimages={m.remote_images} />
                : <pre className={`max-h-[60vh] overflow-auto whitespace-pre-wrap ${PAPER}`}>
                    {m.text || tr("mail.no_text")}
                  </pre>}
            </div>
          ) : (
            <pre className={`max-h-[60vh] overflow-auto whitespace-pre-wrap ${PAPER}`}>
              {m.text || tr("mail.no_text")}
            </pre>
          )}

          {forMail.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted">{tr("mail.actions_label")}</span>
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
        <FolderChoice accountId={accountId} without={folder} onClose={() => setMoveOpen(false)}
          onChoose={(target) => { setMoveOpen(false); move.mutate(target); }} />
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
    onError: (e) => onError(e instanceof ApiError ? e.message : tr("mail.send_failed")),
  });
  const draft = useMutation({
    mutationFn: () => api.post(`/mailbox/accounts/${accountId}/draft`, base()),
    onSuccess: onClose,
    onError: (e) => onError(e instanceof ApiError ? e.message : tr("mail.draft_failed")),
  });

  return (
    // Held in place: whoever is writing a mail otherwise loses half the text on a misplaced
    // click. It is closed through ✕, cancel, draft or send.
    <Dialog wide hold title={tr("mail.compose")} onClose={onClose}
      foot={
        <div className="flex items-center gap-2">
          <Rowbutton onClick={() => draft.mutate()}>{tr("mail.save_as_draft")}</Rowbutton>
          <div className="flex-1" />
          <DialogFoot onCancel={onClose} runs={send.isPending}
            disabled={!identity || !f.to.trim()} saveText={tr("mail.send")}
            onSave={() => send.mutate()} />
        </div>
      }>
      <div className="space-y-3">
        {!identities?.length && (
          <Errorrow text={tr("mail.account_without_identity")} />
        )}
        <Field label={tr("mail.from_label")}>
          <select value={identity ?? ""} className={INPUT_VALUE}
            onChange={(e) => setIdentity(Number(e.target.value))}>
            {identities?.map((i) => (
              <option key={i.id} value={i.id}>
                {i.display_name ? `${i.display_name} <${i.email}>` : i.email}
              </option>
            ))}
          </select>
        </Field>
        <Field label={tr("mail.to_label")} hint={tr("mail.several_addresses_comma")}>
          <input value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("mail.copy")}>
          <input value={f.cc} onChange={(e) => setF({ ...f, cc: e.target.value })} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("mail.subject")}>
          <input value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("mail.text_label")}>
          <textarea value={f.text} rows={14} className={`${INPUT_VALUE} font-mono text-xs`}
            onChange={(e) => setF({ ...f, text: e.target.value })} />
        </Field>

        <Field label={tr("mail.attachments")}>
          <div className="space-y-2">
            {attachments.length > 0 && (
              <Listing>
                {attachments.map((a, i) => (
                  <ListRow key={`${a.filename}-${i}`}>
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate">📎 {a.filename}</span>
                      <Tag>{Math.max(1, Math.round(a.size / 1024))} kB</Tag>
                      <Rowbutton danger
                        onClick={() => setAttachments(attachments.filter((_, j) => j !== i))}>
                        {tr("mail.remove")}
                      </Rowbutton>
                    </div>
                  </ListRow>
                ))}
              </Listing>
            )}
            <label className="inline-block cursor-pointer rounded border border-line px-2 py-1 text-xs text-muted hover:border-brand hover:text-ink">
              {tr("mail.attach_file")}
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
