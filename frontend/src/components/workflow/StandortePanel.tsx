import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDateTime } from "../../lib/formatTime";
import { language, tr } from "../../i18n";
import {
  ApiError, seriesApi, type Grant, type Ort, type Series } from "../../api";
import {
  Actions, Area, Dialog, DialogFuss, INPUT_VALUE, Etikett, Field, Fehlerzeile, ICON,
  IconButton, Button, Listing, ListingLeer, ListenLine, LoeschDialog } from "../ui";

/**
 * Standortreihen: die Geräte, deren Spur Traccoon mitschreibt.
 *
 * Hier steht die Verwaltung, nicht die Karte — die ist ein Plugin. Was ein Gerät braucht, um
 * zu melden, ist eine Adresse mit Token; was Traccoon daraus macht, entscheiden drei Regeln
 * (Ruhefilter, Mindestabstand, Genauigkeitsgrenze) und die benannten Orte weiter unten.
 */

const FARBEN = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#a855f7", "#06b6d4", "#ec4899"];

/** Tausender in der Sprache der Oberfläche, nicht in der des Browsers — die beiden sind hier
 *  nicht dasselbe: Die Sprache haengt am angemeldeten Menschen. */
const zahl = (n: number) => n.toLocaleString(language());

const LEER = {
  key: "", name: "", color: FARBEN[0],
  min_distance_m: 25, min_interval_s: 300, max_accuracy_m: 500,
};

