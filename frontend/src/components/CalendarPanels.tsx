import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { tr } from "../i18n";
import { api, ApiError } from "../api";
import {
  Actions, Area, BUTTON, BUTTON_SMALL, Dialog, DialogFoot, DeleteDialog, Errorrow, Field,
  ICON, IconButton, INPUT_VALUE, Listing, ListingEmpty, ListRow,
} from "./ui";

/**
 * The calendars, arranged the way somebody sets them up: a login to a server,
 * and under it the calendars on that server.
 *
 * They used to hang under the notes, because that is where the appointments end
 * up — in the daily note. But that is one of three uses; the calendar view
 * reads them and appointments are written back through them. And there used to
 * be exactly one login, on the person, which is one server and one account.
 * Neither survives contact with reality: appointments live on several servers,
 * and one server holds several accounts when somebody has a private and a work
 * login on the same machine.
 */

export type Server = {
  id: number;
  label: string;
  url: string;
  username: string;
  has_password: boolean;
  enabled: boolean;
  position: number;
};

export type Calendar = {
  id: number;
  name: string;
  url: string;
  link_target: string;
  auth_user: string;
  server_id: number | null;
  caldav_id: string;
  write_access: "none" | "manual" | "agent";
  server_read_only: boolean;
  on_a_login: boolean;
  writable: boolean;
  agent_may_write: boolean;
  has_password: boolean;
  enabled: boolean;
  position: number;
};

type Collection = {
  id: string;
  name: string;
  url: string;
  readOnly: boolean;
  taken: boolean;
};


