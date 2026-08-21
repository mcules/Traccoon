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
  folder_archive: "Archive", archive_mode: "folder", archive_pattern: "Archive/{year}",
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
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

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
    <Area hint={tr("mail_accounts.area_hint")}>
      <Errorrow text={err} />

      <Listing>
        {accounts?.map((k) => (
          <ListenLine key={k.id} dimmed={!k.enabled}>
            <div className="flex flex-wrap items-center gap-2">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-ink">{k.name}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span className="truncate font-mono">{k.imap_user || k.imap_host}</span>
                  {!k.smtp_host && <Tag color="yellow">{tr("mail_accounts.read_only")}</Tag>}
                  {!k.imap_password_set && <Tag color="red">{tr("mail_accounts.no_password")}</Tag>}
                </div>
              </div>
              {k.enabled
                ? <State color="green" text={tr("mail_accounts.active_state")} />
                : <State color="grey" text={tr("mail_accounts.off_state")} />}
              <Actions>
                <IconButton icon={ICON.edit} title={tr("common.edit")}
                  onClick={() => { setErr(""); setDialog({ ...EMPTY, ...k, imap_password: "", smtp_password: "" }); }} />
                <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                  onClick={() => setDeleteAccount(k)} />
              </Actions>
            </div>
          </ListenLine>
        ))}
        {accounts?.length === 0 && <ListingEmpty>{tr("mail_accounts.no_mailbox_yet")}</ListingEmpty>}
      </Listing>

      <button onClick={() => { setErr(""); setDialog({ ...EMPTY }); }}
        className={BUTTON.primary}>
        {ICON.fresh} {tr("mail_accounts.add_mailbox")}
      </button>

      <McpAccess onError={fail} />

      {dialog && (
        <AccountDialog start={dialog} runs={save.isPending} error={err}
          onClose={() => setDialog(null)} onSave={(f) => save.mutate(f)} />
      )}
      {deleteAccount && (
        <DeleteDialog was={deleteAccount.name} runs={remove.isPending}
          hint={tr("mail_accounts.mailbox_untouched")}
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
    onError: (e) => setErr(e instanceof ApiError ? e.message : tr("mail_accounts.check_failed")),
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
    <Dialog wide title={f.id ? `Postfach ${f.name}` : tr("mail_accounts.add_mailbox")} onClose={onClose}
      foot={<DialogFoot onCancel={onClose} runs={running}
        disabled={!f.name.trim() || !f.imap_host.trim()}
        onSave={() => onSave(f)} />}>
      <Errorrow text={error || err} />
      <div className="space-y-4">
        {/* Name and switch stand above the menu: they belong to none of the four parts but
            to the mailbox as a whole. */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-48 flex-1">
            <Field label={tr("mail_accounts.name")} hint={tr("mail_accounts.short_name_hint")}>
              <input value={f.name} onChange={(e) => set({ name: e.target.value })}
                placeholder="private" className={INPUT_VALUE} />
            </Field>
          </div>
          <label className="flex items-center gap-2 pb-1.5 text-sm text-muted">
            <input type="checkbox" checked={f.enabled}
              onChange={(e) => set({ enabled: e.target.checked })} />
            {tr("mail_accounts.active")}
          </label>
        </div>

        <div className="flex flex-col gap-4 sm:flex-row">
          <Tab vertical active={part} onChoose={setPart} selection={[
            ["empfang", tr("mail_accounts.receive_group")],
            ["senden", tr("mail_accounts.send_group")],
            ["ordner", tr("mail_accounts.folders_group")],
            ["identitaeten", tr("mail_accounts.identities")],
            ["agenten", tr("mail_accounts.agents_group")],
          ]} />

          <div className="min-w-0 flex-1 space-y-4">
        {part === "empfang" && (<>
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label={tr("mail_accounts.server")}><input value={f.imap_host} className={INPUT_VALUE}
            onChange={(e) => set({ imap_host: e.target.value })} placeholder="imap.example.org" /></Field>
          <Field label={tr("mail_accounts.port")}><input type="number" value={f.imap_port} className={INPUT_VALUE}
            onChange={(e) => set({ imap_port: Number(e.target.value) })} /></Field>
          <Field label={tr("mail_accounts.user")}><input value={f.imap_user} className={INPUT_VALUE}
            onChange={(e) => set({ imap_user: e.target.value })} /></Field>
          <Field label={tr("mail_accounts.password")} hint={start.id ? tr("mail_accounts.empty_means_unchanged") : ""}>
            <input type="password" value={f.imap_password} className={INPUT_VALUE}
              onChange={(e) => set({ imap_password: e.target.value })} /></Field>
          <label className="flex items-center gap-2 text-sm text-muted">
            <input type="checkbox" checked={f.imap_ssl}
              onChange={(e) => set({ imap_ssl: e.target.checked })} />
            {tr("mail_accounts.encrypted_ssl")}
          </label>
        </div>
        </>)}

        {part === "senden" && (<>
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label={tr("mail_accounts.server")}><input value={f.smtp_host} className={INPUT_VALUE}
            onChange={(e) => set({ smtp_host: e.target.value })} placeholder="smtp.example.org" /></Field>
          <Field label={tr("mail_accounts.port")}><input type="number" value={f.smtp_port} className={INPUT_VALUE}
            onChange={(e) => set({ smtp_port: Number(e.target.value) })} /></Field>
          <Field label={tr("mail_accounts.user")}><input value={f.smtp_user} className={INPUT_VALUE}
            onChange={(e) => set({ smtp_user: e.target.value })} /></Field>
          <Field label={tr("mail_accounts.password")} hint={start.id ? tr("mail_accounts.empty_means_unchanged") : ""}>
            <input type="password" value={f.smtp_password} className={INPUT_VALUE}
              onChange={(e) => set({ smtp_password: e.target.value })} /></Field>
          <Field label={tr("mail_accounts.encryption")}
            hint={tr("mail_accounts.port_hint")}>
            <select value={f.smtp_security} className={INPUT_VALUE}
              onChange={(e) => {
                const art = e.target.value;
                // Pull the port along as long as it is the usual one of the other variant:
                // whoever switches the encryption almost always means the matching port too —
                // and a port entered on purpose (2525 …) stays untouched.
                const port = art === "ssl" && f.smtp_port === 587 ? 465
                  : art === "starttls" && f.smtp_port === 465 ? 587
                    : f.smtp_port;
                set({ smtp_security: art, smtp_port: port });
              }}>
              <option value="starttls">STARTTLS (587)</option>
              <option value="ssl">SSL/TLS (465)</option>
              <option value="none">{tr("mail_accounts.none_in_house")}</option>
            </select>
          </Field>
        </div>

        </>)}

        {part === "ordner" && (<>
        {!start.id && (
          <p className="text-xs text-muted">
            {tr("mail_accounts.folders_after_saving")}
          </p>
        )}
        <div className="grid gap-2 sm:grid-cols-2">
          <FolderField label={tr("mail_accounts.sent")} value={f.folder_sent} folder={folder}
            onChoose={(v) => set({ folder_sent: v })} />
          <FolderField label={tr("mail_accounts.drafts")} value={f.folder_drafts} folder={folder}
            onChoose={(v) => set({ folder_drafts: v })} />
          <FolderField label={tr("mail_accounts.trash")} value={f.folder_trash} folder={folder}
            onChoose={(v) => set({ folder_trash: v })} />
          <FolderField label={tr("mail_accounts.spam")} value={f.folder_junk} folder={folder}
            hint={tr("mail_accounts.junk_hint")}
            onChoose={(v) => set({ folder_junk: v })} />
        </div>

        <div className="text-xs font-medium uppercase tracking-wider text-muted/70">{tr("mail_accounts.archive_group")}</div>
        <Field label={tr("mail_accounts.split")}
          hint={tr("mail_accounts.archive_hint")}>
          <select value={f.archive_mode} className={INPUT_VALUE}
            onChange={(e) => set({ archive_mode: e.target.value })}>
            <option value="folder">{tr("mail_accounts.one_folder_for_all")}</option>
            <option value="pattern">{tr("mail_accounts.split_by_pattern")}</option>
          </select>
        </Field>
        {f.archive_mode === "folder" ? (
          <FolderField label={tr("mail_accounts.archive_folder")} value={f.folder_archive} folder={folder}
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
              onError={(e) => setErr(e instanceof ApiError ? e.message : tr("common.error"))} />
          ) : (
            <p className="text-sm text-muted">
              {tr("mail_accounts.identities_after_saving")}
            </p>
          )
        )}
          </div>
        </div>

        {start.id && (
          <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
            <Rowbutton onClick={() => { setErr(""); testing.mutate(); }}>
              {testing.isPending ? tr("mail_accounts.checking") : tr("mail_accounts.check_imap_smtp")}
            </Rowbutton>
            {/* The test uses what is saved — not what stands in the form right now.
                Anything else would mean sending half-finished credentials to the server. */}
            <span className="text-xs text-muted">
              {check || tr("mail_accounts.checks_stored_state")}
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
 * second time only whoever stores it could, and then it would be no secret any more but a
 * copy.
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
      <div className="text-sm font-medium text-ink">{tr("mail_accounts.agent_access_mcp")}</div>
      <p className="text-xs text-muted">
        {tr("mail_accounts.mcp_address_hint_1")} <code>Authorization: Bearer …</code>.{" "}
        <span dangerouslySetInnerHTML={{ __html: tr("mail_accounts.mcp_address_hint_2") }} />
      </p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded bg-surface px-1.5 py-0.5 text-xs">
          {address}
        </code>
        <IconButton icon={ICON.copy} title={tr("mail_accounts.copy_address")}
          onClick={() => navigator.clipboard?.writeText(address)} />
      </div>
      {fresh ? (
        <div className="space-y-1 rounded border border-amber-500/30 bg-amber-500/10 p-2">
          <div className="text-xs text-amber-300">
            {tr("mail_accounts.visible_once")}
          </div>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate text-xs text-ink">{fresh}</code>
            <IconButton icon={ICON.copy} title={tr("mail_accounts.copy_token")}
              onClick={() => navigator.clipboard?.writeText(fresh)} />
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Rowbutton onClick={() => create.mutate()}>
            {state?.token_set ? tr("mail_accounts.new_token") : tr("mail_accounts.create_token")}
          </Rowbutton>
          {state?.token_set && (
            <>
              <Tag color="green">{tr("mail_accounts.token_set")} · {state.fingerprint}</Tag>
              <Rowbutton danger onClick={() => remove.mutate()}>{tr("mail_accounts.block_access")}</Rowbutton>
            </>
          )}
          {state?.token_set && (
            <span className="text-xs text-muted">
              {tr("mail_accounts.new_token_invalidates")}
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
 * there as three separate groups and not as the rungs of a ladder.
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
    lesen: tr("mail_accounts.group_read"), change: tr("mail_accounts.group_change"),
    send: tr("mail_accounts.send"),
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
        {tr("mail_accounts.release_for_agents")}
      </label>
      <p className="text-xs text-muted">
        {tr("mail_accounts.release_hint")}
      </p>

      {f.mcp_enabled && (<>
        <div className="text-xs font-medium uppercase tracking-wider text-muted/70">
          {tr("mail_accounts.instructions_group")}
        </div>
        <Field label={tr("mail_accounts.what_agent_must_know")}
          hint={tr("mail_accounts.read_on_connect")}>
          <textarea value={f.mcp_instructions} rows={5} className={`${INPUT_VALUE} text-xs`}
            placeholder={tr("mail_accounts.instructions_example")
              + tr("mail_accounts.house_rules_example")}
            onChange={(e) => set({ mcp_instructions: e.target.value })} />
        </Field>

        <div className="text-xs font-medium uppercase tracking-wider text-muted/70">
          {tr("mail_accounts.tools_group")}
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
          {tr("mail_accounts.hide_folders_group")}
        </div>
        <p className="text-xs text-muted">
          {tr("mail_accounts.hide_folders_hint_1")} <code>*</code>{" "}
          {tr("mail_accounts.hide_folders_hint_2")} (<code>Private*</code>).
        </p>
        <Listing>
          {f.mcp_ignore_folders.map((m) => (
            <ListenLine key={m} dense>
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate">{m}</code>
                <Rowbutton danger onClick={() => patternPath(m)}>{tr("mail_accounts.remove")}</Rowbutton>
              </div>
            </ListenLine>
          ))}
          {!f.mcp_ignore_folders.length && <ListingEmpty>Nichts ausgeblendet.</ListingEmpty>}
        </Listing>
        <div className="flex flex-wrap items-center gap-2">
          <select value="" className={`${INPUT_VALUE} max-w-xs`}
            onChange={(e) => e.target.value && patternHint(e.target.value)}>
            <option value="">{tr("mail_accounts.choose_folder")}</option>
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
          <Rowbutton onClick={() => patternHint(newPattern)}>{tr("mail_accounts.add")}</Rowbutton>
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
      <Field label={tr("mail_accounts.pattern")} hint={tr("mail_accounts.slash_separates")}>
        <input value={value} className={`${INPUT_VALUE} font-mono`}
          onChange={(e) => onUpdate(e.target.value)} placeholder="Archive/{year}" />
      </Field>
      {preview && (
        <p className="text-xs text-muted">
          {tr("mail_accounts.pattern_preview_1")} <code className="text-brand">{preview}</code>.{" "}
          {tr("mail_accounts.pattern_preview_2")}
        </p>
      )}
      <p className="text-[11px] text-muted">
        {tr("mail_accounts.placeholders")} <code>{"{year}"}</code> <code>{"{year_short}"}</code>{" "}
        <code>{"{month}"}</code> <code>{"{month_name}"}</code> <code>{"{day}"}</code>{" "}
        <code>{"{quarter}"}</code> <code>{"{week}"}</code> <code>{"{sender}"}</code>{" "}
        <code>{"{sender_domain}"}</code>
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
 * password), the text field stays — better than an empty select.
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

/** The identities of an account: who appears as the sender. */
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
              {i.is_default && <Tag color="brand">{tr("mail_accounts.default")}</Tag>}
              <Actions>
                <IconButton icon={ICON.edit} title={tr("common.edit")}
                  onClick={() => setDialog(i)} />
                <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                  onClick={() => remove.mutate(i.id)} />
              </Actions>
            </div>
          </ListenLine>
        ))}
        {data?.length === 0 && <ListingEmpty>{tr("mail_accounts.no_identity_yet")}</ListingEmpty>}
      </Listing>
      <button onClick={() => setDialog({ email: "", display_name: "", is_default: !data?.length })}
        className="mt-2 rounded border border-line px-2 py-1 text-xs text-muted hover:border-brand hover:text-ink">
        {tr("mail_accounts.add_identity")}
      </button>

      {dialog && (
        <Dialog title={dialog.id ? tr("mail_accounts.identity") : tr("mail_accounts.create_identity")} onClose={() => setDialog(null)}
          foot={<DialogFoot onCancel={() => setDialog(null)} runs={save.isPending}
            disabled={!dialog.email?.trim()} onSave={() => save.mutate(dialog)} />}>
          <div className="space-y-3">
            <Field label={tr("mail_accounts.sender_address")}><input value={dialog.email || ""} className={INPUT_VALUE}
              onChange={(e) => setDialog({ ...dialog, email: e.target.value })} /></Field>
            <Field label={tr("mail_accounts.display_name")}><input value={dialog.display_name || ""} className={INPUT_VALUE}
              onChange={(e) => setDialog({ ...dialog, display_name: e.target.value })} /></Field>
            <Field label={tr("mail_accounts.reply_to")} hint={tr("mail_accounts.reply_to_hint")}>
              <input value={dialog.reply_to || ""} className={INPUT_VALUE}
                onChange={(e) => setDialog({ ...dialog, reply_to: e.target.value })} /></Field>
            <Field label={tr("mail_accounts.signature")}>
              <textarea value={dialog.signature || ""} rows={4} className={`${INPUT_VALUE} font-mono text-xs`}
                onChange={(e) => setDialog({ ...dialog, signature: e.target.value })} /></Field>
            <label className="flex items-center gap-2 text-sm text-muted">
              <input type="checkbox" checked={!!dialog.is_default}
                onChange={(e) => setDialog({ ...dialog, is_default: e.target.checked })} />
              {tr("mail_accounts.default_for_account")}
            </label>
          </div>
        </Dialog>
      )}
    </div>
  );
}