export default function LocationsPanel() {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Series | {} | null>(null);
  const [loeschTarget, setLoeschTarget] = useState<Series | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState("");

  const { data: series } = useQuery({
    queryKey: ["series", "location"],
    queryFn: () => seriesApi.list("location"),
    refetchInterval: 60_000,
  });

  const inv = () => qc.invalidateQueries({ queryKey: ["series"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));

  const speichern = useMutation({
    mutationFn: ({ key, body }: { key: string | null; body: Record<string, any> }) => {
      const { min_distance_m, min_interval_s, max_accuracy_m, ...remainder } = body;
      const settings = { min_distance_m, min_interval_s, max_accuracy_m };
      return key ? seriesApi.update(key, { ...remainder, settings })
                 : seriesApi.create({ ...remainder, kind: "location", settings });
      // `rest` trägt den Schlüssel mit: Beim Anlegen ist er der Name, beim Ändern das
      // Umbenennen. Der Server prüft, ob der neue schon vergeben ist.
    },
    onSuccess: () => { setDialog(null); setErr(""); inv(); }, onError: fail,
  });
  const remove = useMutation({
    mutationFn: (key: string) => seriesApi.del(key),
    onSuccess: () => { setLoeschTarget(null); inv(); }, onError: fail,
  });

  return (
    <div className="space-y-4">
      <Area titel={tr("standorte.geraete")} hinweis={tr("standorte.einleitung")}
        werkzeuge={<Button art="haupt" onClick={() => setDialog({})}>
          {tr("standorte.geraet_anlegen")}</Button>}>
        <Fehlerzeile text={err} />
        <Listing>
          {series?.map((r) => (
            <DeviceLine key={r.key} reihe={r} offen={open === r.key}
              onOeffnen={() => setOpen(open === r.key ? null : r.key)}
              onBearbeiten={() => setDialog(r)}
              onLoeschen={() => setLoeschTarget(r)}
              onFehler={fail} />
          ))}
          {series?.length === 0 && <ListingLeer>{tr("standorte.keine_geraete")}</ListingLeer>}
        </Listing>
      </Area>

      <Orte onFehler={fail} />

      {dialog && (
        <DeviceDialog reihe={"key" in dialog ? (dialog as Series) : null}
          laeuft={speichern.isPending}
          onClose={() => setDialog(null)}
          onSpeichern={(body) => speichern.mutate({
            key: "key" in dialog ? (dialog as Series).key : null, body })} />
      )}
      {loeschTarget && (
        <LoeschDialog was={loeschTarget.name || loeschTarget.key}
          hinweis={tr("standorte.loeschen_hinweis", { anzahl: String(loeschTarget.points) })}
          laeuft={remove.isPending}
          onClose={() => setLoeschTarget(null)}
          onLoeschen={() => remove.mutate(loeschTarget.key)} />
      )}
    </div>
  );
}

// ── Ein Gerät ────────────────────────────────────────────────────────────────

function DeviceLine({ reihe: series, offen: open, onOeffnen: onOpen_it, onBearbeiten, onLoeschen: onDelete, onFehler: onError }: {
  reihe: Series; offen: boolean; onOeffnen: () => void;
  onBearbeiten: () => void; onLoeschen: () => void; onFehler: (e: unknown) => void;
}) {
  const state = series.state || {};
  const orte: string[] = state.places || [];

  return (
    <ListenLine gedimmt={!series.active}>
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="h-3 w-3 shrink-0 rounded-full"
            style={{ background: series.color || FARBEN[0] }} />
          <button onClick={onOpen_it} className="min-w-0 truncate text-left font-medium text-ink">
            {series.name || series.key}
          </button>
          <code className="shrink-0 font-mono text-xs text-muted">{series.key}</code>
          {orte.map((o) => <Etikett key={o} farbe="gruen">{o}</Etikett>)}
          {!series.own && <Etikett farbe="neutral">{series.owner}</Etikett>}
          <div className="flex-1" />
          <span className="shrink-0 text-xs text-muted">
            {zahl(series.points)} {tr("standorte.punkte")}
          </span>
          {series.own && (
            <Actions>
              <IconButton icon={ICON.bearbeiten} titel={tr("common.bearbeiten")}
                onClick={onBearbeiten} />
              <IconButton icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                onClick={onDelete} />
            </Actions>
          )}
        </div>
        <div className="flex flex-wrap gap-x-4 text-xs text-muted">
          <span>{tr("standorte.zuletzt")}: {series.last_at ? formatDateTime(series.last_at)
            : tr("standorte.nie")}</span>
          {state.battery != null && <span>{Math.round(state.battery)} %</span>}
          {state.accuracy != null && <span>±{Math.round(state.accuracy)} m</span>}
          {state.lat != null && (
            <span className="font-mono">{state.lat.toFixed(5)}, {state.lon.toFixed(5)}</span>
          )}
        </div>
        {open && series.own && <Anschluss reihe={series} onFehler={onError} />}
      </div>
    </ListenLine>
  );
}

/** Adresse zum Melden und die Freigaben — beides erst auf Klick, beides nur für Besitzer. */
function Anschluss({ reihe: series, onFehler: onError }: { reihe: Series; onFehler: (e: unknown) => void }) {
  const qc = useQueryClient();
  const [adresse, setAdresse] = useState("");
  const [neuerUser, setNeuerUser] = useState("");

  const { data: grants } = useQuery({
    queryKey: ["series", series.key, "shares"], queryFn: () => seriesApi.shares(series.key),
  });
  const invF = () => qc.invalidateQueries({ queryKey: ["series", series.key, "shares"] });

  const zeigen = useMutation({
    mutationFn: () => series.has_token ? seriesApi.token(series.key)
                                      : seriesApi.neuesToken(series.key),
    onSuccess: (d) => setAdresse(`${location.origin}${d.path}`),
    onError: onError,
  });
  const renew = useMutation({
    mutationFn: () => seriesApi.neuesToken(series.key),
    onSuccess: (d) => {
      setAdresse(`${location.origin}${d.path}`);
      qc.invalidateQueries({ queryKey: ["series"] });
    },
    onError: onError,
  });
  const teilen = useMutation({
    mutationFn: (user_id: number) => seriesApi.share(series.key, { user_id, level: "view" }),
    onSuccess: () => { setNeuerUser(""); invF(); }, onError: onError,
  });
  const entziehen = useMutation({
    mutationFn: (id: number) => seriesApi.unshare(series.key, id),
    onSuccess: invF, onError: onError,
  });

  return (
    <div className="space-y-3 rounded border border-line bg-card p-3">
      <div>
        <div className="mb-1 text-xs font-medium text-ink">{tr("standorte.adresse")}</div>
        <p className="mb-2 text-xs text-muted">{tr("standorte.adresse_hinweis")}</p>
        <div className="flex items-center gap-2">
          {adresse ? (
            <input readOnly value={adresse} onFocus={(e) => e.target.select()}
              className={`${INPUT_VALUE} font-mono text-xs`} />
          ) : (
            <Button klein laeuft={zeigen.isPending} onClick={() => zeigen.mutate()}>
              {series.has_token ? tr("standorte.adresse_zeigen") : tr("standorte.adresse_erzeugen")}
            </Button>
          )}
          {series.has_token && (
            <Button klein laeuft={renew.isPending} onClick={() => renew.mutate()}
              titel={tr("standorte.token_erneuern_hinweis")}>
              {tr("standorte.token_erneuern")}
            </Button>
          )}
        </div>
      </div>

      <div>
        <div className="mb-1 text-xs font-medium text-ink">{tr("standorte.freigaben")}</div>
        {grants?.length ? (
          <div className="mb-2 space-y-1">
            {grants.map((f: Grant) => (
              <div key={f.id} className="flex items-center gap-2 text-xs">
                <span className="text-ink">{f.username}</span>
                <Etikett farbe="neutral">{f.level}</Etikett>
                <div className="flex-1" />
                <IconButton icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                  onClick={() => entziehen.mutate(f.id)} />
              </div>
            ))}
          </div>
        ) : <p className="mb-2 text-xs text-muted">{tr("standorte.keine_freigaben")}</p>}
        <div className="flex items-center gap-2">
          <input value={neuerUser} onChange={(e) => setNeuerUser(e.target.value)}
            placeholder={tr("standorte.nutzer_id")} inputMode="numeric"
            className={`${INPUT_VALUE} max-w-[140px]`} />
          <Button klein disabled={!neuerUser.trim()} laeuft={teilen.isPending}
            onClick={() => teilen.mutate(Number(neuerUser))}>
            {tr("standorte.freigeben")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function DeviceDialog({ reihe: series, laeuft: running, onClose, onSpeichern }: {
  reihe: Series | null; laeuft: boolean;
  onClose: () => void; onSpeichern: (body: Record<string, any>) => void;
}) {
  const [form, setForm] = useState(series ? {
    key: series.key, name: series.name, color: series.color || FARBEN[0],
    min_distance_m: series.settings?.min_distance_m ?? LEER.min_distance_m,
    min_interval_s: series.settings?.min_interval_s ?? LEER.min_interval_s,
    max_accuracy_m: series.settings?.max_accuracy_m ?? LEER.max_accuracy_m,
  } : { ...LEER });
  const setz = (field: string, value: any) => setForm((f) => ({ ...f, [field]: value }));

  return (
    <Dialog titel={series ? tr("standorte.geraet_bearbeiten") : tr("standorte.geraet_anlegen")}
      onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} laeuft={running}
        deaktiviert={!form.key.trim()}
        onSpeichern={() => onSpeichern(form)} />}>
      <div className="space-y-3">
        <Field label={tr("standorte.schluessel")}
          hinweis={series ? tr("standorte.schluessel_umbenennen") : tr("standorte.schluessel_hinweis")}>
          <input value={form.key} className={INPUT_VALUE}
            placeholder="tracker.pixel" onChange={(e) => setz("key", e.target.value)} />
        </Field>
        <Field label={tr("standorte.name")}>
          <input value={form.name} className={INPUT_VALUE} placeholder="Pixel 9"
            onChange={(e) => setz("name", e.target.value)} />
        </Field>
        <Field label={tr("standorte.farbe")} hinweis={tr("standorte.farbe_hinweis")}>
          <div className="flex gap-1.5">
            {FARBEN.map((f) => (
              <button key={f} type="button" onClick={() => setz("color", f)}
                title={f}
                className={`h-6 w-6 rounded-full border-2 ${
                  form.color === f ? "border-ink" : "border-transparent"}`}
                style={{ background: f }} />
            ))}
          </div>
        </Field>
        <Field label={tr("standorte.mindestabstand")} hinweis={tr("standorte.ruhefilter_hinweis")}>
          <input type="number" min={0} value={form.min_distance_m} className={INPUT_VALUE}
            onChange={(e) => setz("min_distance_m", Number(e.target.value))} />
        </Field>
        <Field label={tr("standorte.mindestabstand_zeit")}>
          <input type="number" min={0} value={form.min_interval_s} className={INPUT_VALUE}
            onChange={(e) => setz("min_interval_s", Number(e.target.value))} />
        </Field>
        <Field label={tr("standorte.genauigkeit")} hinweis={tr("standorte.genauigkeit_hinweis")}>
          <input type="number" min={0} value={form.max_accuracy_m} className={INPUT_VALUE}
            onChange={(e) => setz("max_accuracy_m", Number(e.target.value))} />
        </Field>
      </div>
    </Dialog>
  );
}

// ── Orte ─────────────────────────────────────────────────────────────────────

const ORT_LEER = { key: "", name: "", lat: 0, lon: 0, radius_m: 150, notify: true,
                   color: "#f59e0b" };

function Orte({ onFehler: onError }: { onFehler: (e: unknown) => void }) {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Ort | {} | null>(null);
  const [loeschTarget, setLoeschTarget] = useState<Ort | null>(null);

  const { data: orte } = useQuery({ queryKey: ["places"], queryFn: () => seriesApi.orte() });
  const inv = () => qc.invalidateQueries({ queryKey: ["places"] });

  const speichern = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: Record<string, any> }) =>
      id ? seriesApi.ortAendern(id, body) : seriesApi.ortAnlegen(body),
    onSuccess: () => { setDialog(null); inv(); }, onError: onError,
  });
  const remove = useMutation({
    mutationFn: (id: number) => seriesApi.ortLoeschen(id),
    onSuccess: () => { setLoeschTarget(null); inv(); }, onError: onError,
  });

  return (
    <Area titel={tr("standorte.orte")} hinweis={tr("standorte.orte_einleitung")}
      werkzeuge={<Button art="haupt" onClick={() => setDialog({})}>
        {tr("standorte.ort_anlegen")}</Button>}>
      <Listing>
        {orte?.map((o) => (
          <ListenLine key={o.id}>
            <div className="flex items-center gap-2">
              <span className="h-3 w-3 shrink-0 rounded-full"
                style={{ background: o.color || "#f59e0b" }} />
              <span className="font-medium text-ink">{o.name || o.key}</span>
              <code className="font-mono text-xs text-muted">{o.key}</code>
              {!o.notify && <Etikett farbe="neutral">{tr("standorte.still")}</Etikett>}
              <div className="flex-1" />
              <span className="font-mono text-xs text-muted">
                {o.lat.toFixed(5)}, {o.lon.toFixed(5)} · {o.radius_m} m
              </span>
              <Actions>
                <IconButton icon={ICON.bearbeiten} titel={tr("common.bearbeiten")}
                  onClick={() => setDialog(o)} />
                <IconButton icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                  onClick={() => setLoeschTarget(o)} />
              </Actions>
            </div>
          </ListenLine>
        ))}
        {orte?.length === 0 && <ListingLeer>{tr("standorte.keine_orte")}</ListingLeer>}
      </Listing>

      {dialog && (
        <OrtDialog ort={"id" in dialog ? (dialog as Ort) : null}
          laeuft={speichern.isPending}
          onClose={() => setDialog(null)}
          onSpeichern={(body) => speichern.mutate({
            id: "id" in dialog ? (dialog as Ort).id : null, body })} />
      )}
      {loeschTarget && (
        <LoeschDialog was={loeschTarget.name || loeschTarget.key}
          laeuft={remove.isPending}
          onClose={() => setLoeschTarget(null)}
          onLoeschen={() => remove.mutate(loeschTarget.id)} />
      )}
    </Area>
  );
}