export function CalendarsPanel() {
  const qc = useQueryClient();
  const [serverDialog, setServerDialog] = useState<Server | "new" | null>(null);
  const [calendarDialog, setCalendarDialog] = useState<
    { calendar: Calendar } | { server: Server | null } | null>(null);
  const [dropServer, setDropServer] = useState<Server | null>(null);
  const [dropCalendar, setDropCalendar] = useState<Calendar | null>(null);
  const [err, setErr] = useState("");
  const [probe, setProbe] = useState<Record<number, string>>({});

  const { data: servers } = useQuery({
    queryKey: ["calendar-servers"],
    queryFn: () => api.get<{ servers: Server[] }>("/notes-native/calendar/servers"),
  });
  const { data: calendars } = useQuery({
    queryKey: ["notes-calendars"],
    queryFn: () => api.get<{ calendars: Calendar[] }>("/notes-native/calendars"),
  });

  const inv = () => {
    qc.invalidateQueries({ queryKey: ["calendar-servers"] });
    qc.invalidateQueries({ queryKey: ["notes-calendars"] });
  };
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  const saveServer = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: Record<string, unknown> }) =>
      id ? api.patch(`/notes-native/calendar/servers/${id}`, body)
         : api.post("/notes-native/calendar/servers", body),
    onSuccess: () => { setServerDialog(null); setErr(""); inv(); }, onError: fail,
  });
  const removeServer = useMutation({
    mutationFn: (id: number) => api.del(`/notes-native/calendar/servers/${id}`),
    onSuccess: () => { setDropServer(null); inv(); }, onError: fail,
  });
  const saveCalendar = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: Record<string, unknown> }) =>
      id ? api.patch(`/notes-native/calendars/${id}`, body)
         : api.post("/notes-native/calendars", body),
    onSuccess: () => { setCalendarDialog(null); setErr(""); inv(); }, onError: fail,
  });
  const removeCalendar = useMutation({
    mutationFn: (id: number) => api.del(`/notes-native/calendars/${id}`),
    onSuccess: () => { setDropCalendar(null); inv(); }, onError: fail,
  });
  // Reading a feed once answers, before anything is saved, the only question
  // somebody actually has: does this address work.
  const test = useMutation({
    mutationFn: (c: Calendar) =>
      api.post<{ ok: boolean; count?: number; message?: string }>(
        "/notes-native/calendar/test-source",
        { url: c.url, auth_user: c.auth_user, name: c.name }),
    onSuccess: (r, c) => setProbe((p) => ({
      ...p,
      [c.id]: r.ok ? tr("calendars.found_appointments", { n: r.count ?? 0 })
                   : (r.message || tr("common.error")),
    })),
    onError: (e, c) => setProbe((p) => ({
      ...p, [c.id]: e instanceof ApiError ? e.message : tr("common.error"),
    })),
  });

  const all = calendars?.calendars ?? [];
  const loose = all.filter((c) => c.server_id === null);

  const row = (c: Calendar) => (
    <ListRow key={c.id} dimmed={!c.enabled}>
      <div className="flex items-center gap-2">
        <input type="checkbox" checked={c.enabled} title={tr("calendars.fetch_it")}
          onChange={(e) => saveCalendar.mutate({ id: c.id, body: { enabled: e.target.checked } })} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{c.name || tr("calendars.unnamed")}</span>
            {c.writable ? (
              <span className="rounded bg-green-500/15 px-1.5 py-0.5 text-xs text-green-300"
                title={tr("calendars.writable_hint")}>
                {tr("calendars.writable")}
                {c.agent_may_write && ` · ${tr("calendars.agent_writes")}`}
              </span>
            ) : (
              <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted"
                title={c.server_read_only ? tr("calendars.server_read_only")
                                          : tr("calendars.read_only_hint")}>
                {tr("calendars.read_only")}
              </span>
            )}
            {c.link_target && (
              <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted"
                title={tr("calendars.links_to")}>→ {c.link_target}</span>
            )}
            {probe[c.id] && <span className="text-xs text-muted">{probe[c.id]}</span>}
          </div>
          <div className="mt-0.5 truncate text-xs text-muted" title={c.url}>{c.url}</div>
        </div>
        <Actions>
          <IconButton icon={ICON.testing} title={tr("calendars.test")}
            onClick={() => test.mutate(c)} disabled={test.isPending} />
          <IconButton icon={ICON.edit} title={tr("common.edit")}
            onClick={() => { setErr(""); setCalendarDialog({ calendar: c }); }} />
          <IconButton icon={ICON.remove} title={tr("common.delete")} danger
            onClick={() => setDropCalendar(c)} />
        </Actions>
      </div>
    </ListRow>
  );

  return (
    <Area title={tr("calendars.title")} hint={tr("calendars.hint")}>
      <Errorrow text={err} />

      {servers?.servers.map((s) => {
        const mine = all.filter((c) => c.server_id === s.id);
        return (
          <div key={s.id} className="mb-4">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span className="font-medium">{s.label || s.url}</span>
              {s.username && <span className="text-xs text-muted">{s.username}</span>}
              {s.has_password && <span className="text-xs" title={tr("calendars.password_kept")}>🔒</span>}
              {!s.enabled && (
                <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">
                  {tr("servers.off")}
                </span>
              )}
              <span className="min-w-0 flex-1 truncate text-xs text-muted" title={s.url}>{s.url}</span>
              <Actions>
                <IconButton icon={ICON.edit} title={tr("common.edit")}
                  onClick={() => { setErr(""); setServerDialog(s); }} />
                <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                  onClick={() => setDropServer(s)} />
              </Actions>
            </div>
            <Listing>
              {mine.map(row)}
              {mine.length === 0 && <ListingEmpty>{tr("servers.no_calendars_yet")}</ListingEmpty>}
            </Listing>
            <button className={`${BUTTON_SMALL.secondary} mt-2`}
              onClick={() => { setErr(""); setCalendarDialog({ server: s }); }}>
              {ICON.fresh} {tr("calendars.add_here")}
            </button>
          </div>
        );
      })}

      {/* Subscriptions: a public address, read and nothing more. They belong to
          no login, so they stand apart rather than under one. */}
      {loose.length > 0 && (
        <div className="mb-4">
          <div className="mb-1 text-sm font-medium text-muted">{tr("calendars.without_login")}</div>
          <Listing>{loose.map(row)}</Listing>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <button className={BUTTON.primary} onClick={() => { setErr(""); setServerDialog("new"); }}>
          {ICON.fresh} {tr("servers.add")}
        </button>
        <button className={BUTTON.secondary}
          onClick={() => { setErr(""); setCalendarDialog({ server: null }); }}>
          {ICON.fresh} {tr("calendars.add_subscription")}
        </button>
      </div>

      {servers?.servers.length === 0 && loose.length === 0 && (
        <p className="mt-3 text-xs text-muted">{tr("servers.none_yet")}</p>
      )}

      {serverDialog && (
        <ServerDialog server={serverDialog === "new" ? null : serverDialog}
          runs={saveServer.isPending} error={err}
          onClose={() => setServerDialog(null)}
          onSave={(body) => saveServer.mutate({
            id: serverDialog === "new" ? null : serverDialog.id, body })} />
      )}
      {calendarDialog && (
        <CalendarDialog
          calendar={"calendar" in calendarDialog ? calendarDialog.calendar : null}
          server={"server" in calendarDialog ? calendarDialog.server
                  : (servers?.servers.find((s) => s.id === calendarDialog.calendar.server_id) ?? null)}
          servers={servers?.servers ?? []}
          runs={saveCalendar.isPending} error={err}
          onClose={() => setCalendarDialog(null)}
          onSave={(body) => saveCalendar.mutate({
            id: "calendar" in calendarDialog ? calendarDialog.calendar.id : null, body })} />
      )}
      {dropServer && (
        <DeleteDialog was={dropServer.label || dropServer.url}
          hint={tr("servers.delete_hint")} onClose={() => setDropServer(null)}
          onDelete={() => removeServer.mutate(dropServer.id)} runs={removeServer.isPending} />
      )}
      {dropCalendar && (
        <DeleteDialog was={dropCalendar.name || dropCalendar.url}
          hint={tr("calendars.delete_hint")} onClose={() => setDropCalendar(null)}
          onDelete={() => removeCalendar.mutate(dropCalendar.id)} runs={removeCalendar.isPending} />
      )}
    </Area>
  );
}


function ServerDialog({ server, onClose, onSave, runs, error }: {
  server: Server | null;
  onClose: () => void;
  onSave: (body: Record<string, unknown>) => void;
  runs: boolean;
  error: string;
}) {
  const [label, setLabel] = useState(server?.label ?? "");
  const [url, setUrl] = useState(server?.url ?? "");
  const [username, setUsername] = useState(server?.username ?? "");
  // Never prefilled: the password does not come back out of the server, and a
  // field that looks filled would be a field somebody trusts.
  const [password, setPassword] = useState("");

  const submit = () => {
    const body: Record<string, unknown> = {
      label: label.trim(), url: url.trim(), username: username.trim(),
    };
    // Empty means "leave what is stored alone" — otherwise renaming a login
    // would silently take its password away.
    if (password) body.password = password;
    onSave(body);
  };

  return (
    <Dialog title={server ? tr("servers.edit_title") : tr("servers.add")} onClose={onClose} hold
      foot={<DialogFoot onCancel={onClose} onSave={submit} runs={runs} disabled={!url.trim()} />}>
      <div className="space-y-3">
        <Errorrow text={error} />
        <Field label={tr("servers.label")} hint={tr("servers.label_hint")}>
          <input className={INPUT_VALUE} value={label} autoFocus
            onChange={(e) => setLabel(e.target.value)} />
        </Field>
        <Field label={tr("caldav.url")} hint={tr("caldav.url_hint")}>
          <input className={INPUT_VALUE} value={url} onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <div className="flex flex-wrap gap-3">
          <div className="min-w-[12rem] flex-1">
            <Field label={tr("calendars.user")}>
              <input className={INPUT_VALUE} value={username}
                onChange={(e) => setUsername(e.target.value)} />
            </Field>
          </div>
          <div className="min-w-[12rem] flex-1">
            <Field label={tr("calendars.password")}
              hint={server?.has_password ? tr("calendars.password_set_hint")
                                         : tr("calendars.password_hint")}>
              <input className={INPUT_VALUE} type="password" value={password}
                placeholder={server?.has_password ? "••••••••" : ""}
                onChange={(e) => setPassword(e.target.value)} />
            </Field>
          </div>
        </div>
      </div>
    </Dialog>
  );
}


function CalendarDialog({ calendar, server, servers, onClose, onSave, runs, error }: {
  calendar: Calendar | null;
  server: Server | null;
  servers: Server[];
  onClose: () => void;
  onSave: (body: Record<string, unknown>) => void;
  runs: boolean;
  error: string;
}) {
  const [serverId, setServerId] = useState<number | null>(
    calendar ? calendar.server_id : (server?.id ?? null));
  const [name, setName] = useState(calendar?.name ?? "");
  const [url, setUrl] = useState(calendar?.url ?? "");
  const [caldavId, setCaldavId] = useState(calendar?.caldav_id ?? "");
  const [writeAccess, setWriteAccess] =
    useState<"none" | "manual" | "agent">(calendar?.write_access ?? "none");
  const [linkTarget, setLinkTarget] = useState(calendar?.link_target ?? "");
  const [authUser, setAuthUser] = useState(calendar?.auth_user ?? "");
  const [password, setPassword] = useState("");

  // What the server itself says it has. Asked for only when a login is chosen —
  // a subscription has nobody to ask.
  const { data: found, isFetching } = useQuery({
    queryKey: ["calendar-collections", serverId],
    enabled: serverId !== null,
    queryFn: () => api.get<{ ok: boolean; reason?: string; message?: string;
                             collections: Collection[] }>(
      `/notes-native/calendar/servers/${serverId}/collections`),
  });

  const chosen = found?.ok ? found.collections.find((c) => c.id === caldavId.trim()) : undefined;
  const refuses = chosen ? chosen.readOnly : (calendar?.server_read_only ?? false);

  const pick = (c: Collection) => {
    setCaldavId(c.id);
    if (!name.trim()) setName(c.name);
    if (!url.trim()) setUrl(c.url);
  };

  const submit = () => {
    const body: Record<string, unknown> = {
      name: name.trim(), url: url.trim(), link_target: linkTarget.trim(),
      server_id: serverId, caldav_id: serverId === null ? "" : caldavId.trim(),
      // A subscription has nothing to write to, so the permission goes back to
      // none rather than sitting there meaning nothing.
      write_access: serverId === null || !caldavId.trim() ? "none" : writeAccess,
      auth_user: serverId === null ? authUser.trim() : "",
    };
    if (password && serverId === null) body.auth_password = password;
    onSave(body);
  };

  return (
    <Dialog title={calendar ? tr("calendars.edit_title") : tr("calendars.add")} onClose={onClose}
      hold wide
      foot={<DialogFoot onCancel={onClose} onSave={submit} runs={runs} disabled={!url.trim()} />}>
      <div className="space-y-3">
        <Errorrow text={error} />

        <Field label={tr("calendars.on_server")} hint={tr("calendars.on_server_hint")}>
          <select className={INPUT_VALUE} value={serverId ?? ""}
            onChange={(e) => { setServerId(e.target.value ? Number(e.target.value) : null);
                               setCaldavId(""); }}>
            <option value="">{tr("calendars.without_login")}</option>
            {servers.map((s) => (
              <option key={s.id} value={s.id}>{s.label || s.url}</option>
            ))}
          </select>
        </Field>

        {serverId !== null && (
          <Field label={tr("calendars.which_collection")} hint={tr("calendars.which_collection_hint")}>
            {isFetching && <div className="text-xs text-muted">{tr("common.loading")}</div>}
            {found && !found.ok && (
              <div className="text-xs text-red-400">
                {found.reason === "incomplete" ? tr("servers.incomplete") : found.message}
              </div>
            )}
            {found?.ok && (
              <div className="max-h-52 space-y-1 overflow-y-auto rounded border border-line p-1">
                {found.collections.map((c) => (
                  <button key={c.id} type="button"
                    onClick={() => pick(c)}
                    className={`flex w-full flex-wrap items-center gap-2 rounded px-2 py-1 text-left text-sm ${
                      caldavId === c.id ? "bg-brand/20 text-ink" : "hover:bg-surface"}`}>
                    <span className="font-medium">{c.name}</span>
                    <span className="font-mono text-xs text-muted">{c.id}</span>
                    {c.readOnly && (
                      <span className="text-xs text-muted">{tr("calendars.read_only")}</span>
                    )}
                    {c.taken && caldavId !== c.id && (
                      <span className="text-xs text-muted">{tr("calendars.already_taken")}</span>
                    )}
                  </button>
                ))}
                {found.collections.length === 0 && (
                  <div className="px-2 py-1 text-xs text-muted">{tr("servers.nothing_there")}</div>
                )}
              </div>
            )}
          </Field>
        )}

        <Field label={tr("calendars.name")} hint={tr("calendars.name_hint")}>
          <input className={INPUT_VALUE} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={tr("calendars.url")} hint={tr("calendars.url_hint")}>
          <input className={INPUT_VALUE} value={url} onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <Field label={tr("calendars.link_target")} hint={tr("calendars.link_target_hint")}>
          <input className={INPUT_VALUE} value={linkTarget}
            onChange={(e) => setLinkTarget(e.target.value)} />
        </Field>

        {/* Only where it can mean something: a subscription has nothing to
            write to, and a server that refuses makes every setting here a
            promise that breaks on save. */}
        {serverId !== null && caldavId.trim() !== "" && (
          <Field label={tr("calendars.write_access")}
            hint={refuses ? tr("calendars.server_read_only")
                          : tr("calendars.write_access_hint")}>
            <select className={INPUT_VALUE} value={refuses ? "none" : writeAccess}
              disabled={refuses}
              onChange={(e) => setWriteAccess(e.target.value as typeof writeAccess)}>
              <option value="none">{tr("calendars.write_none")}</option>
              <option value="manual">{tr("calendars.write_manual")}</option>
              <option value="agent">{tr("calendars.write_agent")}</option>
            </select>
          </Field>
        )}

        {/* Only for a subscription: a calendar on a login is read with that
            login, and a second set of credentials here would be a second answer
            to the same question. */}
        {serverId === null && (
          <div className="flex flex-wrap gap-3">
            <div className="min-w-[12rem] flex-1">
              <Field label={tr("calendars.user")} hint={tr("calendars.subscription_auth_hint")}>
                <input className={INPUT_VALUE} value={authUser}
                  onChange={(e) => setAuthUser(e.target.value)} />
              </Field>
            </div>
            <div className="min-w-[12rem] flex-1">
              <Field label={tr("calendars.password")}
                hint={calendar?.has_password ? tr("calendars.password_set_hint")
                                             : tr("calendars.password_hint")}>
                <input className={INPUT_VALUE} type="password" value={password}
                  placeholder={calendar?.has_password ? "••••••••" : ""}
                  onChange={(e) => setPassword(e.target.value)} />
              </Field>
            </div>
          </div>
        )}
      </div>
    </Dialog>
  );
}
