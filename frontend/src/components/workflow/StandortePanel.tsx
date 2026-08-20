import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDateTime } from "../../lib/formatTime";
import { sprache, tr } from "../../i18n";
import {
  ApiError, seriesApi, type Freigabe, type Ort, type Reihe } from "../../api";
import {
  Aktionen, Bereich, Dialog, DialogFuss, EINGABE, Etikett, Feld, Fehlerzeile, ICON,
  IconKnopf, Knopf, Liste, ListeLeer, ListenZeile, LoeschDialog } from "../ui";

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
const zahl = (n: number) => n.toLocaleString(sprache());

const LEER = {
  key: "", name: "", color: FARBEN[0],
  min_distance_m: 25, min_interval_s: 300, max_accuracy_m: 500,
};

export default function StandortePanel() {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Reihe | {} | null>(null);
  const [loeschZiel, setLoeschZiel] = useState<Reihe | null>(null);
  const [offen, setOffen] = useState<string | null>(null);
  const [err, setErr] = useState("");

  const { data: reihen } = useQuery({
    queryKey: ["series", "location"],
    queryFn: () => seriesApi.list("location"),
    refetchInterval: 60_000,
  });

  const inv = () => qc.invalidateQueries({ queryKey: ["series"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));

  const speichern = useMutation({
    mutationFn: ({ key, body }: { key: string | null; body: Record<string, any> }) => {
      const { min_distance_m, min_interval_s, max_accuracy_m, ...rest } = body;
      const settings = { min_distance_m, min_interval_s, max_accuracy_m };
      return key ? seriesApi.update(key, { ...rest, settings })
                 : seriesApi.create({ ...rest, kind: "location", settings });
    },
    onSuccess: () => { setDialog(null); setErr(""); inv(); }, onError: fail,
  });
  const loeschen = useMutation({
    mutationFn: (key: string) => seriesApi.del(key),
    onSuccess: () => { setLoeschZiel(null); inv(); }, onError: fail,
  });

  return (
    <div className="space-y-4">
      <Bereich titel={tr("standorte.geraete")} hinweis={tr("standorte.einleitung")}
        werkzeuge={<Knopf art="haupt" onClick={() => setDialog({})}>
          {tr("standorte.geraet_anlegen")}</Knopf>}>
        <Fehlerzeile text={err} />
        <Liste>
          {reihen?.map((r) => (
            <GeraetZeile key={r.key} reihe={r} offen={offen === r.key}
              onOeffnen={() => setOffen(offen === r.key ? null : r.key)}
              onBearbeiten={() => setDialog(r)}
              onLoeschen={() => setLoeschZiel(r)}
              onFehler={fail} />
          ))}
          {reihen?.length === 0 && <ListeLeer>{tr("standorte.keine_geraete")}</ListeLeer>}
        </Liste>
      </Bereich>

      <Orte onFehler={fail} />

      {dialog && (
        <GeraetDialog reihe={"key" in dialog ? (dialog as Reihe) : null}
          laeuft={speichern.isPending}
          onClose={() => setDialog(null)}
          onSpeichern={(body) => speichern.mutate({
            key: "key" in dialog ? (dialog as Reihe).key : null, body })} />
      )}
      {loeschZiel && (
        <LoeschDialog was={loeschZiel.name || loeschZiel.key}
          hinweis={tr("standorte.loeschen_hinweis", { anzahl: String(loeschZiel.points) })}
          laeuft={loeschen.isPending}
          onClose={() => setLoeschZiel(null)}
          onLoeschen={() => loeschen.mutate(loeschZiel.key)} />
      )}
    </div>
  );
}

// ── Ein Gerät ────────────────────────────────────────────────────────────────

function GeraetZeile({ reihe, offen, onOeffnen, onBearbeiten, onLoeschen, onFehler }: {
  reihe: Reihe; offen: boolean; onOeffnen: () => void;
  onBearbeiten: () => void; onLoeschen: () => void; onFehler: (e: unknown) => void;
}) {
  const stand = reihe.state || {};
  const orte: string[] = stand.places || [];

  return (
    <ListenZeile gedimmt={!reihe.active}>
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="h-3 w-3 shrink-0 rounded-full"
            style={{ background: reihe.color || FARBEN[0] }} />
          <button onClick={onOeffnen} className="min-w-0 truncate text-left font-medium text-ink">
            {reihe.name || reihe.key}
          </button>
          <code className="shrink-0 font-mono text-xs text-muted">{reihe.key}</code>
          {orte.map((o) => <Etikett key={o} farbe="gruen">{o}</Etikett>)}
          {!reihe.own && <Etikett farbe="neutral">{reihe.owner}</Etikett>}
          <div className="flex-1" />
          <span className="shrink-0 text-xs text-muted">
            {zahl(reihe.points)} {tr("standorte.punkte")}
          </span>
          {reihe.own && (
            <Aktionen>
              <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")}
                onClick={onBearbeiten} />
              <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                onClick={onLoeschen} />
            </Aktionen>
          )}
        </div>
        <div className="flex flex-wrap gap-x-4 text-xs text-muted">
          <span>{tr("standorte.zuletzt")}: {reihe.last_at ? formatDateTime(reihe.last_at)
            : tr("standorte.nie")}</span>
          {stand.battery != null && <span>{Math.round(stand.battery)} %</span>}
          {stand.accuracy != null && <span>±{Math.round(stand.accuracy)} m</span>}
          {stand.lat != null && (
            <span className="font-mono">{stand.lat.toFixed(5)}, {stand.lon.toFixed(5)}</span>
          )}
        </div>
        {offen && reihe.own && <Anschluss reihe={reihe} onFehler={onFehler} />}
      </div>
    </ListenZeile>
  );
}

/** Adresse zum Melden und die Freigaben — beides erst auf Klick, beides nur für Besitzer. */
function Anschluss({ reihe, onFehler }: { reihe: Reihe; onFehler: (e: unknown) => void }) {
  const qc = useQueryClient();
  const [adresse, setAdresse] = useState("");
  const [neuerNutzer, setNeuerNutzer] = useState("");

  const { data: freigaben } = useQuery({
    queryKey: ["series", reihe.key, "shares"], queryFn: () => seriesApi.shares(reihe.key),
  });
  const invF = () => qc.invalidateQueries({ queryKey: ["series", reihe.key, "shares"] });

  const zeigen = useMutation({
    mutationFn: () => reihe.has_token ? seriesApi.token(reihe.key)
                                      : seriesApi.neuesToken(reihe.key),
    onSuccess: (d) => setAdresse(`${location.origin}${d.path}`),
    onError: onFehler,
  });
  const erneuern = useMutation({
    mutationFn: () => seriesApi.neuesToken(reihe.key),
    onSuccess: (d) => {
      setAdresse(`${location.origin}${d.path}`);
      qc.invalidateQueries({ queryKey: ["series"] });
    },
    onError: onFehler,
  });
  const teilen = useMutation({
    mutationFn: (user_id: number) => seriesApi.share(reihe.key, { user_id, level: "view" }),
    onSuccess: () => { setNeuerNutzer(""); invF(); }, onError: onFehler,
  });
  const entziehen = useMutation({
    mutationFn: (id: number) => seriesApi.unshare(reihe.key, id),
    onSuccess: invF, onError: onFehler,
  });

  return (
    <div className="space-y-3 rounded border border-line bg-card p-3">
      <div>
        <div className="mb-1 text-xs font-medium text-ink">{tr("standorte.adresse")}</div>
        <p className="mb-2 text-xs text-muted">{tr("standorte.adresse_hinweis")}</p>
        <div className="flex items-center gap-2">
          {adresse ? (
            <input readOnly value={adresse} onFocus={(e) => e.target.select()}
              className={`${EINGABE} font-mono text-xs`} />
          ) : (
            <Knopf klein laeuft={zeigen.isPending} onClick={() => zeigen.mutate()}>
              {reihe.has_token ? tr("standorte.adresse_zeigen") : tr("standorte.adresse_erzeugen")}
            </Knopf>
          )}
          {reihe.has_token && (
            <Knopf klein laeuft={erneuern.isPending} onClick={() => erneuern.mutate()}
              titel={tr("standorte.token_erneuern_hinweis")}>
              {tr("standorte.token_erneuern")}
            </Knopf>
          )}
        </div>
      </div>

      <div>
        <div className="mb-1 text-xs font-medium text-ink">{tr("standorte.freigaben")}</div>
        {freigaben?.length ? (
          <div className="mb-2 space-y-1">
            {freigaben.map((f: Freigabe) => (
              <div key={f.id} className="flex items-center gap-2 text-xs">
                <span className="text-ink">{f.username}</span>
                <Etikett farbe="neutral">{f.level}</Etikett>
                <div className="flex-1" />
                <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                  onClick={() => entziehen.mutate(f.id)} />
              </div>
            ))}
          </div>
        ) : <p className="mb-2 text-xs text-muted">{tr("standorte.keine_freigaben")}</p>}
        <div className="flex items-center gap-2">
          <input value={neuerNutzer} onChange={(e) => setNeuerNutzer(e.target.value)}
            placeholder={tr("standorte.nutzer_id")} inputMode="numeric"
            className={`${EINGABE} max-w-[140px]`} />
          <Knopf klein disabled={!neuerNutzer.trim()} laeuft={teilen.isPending}
            onClick={() => teilen.mutate(Number(neuerNutzer))}>
            {tr("standorte.freigeben")}
          </Knopf>
        </div>
      </div>
    </div>
  );
}

function GeraetDialog({ reihe, laeuft, onClose, onSpeichern }: {
  reihe: Reihe | null; laeuft: boolean;
  onClose: () => void; onSpeichern: (body: Record<string, any>) => void;
}) {
  const [form, setForm] = useState(reihe ? {
    key: reihe.key, name: reihe.name, color: reihe.color || FARBEN[0],
    min_distance_m: reihe.settings?.min_distance_m ?? LEER.min_distance_m,
    min_interval_s: reihe.settings?.min_interval_s ?? LEER.min_interval_s,
    max_accuracy_m: reihe.settings?.max_accuracy_m ?? LEER.max_accuracy_m,
  } : { ...LEER });
  const setz = (feld: string, wert: any) => setForm((f) => ({ ...f, [feld]: wert }));

  return (
    <Dialog titel={reihe ? tr("standorte.geraet_bearbeiten") : tr("standorte.geraet_anlegen")}
      onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} laeuft={laeuft}
        deaktiviert={!form.key.trim()}
        onSpeichern={() => onSpeichern(form)} />}>
      <div className="space-y-3">
        <Feld label={tr("standorte.schluessel")}
          hinweis={reihe ? tr("standorte.schluessel_fest") : tr("standorte.schluessel_hinweis")}>
          <input value={form.key} disabled={!!reihe} className={EINGABE}
            placeholder="handy.pixel" onChange={(e) => setz("key", e.target.value)} />
        </Feld>
        <Feld label={tr("standorte.name")}>
          <input value={form.name} className={EINGABE} placeholder="Pixel 9"
            onChange={(e) => setz("name", e.target.value)} />
        </Feld>
        <Feld label={tr("standorte.farbe")} hinweis={tr("standorte.farbe_hinweis")}>
          <div className="flex gap-1.5">
            {FARBEN.map((f) => (
              <button key={f} type="button" onClick={() => setz("color", f)}
                title={f}
                className={`h-6 w-6 rounded-full border-2 ${
                  form.color === f ? "border-ink" : "border-transparent"}`}
                style={{ background: f }} />
            ))}
          </div>
        </Feld>
        <Feld label={tr("standorte.mindestabstand")} hinweis={tr("standorte.ruhefilter_hinweis")}>
          <input type="number" min={0} value={form.min_distance_m} className={EINGABE}
            onChange={(e) => setz("min_distance_m", Number(e.target.value))} />
        </Feld>
        <Feld label={tr("standorte.mindestabstand_zeit")}>
          <input type="number" min={0} value={form.min_interval_s} className={EINGABE}
            onChange={(e) => setz("min_interval_s", Number(e.target.value))} />
        </Feld>
        <Feld label={tr("standorte.genauigkeit")} hinweis={tr("standorte.genauigkeit_hinweis")}>
          <input type="number" min={0} value={form.max_accuracy_m} className={EINGABE}
            onChange={(e) => setz("max_accuracy_m", Number(e.target.value))} />
        </Feld>
      </div>
    </Dialog>
  );
}

