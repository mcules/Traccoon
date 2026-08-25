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
  IconButton, Button, BUTTON, Listing, ListingEmpty, ListRow, Tab, Rowbutton, BUTTON_TEXT} from "../components/ui";

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

export default function Mail() {
  usePageChrome("Mail", []);
  const qc = useQueryClient();
  const { user, refresh: userAgain } = useAuth();
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

  const accountSwitch = (id: number) => {
    setAccountId(id);
    setFolder("INBOX");
    setUid(null);
    // The server notes it, and the browser has to hear about it: leaving the page throws the
    // choice away (the component goes with it), and on coming back the person in the context
    // is the one from the last login. Without the second look one landed in the mailbox one
    // had chosen the day before, not in the one one had just left.
    api.post(`/mailbox/accounts/${id}/last`, {})
      .then(() => userAgain())
      .catch(() => {/* Remembering is no must */});
  };

  const { data: folderListing } = useQuery({
    queryKey: ["mail-folders", accountId], enabled: !!accountId,
    queryFn: () => api.get<Folder[]>(`/mailbox/accounts/${accountId}/folders?counts=true`),
    refetchInterval: 60_000, refetchOnWindowFocus: true,
  });

  if (!accounts?.length) {
    return (
      <Area hint={tr("mail.no_mailbox_yet")}>
        <p className="text-sm text-muted">
          <span dangerouslySetInnerHTML={{ __html: tr("mail.accounts_in_account") }} />
        </p>
      </Area>
    );
  }

  return (
    <div className="space-y-3">
      <Errorrow text={err} />
      {/* One row for everything that belongs to the mailbox: which one, its settings, and
          the only action that does not start from a message. */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">{tr("mail.mailbox")}</span>
        <select value={accountId ?? ""} onChange={(e) => accountSwitch(Number(e.target.value))}
          className={`${INPUT_VALUE} max-w-[16rem]`}>
          {accounts.map((k) => {
            const open = unread?.accounts.find((a) => a.account_id === k.id)?.unseen;
            return (
              <option key={k.id} value={k.id}>
                {k.name}{k.enabled ? "" : ` (${tr("mail.off_short")})`}{open ? ` — ${open} ${tr("mail.new_short")}` : ""}
              </option>
            );
          })}
        </select>
        <IconButton icon="⟳" title={tr("mail.look_now")}
          onClick={() => {
            qc.invalidateQueries({ queryKey: ["mail-unread"] });
            qc.invalidateQueries({ queryKey: ["mail-folders"] });
            qc.invalidateQueries({ queryKey: ["mail-list"] });
          }} />
        <IconButton icon="⚙" title={tr("mail.settings_of_mailbox")}
          onClick={() => setSettings(accounts.find((k) => k.id === accountId) || null)} />
        {/* The other mailboxes with new mail — visible without opening the select, and one
            click jumps there. Whoever has nothing waiting does not turn up here: a
            row of zeroes would be no information but wallpaper. */}
        {accounts.filter((k) => {
          const open = unread?.accounts.find((a) => a.account_id === k.id)?.unseen;
          return k.id !== accountId && !!open;
        }).map((k) => (
          <button key={k.id} onClick={() => accountSwitch(k.id)}
            title={tr("mail.switch_to", { name: k.name })}
            className="flex shrink-0 items-center gap-1.5 rounded border border-brand/40 bg-brand/15 px-2 py-1 text-xs text-brand transition-colors hover:bg-brand/25">
            {k.name}
            <span className="rounded-full bg-brand px-1.5 text-[11px] text-white tabular-nums">
              {unread?.accounts.find((a) => a.account_id === k.id)?.unseen}
            </span>
          </button>
        ))}
        {/* The search belongs to the mailbox, not to the list below it: it applies to the whole
            folder and stays visible even when a message is open on the right. */}
        <form onSubmit={(e) => { e.preventDefault(); setSearch(question); setUid(null); }}
              className="flex min-w-0 flex-1 items-center gap-2">
          <input value={question} onChange={(e) => setQuestion(e.target.value)}
            placeholder={tr("mail.search_fulltext")} className={`${INPUT_VALUE} min-w-0 max-w-md flex-1`} />
          {search && (
            <Rowbutton onClick={() => { setQuestion(""); setSearch(""); }}>
              {tr("mail.reset")}
            </Rowbutton>
          )}
        </form>
        <button onClick={() => setCompose({})}
          className={BUTTON.primary}>
          {tr("mail.compose_button")}
        </button>
      </div>

      {/* Side by side from `sm` on: the folder column needs no 300 px, and stacked it pushes
          the message list below the edge of the screen — exactly what one does not open a
          mail program for. */}
      {/* Two states, one arrangement: with no mail open the list sits on the right and may be
          wide. With a mail open the list moves under the folders — one keeps the overview,
          jumps to the next mail and reads on beside it, instead of switching back and forth
          between two views. */}
      <div className="flex flex-col gap-3 sm:flex-row">
        <div className={`sm:shrink-0 ${uid === null ? "sm:w-48 lg:w-56" : "sm:w-72 lg:w-80"}`}>
          <div className="space-y-3">
            <Area>
              <FolderTree folder={folderListing} active={folder}
                onChoose={(n) => { setFolder(n); setUid(null); setSearch(""); setQuestion(""); }} />
              {/* Handles for the CHOSEN folder. They stand below the tree and not in every
                  row: they are needed rarely, and beside every
                  folder one delete button is one too many. */}
              {accountId && (
                <FolderHandgrips accountId={accountId} folder={folder}
                  account={accounts.find((k) => k.id === accountId)}
                  onGone={() => { setFolder("INBOX"); setUid(null); }}
                  onEmptied={() => setUid(null)}
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
    onError: (e) => onError(e instanceof ApiError ? e.message : tr("common.save_failed")),
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
  if (!folder) return <Listing><ListingEmpty>{tr("mail.folders_loading")}</ListingEmpty></Listing>;

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
        <ListRow key={o.name} dense onClick={() => onChoose(o.name)}>
          {/* Fixed columns instead of flex with placeholders: only that way does the folder
              icon of every
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
                  title={onlyChildren ? tr("mail.in_subfolders") : tr("mail.unread")}>
                  {number}
                </Tag>
              );
            })()}
          </div>
        </ListRow>
      ))}
    </Listing>
  );
}

/**
 * What one can do with a whole folder: mark everything read, empty it, and the folder
 * management behind it.
 *
 * Emptying and deleting are two different things, and the difference is the folder itself:
 * "empty" takes the mail out and leaves the folder standing, "delete" takes both. The daily
 * case is the first one — which is why it stands here, while creating, renaming and deleting
 * sit one click away in the management: rare, and none of them a thing one wants to have
 * within reach of a misgrab.
 */
function FolderHandgrips({ accountId, folder: folder, account, onGone, onEmptied,
                            onError: onError }: {
  accountId: number; folder: string; account: MailAccount | undefined;
  onGone: () => void; onEmptied: () => void; onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const [question, setQuestion] = useState<"gelesen" | "leeren" | null>(null);
  const [manage, setManage] = useState(false);
  const [notice, setNotice] = useState("");
  const gonewrong = (was: string) => (e: unknown) =>
    onError(e instanceof ApiError ? e.message : tr("mail.failed_suffix", { what: was }));
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["mail-folders"] });
    qc.invalidateQueries({ queryKey: ["mail-list"] });
    qc.invalidateQueries({ queryKey: ["mail-unread"] });
  };

  const read = useMutation({
    mutationFn: () => api.post<{ marked: number }>(
      `/mailbox/accounts/${accountId}/folders/read-all`, { folder: folder }),
    onSuccess: (r) => {
      setQuestion(null);
      setNotice(r.marked ? tr("mail.marked_read_n", { n: r.marked }) : tr("mail.nothing_unread"));
      refresh();
    },
    onError: (e) => { setQuestion(null); gonewrong(tr("mail.marking"))(e); },
  });
  const empty = useMutation({
    mutationFn: () => api.post<{ deleted: number; target: string }>(
      `/mailbox/accounts/${accountId}/folders/empty`, { folder: folder }),
    onSuccess: (r) => {
      setQuestion(null);
      setNotice(!r.deleted ? tr("mail.folder_was_empty")
        : r.target ? tr("mail.emptied_into", { n: r.deleted, folder: r.target })
                   : tr("mail.emptied_finally", { n: r.deleted }));
      refresh();
      onEmptied();
    },
    onError: (e) => { setQuestion(null); gonewrong(tr("mail.emptying"))(e); },
  });

  // Whether emptying moves or really deletes is the same question as with a single message,
  // and the answer belongs in the safety question, afterwards it is too late for it.
  const finally_ = !account?.folder_trash || folder === account.folder_trash;

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Rowbutton onClick={() => setQuestion("gelesen")}>{tr("mail.mark_all_read")}</Rowbutton>
        <Rowbutton danger onClick={() => setQuestion("leeren")}>{tr("mail.empty_folder")}</Rowbutton>
        <Rowbutton onClick={() => setManage(true)}>{tr("mail.manage_folders")}</Rowbutton>
      </div>
      {notice && <div className="text-xs text-green-400">{notice}</div>}

      {question === "gelesen" && (
        <ConfirmDialog
          title={tr("mail.mark_all_read_q")}
          text={tr("mail.everything_unread_in", { folder })}
          hint={tr("mail.undo_message_by_message")}
          danger={false} confirmText={tr("mail.mark")} runs={read.isPending}
          onClose={() => setQuestion(null)} onConfirm={() => read.mutate()} />
      )}
      {question === "leeren" && (
        <ConfirmDialog
          title={tr("mail.empty_folder_q", { folder })}
          text={finally_ ? tr("mail.empty_finally_text", { folder })
                          : tr("mail.empty_into_trash_text",
                               { folder, trash: account!.folder_trash })}
          hint={tr("mail.folder_itself_stays")}
          confirmText={finally_ ? tr("mail.delete_finally") : tr("mail.empty")}
          runs={empty.isPending}
          onClose={() => setQuestion(null)} onConfirm={() => empty.mutate()} />
      )}
      {manage && (
        <FolderManagement accountId={accountId} account={account} chosen={folder}
          onClose={() => setManage(false)} onGone={onGone} onError={onError} />
      )}
    </>
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
          {tr("mail.remote_images_blocked")}
          <Rowbutton onClick={() => setImages(true)}>{tr("mail.load_images")}</Rowbutton>
        </div>
      )}
      <iframe
        title={tr("mail.message")}
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
    if (error) onError(error instanceof ApiError ? error.message : tr("mail.mailbox_unreachable"));
  }, [error]);

  return (
    <Area
      title={folder}
      tools={<>
        {search && <Tag color="brand">{tr("mail.search_label")}: {search}</Tag>}
        <div className="flex-1" />
        <span className="text-xs text-muted">
          {data?.total ?? 0} {search ? tr("mail.hits") : tr("mail.messages")}
        </span>
      </>}
    >
      {/* Narrow means: the list stands beside the open mail and scrolls on its own. Without
          its own height would make the page as long as the mailbox. */}
      <div className={narrow ? "max-h-[55vh] overflow-y-auto" : ""}>
      <Listing>
        {data?.messages.map((m) => (
          <ListRow key={m.uid} dense={narrow} onClick={() => onOpen_it(m.uid)}>
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
          </ListRow>
        ))}
        {isLoading && <ListingEmpty>{tr("mail.loading")}</ListingEmpty>}
        {!isLoading && !data?.messages.length && <ListingEmpty>{tr("mail.nothing_in_folder")}</ListingEmpty>}
      </Listing>
      </div>
      {(data?.total ?? 0) > limit && (
        <div className="flex items-center gap-2">
          <Rowbutton onClick={() => setPage(Math.max(0, page - 1))}>{tr("mail.newer")}</Rowbutton>
          <span className="text-xs text-muted">
            {page * limit + 1}–{Math.min((page + 1) * limit, data!.total)} {tr("mail.of")} {data!.total}
          </span>
          <Rowbutton onClick={() => setPage(page + 1)}>{tr("mail.older")}</Rowbutton>
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
      title={m?.subject || "…"}
      tools={<>
        <Rowbutton onClick={onBack}>{tr("mail.back_to_list")}</Rowbutton>
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
                : <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded border border-line bg-surface p-3 text-sm text-ink">
                    {m.text || tr("mail.no_text")}
                  </pre>}
            </div>
          ) : (
            <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded border border-line bg-surface p-3 text-sm text-ink">
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
        <Dialog title={tr("mail.move_to")} onClose={() => setMoveOpen(false)}>
          {/* The tree as in the folder column, only without counters: here one chooses, one
              does not browse. A click moves and closes — a second "apply" button
              would be a step nobody needs. */}
          <Listing>
            {(allFolder || []).filter((o) => o.name !== folder).map((o) => (
              <ListRow key={o.name} dense
                onClick={() => { setMoveOpen(false); move.mutate(o.name); }}>
                <div className="flex items-center gap-2"
                     style={{ paddingLeft: `${o.level * 0.85}rem` }}>
                  <span>{SPECIAL[o.special] || "📁"}</span>
                  <span className="min-w-0 flex-1 truncate">{o.display}</span>
                </div>
              </ListRow>
            ))}
            {!allFolder?.length && <ListingEmpty>{tr("mail.no_further_folders")}</ListingEmpty>}
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
