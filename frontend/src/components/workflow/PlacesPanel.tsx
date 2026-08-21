import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDateTime } from "../../lib/formatTime";
import { language, tr } from "../../i18n";
import {
  ApiError, seriesApi, type Grant, type Place, type Series } from "../../api";
import {
  Actions, Area, Dialog, DialogFoot, INPUT_VALUE, Tag, Field, Errorrow, ICON,
  IconButton, Button, Listing, ListingEmpty, ListenLine, DeleteDialog } from "../ui";

/**
 * Location series: the devices whose trace Traccoon records.
 *
 * Here stands the administration, not the map — that is a plugin. What a device needs in order
 * to report is an address with a token; what Traccoon makes of it is decided by three rules
 * (rest filter, minimum distance, accuracy limit) and the named places further down.
 */

const COLORS = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#a855f7", "#06b6d4", "#ec4899"];

/** Thousands in the language of the UI, not in that of the browser — the two are not the same
 *  thing here: the language hangs on the logged-in person. */
const number = (n: number) => n.toLocaleString(language());

const EMPTY = {
  key: "", name: "", color: COLORS[0],
  min_distance_m: 25, min_interval_s: 300, max_accuracy_m: 500,
};

export default function LocationsPanel() {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Series | {} | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Series | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState("");

  const { data: series } = useQuery({
    queryKey: ["series", "location"],
    queryFn: () => seriesApi.list("location"),
    refetchInterval: 60_000,
  });

  const inv = () => qc.invalidateQueries({ queryKey: ["series"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  const save = useMutation({
    mutationFn: ({ key, body }: { key: string | null; body: Record<string, any> }) => {
      const { min_distance_m, min_interval_s, max_accuracy_m, ...remainder } = body;
      const settings = { min_distance_m, min_interval_s, max_accuracy_m };
      return key ? seriesApi.update(key, { ...remainder, settings })
                 : seriesApi.create({ ...remainder, kind: "location", settings });
      // `rest` carries the key along: on creation it is the name, on a change the renaming.
      // The server checks whether the new one is already taken.
    },
    onSuccess: () => { setDialog(null); setErr(""); inv(); }, onError: fail,
  });
  const remove = useMutation({
    mutationFn: (key: string) => seriesApi.del(key),
    onSuccess: () => { setDeleteTarget(null); inv(); }, onError: fail,
  });

  return (
    <div className="space-y-4">
      <Area title={tr("places.devices")} hint={tr("places.devices_whose_track_recorded")}
        tools={<Button variant="primary" onClick={() => setDialog({})}>
          {tr("places.add_device")}</Button>}>
        <Errorrow text={err} />
        <Listing>
          {series?.map((r) => (
            <DeviceLine key={r.key} series={r} open={open === r.key}
              onOpen={() => setOpen(open === r.key ? null : r.key)}
              onEdit={() => setDialog(r)}
              onDelete={() => setDeleteTarget(r)}
              onError={fail} />
          ))}
          {series?.length === 0 && <ListingEmpty>{tr("places.no_device_yet")}</ListingEmpty>}
        </Listing>
      </Area>

      <Places onError={fail} />

      {dialog && (
        <DeviceDialog series={"key" in dialog ? (dialog as Series) : null}
          runs={save.isPending}
          onClose={() => setDialog(null)}
          onSave={(body) => save.mutate({
            key: "key" in dialog ? (dialog as Series).key : null, body })} />
      )}
      {deleteTarget && (
        <DeleteDialog was={deleteTarget.name || deleteTarget.key}
          hint={tr("places.count_points_deleted", { count: String(deleteTarget.points) })}
          runs={remove.isPending}
          onClose={() => setDeleteTarget(null)}
          onDelete={() => remove.mutate(deleteTarget.key)} />
      )}
    </div>
  );
}

// ── Ein Gerät ────────────────────────────────────────────────────────────────

function DeviceLine({ series: series, open: open, onOpen: onOpen_it, onEdit, onDelete: onDelete, onError: onError }: {
  series: Series; open: boolean; onOpen: () => void;
  onEdit: () => void; onDelete: () => void; onError: (e: unknown) => void;
}) {
  const state = series.state || {};
  const places: string[] = state.places || [];

  return (
    <ListenLine dimmed={!series.active}>
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="h-3 w-3 shrink-0 rounded-full"
            style={{ background: series.color || COLORS[0] }} />
          <button onClick={onOpen_it} className="min-w-0 truncate text-left font-medium text-ink">
            {series.name || series.key}
          </button>
          <code className="shrink-0 font-mono text-xs text-muted">{series.key}</code>
          {places.map((o) => <Tag key={o} color="green">{o}</Tag>)}
          {!series.own && <Tag color="neutral">{series.owner}</Tag>}
          <div className="flex-1" />
          <span className="shrink-0 text-xs text-muted">
            {number(series.points)} {tr("places.points")}
          </span>
          {series.own && (
            <Actions>
              <IconButton icon={ICON.edit} title={tr("common.edit")}
                onClick={onEdit} />
              <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                onClick={onDelete} />
            </Actions>
          )}
        </div>
        <div className="flex flex-wrap gap-x-4 text-xs text-muted">
          <span>{tr("places.last")}: {series.last_at ? formatDateTime(series.last_at)
            : tr("places.never")}</span>
          {state.battery != null && <span>{Math.round(state.battery)} %</span>}
          {state.accuracy != null && <span>±{Math.round(state.accuracy)} m</span>}
          {state.lat != null && (
            <span className="font-mono">{state.lat.toFixed(5)}, {state.lon.toFixed(5)}</span>
          )}
        </div>
        {open && series.own && <Connection series={series} onError={onError} />}
      </div>
    </ListenLine>
  );
}

/** The address for reporting and the grants — both only on a click, both for owners only. */
function Connection({ series: series, onError: onError }: { series: Series; onError: (e: unknown) => void }) {
  const qc = useQueryClient();
  const [address, setAddress] = useState("");
  const [newUser, setNewUser] = useState("");

  const { data: grants } = useQuery({
    queryKey: ["series", series.key, "shares"], queryFn: () => seriesApi.shares(series.key),
  });
  const invF = () => qc.invalidateQueries({ queryKey: ["series", series.key, "shares"] });

  const show = useMutation({
    mutationFn: () => series.has_token ? seriesApi.token(series.key)
                                      : seriesApi.newToken(series.key),
    onSuccess: (d) => setAddress(`${location.origin}${d.path}`),
    onError: onError,
  });
  const renew = useMutation({
    mutationFn: () => seriesApi.newToken(series.key),
    onSuccess: (d) => {
      setAddress(`${location.origin}${d.path}`);
      qc.invalidateQueries({ queryKey: ["series"] });
    },
    onError: onError,
  });
  const share = useMutation({
    mutationFn: (user_id: number) => seriesApi.share(series.key, { user_id, level: "view" }),
    onSuccess: () => { setNewUser(""); invF(); }, onError: onError,
  });
  const revoke = useMutation({
    mutationFn: (id: number) => seriesApi.unshare(series.key, id),
    onSuccess: invF, onError: onError,
  });

  return (
    <div className="space-y-3 rounded border border-line bg-card p-3">
      <div>
        <div className="mb-1 text-xs font-medium text-ink">{tr("places.reporting_address")}</div>
        <p className="mb-2 text-xs text-muted">{tr("places.enter_address_device_automation")}</p>
        <div className="flex items-center gap-2">
          {address ? (
            <input readOnly value={address} onFocus={(e) => e.target.select()}
              className={`${INPUT_VALUE} font-mono text-xs`} />
          ) : (
            <Button small runs={show.isPending} onClick={() => show.mutate()}>
              {series.has_token ? tr("places.show_address") : tr("places.create_address")}
            </Button>
          )}
          {series.has_token && (
            <Button small runs={renew.isPending} onClick={() => renew.mutate()}
              title={tr("places.old_address_stops_working")}>
              {tr("places.regenerate")}
            </Button>
          )}
        </div>
      </div>

      <div>
        <div className="mb-1 text-xs font-medium text-ink">{tr("places.shared")}</div>
        {grants?.length ? (
          <div className="mb-2 space-y-1">
            {grants.map((f: Grant) => (
              <div key={f.id} className="flex items-center gap-2 text-xs">
                <span className="text-ink">{f.username}</span>
                <Tag color="neutral">{f.level}</Tag>
                <div className="flex-1" />
                <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                  onClick={() => revoke.mutate(f.id)} />
              </div>
            ))}
          </div>
        ) : <p className="mb-2 text-xs text-muted">{tr("places.not_shared_anyone")}</p>}
        <div className="flex items-center gap-2">
          <input value={newUser} onChange={(e) => setNewUser(e.target.value)}
            placeholder={tr("places.user_id")} inputMode="numeric"
            className={`${INPUT_VALUE} max-w-[140px]`} />
          <Button small disabled={!newUser.trim()} runs={share.isPending}
            onClick={() => share.mutate(Number(newUser))}>
            {tr("places.share")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function DeviceDialog({ series: series, runs: running, onClose, onSave }: {
  series: Series | null; runs: boolean;
  onClose: () => void; onSave: (body: Record<string, any>) => void;
}) {
  const [form, setForm] = useState(series ? {
    key: series.key, name: series.name, color: series.color || COLORS[0],
    min_distance_m: series.settings?.min_distance_m ?? EMPTY.min_distance_m,
    min_interval_s: series.settings?.min_interval_s ?? EMPTY.min_interval_s,
    max_accuracy_m: series.settings?.max_accuracy_m ?? EMPTY.max_accuracy_m,
  } : { ...EMPTY });
  const set = (field: string, value: any) => setForm((f) => ({ ...f, [field]: value }));

  return (
    <Dialog title={series ? tr("places.edit_device") : tr("places.add_device")}
      onClose={onClose}
      foot={<DialogFoot onCancel={onClose} runs={running}
        disabled={!form.key.trim()}
        onSave={() => onSave(form)} />}>
      <div className="space-y-3">
        <Field label={tr("places.key")}
          hint={series ? tr("places.renaming_possible_flows_name") : tr("places.short_permanent_e_g")}>
          <input value={form.key} className={INPUT_VALUE}
            placeholder="tracker.pixel" onChange={(e) => set("key", e.target.value)} />
        </Field>
        <Field label={tr("places.name")}>
          <input value={form.name} className={INPUT_VALUE} placeholder="Pixel 9"
            onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field label={tr("places.colour")} hint={tr("places.map_draws_track_colour")}>
          <div className="flex gap-1.5">
            {COLORS.map((f) => (
              <button key={f} type="button" onClick={() => set("color", f)}
                title={f}
                className={`h-6 w-6 rounded-full border-2 ${
                  form.color === f ? "border-ink" : "border-transparent"}`}
                style={{ background: f }} />
            ))}
          </div>
        </Field>
        <Field label={tr("places.minimum_distance_m")} hint={tr("places.point_only_stored_when")}>
          <input type="number" min={0} value={form.min_distance_m} className={INPUT_VALUE}
            onChange={(e) => set("min_distance_m", Number(e.target.value))} />
        </Field>
        <Field label={tr("places.latest_after_s")}>
          <input type="number" min={0} value={form.min_interval_s} className={INPUT_VALUE}
            onChange={(e) => set("min_interval_s", Number(e.target.value))} />
        </Field>
        <Field label={tr("places.accuracy_most_m")} hint={tr("places.less_accurate_reports_discarded")}>
          <input type="number" min={0} value={form.max_accuracy_m} className={INPUT_VALUE}
            onChange={(e) => set("max_accuracy_m", Number(e.target.value))} />
        </Field>
      </div>
    </Dialog>
  );
}

// ── Orte ─────────────────────────────────────────────────────────────────────

const PLACE_EMPTY = { key: "", name: "", lat: 0, lon: 0, radius_m: 150, notify: true,
                   color: "#f59e0b" };

function Places({ onError: onError }: { onError: (e: unknown) => void }) {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Place | {} | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Place | null>(null);

  const { data: places } = useQuery({ queryKey: ["places"], queryFn: () => seriesApi.places() });
  const inv = () => qc.invalidateQueries({ queryKey: ["places"] });

  const save = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: Record<string, any> }) =>
      id ? seriesApi.placeChange(id, body) : seriesApi.placeCreate(body),
    onSuccess: () => { setDialog(null); inv(); }, onError: onError,
  });
  const remove = useMutation({
    mutationFn: (id: number) => seriesApi.placeDelete(id),
    onSuccess: () => { setDeleteTarget(null); inv(); }, onError: onError,
  });

  return (
    <Area title={tr("places.places")} hint={tr("places.named_circles_entering_leaving")}
      tools={<Button variant="primary" onClick={() => setDialog({})}>
        {tr("places.add_place")}</Button>}>
      <Listing>
        {places?.map((o) => (
          <ListenLine key={o.id}>
            <div className="flex items-center gap-2">
              <span className="h-3 w-3 shrink-0 rounded-full"
                style={{ background: o.color || "#f59e0b" }} />
              <span className="font-medium text-ink">{o.name || o.key}</span>
              <code className="font-mono text-xs text-muted">{o.key}</code>
              {!o.notify && <Tag color="neutral">{tr("places.still")}</Tag>}
              <div className="flex-1" />
              <span className="font-mono text-xs text-muted">
                {o.lat.toFixed(5)}, {o.lon.toFixed(5)} · {o.radius_m} m
              </span>
              <Actions>
                <IconButton icon={ICON.edit} title={tr("common.edit")}
                  onClick={() => setDialog(o)} />
                <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                  onClick={() => setDeleteTarget(o)} />
              </Actions>
            </div>
          </ListenLine>
        ))}
        {places?.length === 0 && <ListingEmpty>{tr("places.no_place_yet")}</ListingEmpty>}
      </Listing>

      {dialog && (
        <PlaceDialog place={"id" in dialog ? (dialog as Place) : null}
          runs={save.isPending}
          onClose={() => setDialog(null)}
          onSave={(body) => save.mutate({
            id: "id" in dialog ? (dialog as Place).id : null, body })} />
      )}
      {deleteTarget && (
        <DeleteDialog was={deleteTarget.name || deleteTarget.key}
          runs={remove.isPending}
          onClose={() => setDeleteTarget(null)}
          onDelete={() => remove.mutate(deleteTarget.id)} />
      )}
    </Area>
  );
}

function PlaceDialog({ place, runs: running, onClose, onSave }: {
  place: Place | null; runs: boolean;
  onClose: () => void; onSave: (body: Record<string, any>) => void;
}) {
  const [form, setForm] = useState(place ? {
    key: place.key, name: place.name, lat: place.lat, lon: place.lon,
    radius_m: place.radius_m, notify: place.notify, color: place.color || PLACE_EMPTY.color,
  } : { ...PLACE_EMPTY });
  const set = (field: string, value: any) => setForm((f) => ({ ...f, [field]: value }));

  return (
    <Dialog title={place ? tr("places.edit_place") : tr("places.add_place")}
      onClose={onClose}
      foot={<DialogFoot onCancel={onClose} runs={running}
        disabled={!form.key.trim() || (!form.lat && !form.lon)}
        onSave={() => onSave(form)} />}>
      <div className="space-y-3">
        <Field label={tr("places.key")} hint={tr("places.name_flow_refers_e")}>
          <input value={form.key} className={INPUT_VALUE} placeholder="zuhause"
            onChange={(e) => set("key", e.target.value)} />
        </Field>
        <Field label={tr("places.name")}>
          <input value={form.name} className={INPUT_VALUE} placeholder="Zuhause"
            onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field label={tr("places.latitude_longitude")} hint={tr("places.read_map_take_device")}>
          <div className="flex gap-2">
            <input type="number" step="any" value={form.lat} className={INPUT_VALUE}
              placeholder="50.0825" onChange={(e) => set("lat", Number(e.target.value))} />
            <input type="number" step="any" value={form.lon} className={INPUT_VALUE}
              placeholder="10.5663" onChange={(e) => set("lon", Number(e.target.value))} />
          </div>
        </Field>
        <Field label={tr("places.radius")} hint={tr("places.leaving_only_counts_50")}>
          <input type="number" min={10} value={form.radius_m} className={INPUT_VALUE}
            onChange={(e) => set("radius_m", Number(e.target.value))} />
        </Field>
        <Field label={tr("places.event")} hint={tr("places.off_place_only_drawn")}>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={form.notify}
              onChange={(e) => set("notify", e.target.checked)} />
            {tr("places.report_entering_leaving")}
          </label>
        </Field>
      </div>
    </Dialog>
  );
}