// ── Orte ─────────────────────────────────────────────────────────────────────

const ORT_LEER = { key: "", name: "", lat: 0, lon: 0, radius_m: 150, notify: true,
                   color: "#f59e0b" };

function Orte({ onFehler }: { onFehler: (e: unknown) => void }) {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Ort | {} | null>(null);
  const [loeschZiel, setLoeschZiel] = useState<Ort | null>(null);

  const { data: orte } = useQuery({ queryKey: ["places"], queryFn: () => seriesApi.orte() });
  const inv = () => qc.invalidateQueries({ queryKey: ["places"] });

  const speichern = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: Record<string, any> }) =>
      id ? seriesApi.ortAendern(id, body) : seriesApi.ortAnlegen(body),
    onSuccess: () => { setDialog(null); inv(); }, onError: onFehler,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => seriesApi.ortLoeschen(id),
    onSuccess: () => { setLoeschZiel(null); inv(); }, onError: onFehler,
  });

  return (
    <Bereich titel={tr("standorte.orte")} hinweis={tr("standorte.orte_einleitung")}
      werkzeuge={<Knopf art="haupt" onClick={() => setDialog({})}>
        {tr("standorte.ort_anlegen")}</Knopf>}>
      <Liste>
        {orte?.map((o) => (
          <ListenZeile key={o.id}>
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
              <Aktionen>
                <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")}
                  onClick={() => setDialog(o)} />
                <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                  onClick={() => setLoeschZiel(o)} />
              </Aktionen>
            </div>
          </ListenZeile>
        ))}
        {orte?.length === 0 && <ListeLeer>{tr("standorte.keine_orte")}</ListeLeer>}
      </Liste>

      {dialog && (
        <OrtDialog ort={"id" in dialog ? (dialog as Ort) : null}
          laeuft={speichern.isPending}
          onClose={() => setDialog(null)}
          onSpeichern={(body) => speichern.mutate({
            id: "id" in dialog ? (dialog as Ort).id : null, body })} />
      )}
      {loeschZiel && (
        <LoeschDialog was={loeschZiel.name || loeschZiel.key}
          laeuft={loeschen.isPending}
          onClose={() => setLoeschZiel(null)}
          onLoeschen={() => loeschen.mutate(loeschZiel.id)} />
      )}
    </Bereich>
  );
}