function OrtDialog({ ort, laeuft: running, onClose, onSpeichern }: {
  ort: Ort | null; laeuft: boolean;
  onClose: () => void; onSpeichern: (body: Record<string, any>) => void;
}) {
  const [form, setForm] = useState(ort ? {
    key: ort.key, name: ort.name, lat: ort.lat, lon: ort.lon,
    radius_m: ort.radius_m, notify: ort.notify, color: ort.color || ORT_LEER.color,
  } : { ...ORT_LEER });
  const setz = (field: string, value: any) => setForm((f) => ({ ...f, [field]: value }));

  return (
    <Dialog titel={ort ? tr("standorte.ort_bearbeiten") : tr("standorte.ort_anlegen")}
      onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} laeuft={running}
        deaktiviert={!form.key.trim() || (!form.lat && !form.lon)}
        onSpeichern={() => onSpeichern(form)} />}>
      <div className="space-y-3">
        <Field label={tr("standorte.schluessel")} hinweis={tr("standorte.ort_schluessel_hinweis")}>
          <input value={form.key} className={INPUT_VALUE} placeholder="zuhause"
            onChange={(e) => setz("key", e.target.value)} />
        </Field>
        <Field label={tr("standorte.name")}>
          <input value={form.name} className={INPUT_VALUE} placeholder="Zuhause"
            onChange={(e) => setz("name", e.target.value)} />
        </Field>
        <Field label={tr("standorte.koordinaten")} hinweis={tr("standorte.koordinaten_hinweis")}>
          <div className="flex gap-2">
            <input type="number" step="any" value={form.lat} className={INPUT_VALUE}
              placeholder="50.0825" onChange={(e) => setz("lat", Number(e.target.value))} />
            <input type="number" step="any" value={form.lon} className={INPUT_VALUE}
              placeholder="10.5663" onChange={(e) => setz("lon", Number(e.target.value))} />
          </div>
        </Field>
        <Field label={tr("standorte.radius")} hinweis={tr("standorte.radius_hinweis")}>
          <input type="number" min={10} value={form.radius_m} className={INPUT_VALUE}
            onChange={(e) => setz("radius_m", Number(e.target.value))} />
        </Field>
        <Field label={tr("standorte.meldet")} hinweis={tr("standorte.meldet_hinweis")}>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={form.notify}
              onChange={(e) => setz("notify", e.target.checked)} />
            {tr("standorte.meldet_an")}
          </label>
        </Field>
      </div>
    </Dialog>
  );
}