function OrtDialog({ ort, laeuft, onClose, onSpeichern }: {
  ort: Ort | null; laeuft: boolean;
  onClose: () => void; onSpeichern: (body: Record<string, any>) => void;
}) {
  const [form, setForm] = useState(ort ? {
    key: ort.key, name: ort.name, lat: ort.lat, lon: ort.lon,
    radius_m: ort.radius_m, notify: ort.notify, color: ort.color || ORT_LEER.color,
  } : { ...ORT_LEER });
  const setz = (feld: string, wert: any) => setForm((f) => ({ ...f, [feld]: wert }));

  return (
    <Dialog titel={ort ? tr("standorte.ort_bearbeiten") : tr("standorte.ort_anlegen")}
      onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} laeuft={laeuft}
        deaktiviert={!form.key.trim() || (!form.lat && !form.lon)}
        onSpeichern={() => onSpeichern(form)} />}>
      <div className="space-y-3">
        <Feld label={tr("standorte.schluessel")} hinweis={tr("standorte.ort_schluessel_hinweis")}>
          <input value={form.key} className={EINGABE} placeholder="zuhause"
            onChange={(e) => setz("key", e.target.value)} />
        </Feld>
        <Feld label={tr("standorte.name")}>
          <input value={form.name} className={EINGABE} placeholder="Zuhause"
            onChange={(e) => setz("name", e.target.value)} />
        </Feld>
        <Feld label={tr("standorte.koordinaten")} hinweis={tr("standorte.koordinaten_hinweis")}>
          <div className="flex gap-2">
            <input type="number" step="any" value={form.lat} className={EINGABE}
              placeholder="50.0825" onChange={(e) => setz("lat", Number(e.target.value))} />
            <input type="number" step="any" value={form.lon} className={EINGABE}
              placeholder="10.5663" onChange={(e) => setz("lon", Number(e.target.value))} />
          </div>
        </Feld>
        <Feld label={tr("standorte.radius")} hinweis={tr("standorte.radius_hinweis")}>
          <input type="number" min={10} value={form.radius_m} className={EINGABE}
            onChange={(e) => setz("radius_m", Number(e.target.value))} />
        </Feld>
        <Feld label={tr("standorte.meldet")} hinweis={tr("standorte.meldet_hinweis")}>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={form.notify}
              onChange={(e) => setz("notify", e.target.checked)} />
            {tr("standorte.meldet_an")}
          </label>
        </Feld>
      </div>
    </Dialog>
  );
}
