import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import {
  Aktionen, Dialog, DialogFuss, EINGABE, Feld as Formularfeld, Fehlerzeile, ICON, IconKnopf,
  LoeschDialog, Bereich, Liste, ListenZeile,
} from "./ui";

interface Wert {
  id: number; value: string; label: string; color: string; order: number; enabled: boolean;
  category: string; waiting: boolean;
}
interface Feld {
  id: number; key: string; label: string; kind: string; multi: boolean;
  required: boolean; order: number; description: string; enabled: boolean;
  source: string; options_source: string; builtin: boolean;
  options: Wert[]; dynamic_options: [string, string][];
}
interface Typ {
  id: number; key: string; name: string; plural: string; icon: string; color: string;
  backing: string; project_id: number | null;
  builtin: boolean; enabled: boolean; description: string;
  fields: Feld[];
}

// The lists hold keys, not texts: they come into being while the module loads, and a tr()
// at this place would freeze the language of the first call.
const KATEGORIE: [string, string][] = [
  ["todo", "artifact_types_panel.kat_todo"],
  ["in_progress", "artifact_types_panel.kat_in_progress"],
  ["done", "artifact_types_panel.kat_done"],
];

const FELDTYP: [string, string][] = [
  ["text", "artifact_types_panel.typ_text"],
  ["number", "artifact_types_panel.typ_number"],
  ["date", "artifact_types_panel.typ_date"],
  ["boolean", "artifact_types_panel.typ_boolean"],
  ["select", "artifact_types_panel.typ_select"],
];

const HERKUNFT: Record<string, string> = {
  issue_type: "artifact_types_panel.herkunft_issue_type",
  board_status: "artifact_types_panel.herkunft_board_status",
  sprint: "artifact_types_panel.herkunft_sprint",
  member: "artifact_types_panel.herkunft_member",
  location: "artifact_types_panel.herkunft_location",
};

const BACKING_LABEL: Record<string, string> = {
  issue: "artifact_types_panel.backing_issue",
  hardware_asset: "artifact_types_panel.backing_hardware",
  generic: "artifact_types_panel.backing_generic",
};

const inp = "rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

/**
 * Artifact register: what Traccoon manages, which states it knows and which fields it
 * carries.
 *
 * Three levels following the ALMEX example: an **artifact type** orders things (process,
 * object), an **artifact** is the thing itself (ticket, hardware), and below it hang the
 * **fields** with their **value list**. The field says whether a single unit may carry one
 * or several values from it.
 *
 * A field used to be edited in its own row: label as an input, type as a select, three
 * checkboxes and the value list behind them, all in a line that wrapped into a column of
 * unlabelled controls on anything narrower than a desktop. Editing happens in a dialog now,
 * and the row says what the field is.
 */
export default function ArtifactTypesPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [neuDialog, setNeuDialog] = useState(false);
  const [loeschTyp, setLoeschTyp] = useState<Typ | null>(null);

  const { data: typen } = useQuery({
    queryKey: ["artifact-types"], queryFn: () => api.get<Typ[]>("/artifact-types"),
  });

  const inv = () => { qc.invalidateQueries({ queryKey: ["artifact-types"] }); };
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));
  const ok = () => { setErr(""); inv(); };

  const anlegen = useMutation({
    mutationFn: (neu: { key: string; name: string; icon: string }) => api.post("/artifact-types", neu),
    onSuccess: () => { setNeuDialog(false); ok(); }, onError: fail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-types/${id}`),
    onSuccess: () => { setLoeschTyp(null); ok(); }, onError: fail,
  });

  // Ein Satz, ein Schlüssel: die Hervorhebungen fallen weg, weil sich Markup mitten im Satz
  // nicht mitübersetzen lässt (die Wortstellung ist anderswo eine andere).
  return (
    <Bereich hinweis={tr("artifact_types_panel.einleitung")}>
      <Fehlerzeile text={err} />

      <Liste>
        {typen?.map((typ) => (
          <ArtefaktKarte key={typ.id} t={typ} onFail={fail} onOk={ok}
                         onDelete={() => setLoeschTyp(typ)} />
        ))}
      </Liste>

      <button onClick={() => { setErr(""); setNeuDialog(true); }}
        className="rounded bg-brand px-3 py-1.5 text-sm text-white">
        {ICON.neu} {tr("artifact_types_panel.eigenes_artefakt_anlegen")}
      </button>

      {neuDialog && (
        <ArtefaktDialog fehler={err} laeuft={anlegen.isPending}
          onClose={() => { setNeuDialog(false); setErr(""); }}
          onSpeichern={(werte) => anlegen.mutate(werte)} />
      )}
      {loeschTyp && (
        <LoeschDialog was={loeschTyp.name} laeuft={loeschen.isPending}
          hinweis={tr("artifact_types_panel.loeschen_hinweis")}
          onClose={() => setLoeschTyp(null)} onLoeschen={() => loeschen.mutate(loeschTyp.id)} />
      )}
    </Bereich>
  );
}

function ArtefaktDialog({ fehler, laeuft, onClose, onSpeichern }: {
  fehler: string; laeuft: boolean; onClose: () => void;
  onSpeichern: (werte: { key: string; name: string; icon: string }) => void;
}) {
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [icon, setIcon] = useState("📦");

  return (
    <Dialog titel={tr("artifact_types_panel.eigenes_artefakt_anlegen")} onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} deaktiviert={!key.trim() || !name.trim()} laeuft={laeuft}
        speichernText={tr("common.anlegen")}
        onSpeichern={() => onSpeichern({ key: key.trim(), name: name.trim(), icon })} />}>
      <Fehlerzeile text={fehler} />
      <div className="space-y-3">
        <div className="flex gap-3">
          <div className="w-20">
            <Formularfeld label={tr("artifact_types_panel.icon")}>
              <input value={icon} onChange={(e) => setIcon(e.target.value)}
                className={`${EINGABE} text-center`} />
            </Formularfeld>
          </div>
          <div className="flex-1">
            <Formularfeld label={tr("artifact_types_panel.schluessel_vertrag")}>
              <input value={key} autoFocus onChange={(e) => setKey(e.target.value)}
                className={`${EINGABE} font-mono`} />
            </Formularfeld>
          </div>
        </div>
        <Formularfeld label={tr("artifact_types_panel.name_vertrag")}>
          <input value={name} onChange={(e) => setName(e.target.value)} className={EINGABE} />
        </Formularfeld>
        <p className="text-[11px] text-muted">{tr("artifact_types_panel.eigenes_artefakt_hinweis")}</p>
      </div>
    </Dialog>
  );
}

// ── One artifact with states and fields ──────────────────────────────────────

function ArtefaktKarte({ t: typ, onFail, onOk, onDelete }: {
  t: Typ; onFail: (e: unknown) => void; onOk: () => void; onDelete: () => void;
}) {
  const [zeigeFelder, setZeigeFelder] = useState(false);
  return (
    <ListenZeile>
      <div className="mb-2 flex items-center gap-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <span className="text-lg">{typ.icon}</span>
          <span className="font-medium">{typ.name}</span>
          <span className="font-mono text-xs text-muted">{typ.key}</span>
          {typ.builtin && (
            <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">{tr("artifact_types_panel.eingebaut")}</span>
          )}
          {typ.fields.length > 0 && (
            <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">
              {tr("artifact_types_panel.felder_anzahl", { anzahl: typ.fields.length })}
            </span>
          )}
        </div>
        <Aktionen>
          <button onClick={() => setZeigeFelder(!zeigeFelder)}
            className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink">
            {zeigeFelder ? tr("artifact_types_panel.felder_ausblenden") : tr("artifact_types_panel.felder")}
          </button>
          {!typ.builtin && (
            <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={onDelete} />
          )}
        </Aktionen>
      </div>
      <div className="mb-3 text-xs text-muted">
        {typ.description} · {tr("artifact_types_panel.daten")}: {BACKING_LABEL[typ.backing] ? tr(BACKING_LABEL[typ.backing]) : typ.backing}
      </div>

      <Felder t={typ} onFail={onFail} onOk={onOk} offen={zeigeFelder} />
    </ListenZeile>
  );
}

// ── Felder eines Artefakts samt Werteliste ───────────────────────────────────

function Felder({ t: typ, onFail, onOk, offen }: {
  t: Typ; onFail: (e: unknown) => void; onOk: () => void; offen: boolean;
}) {
  const [dialog, setDialog] = useState<Feld | {} | null>(null);    // {} = neues Feld
  const [loeschFeld, setLoeschFeld] = useState<Feld | null>(null);

  const anlegen = useMutation({
    mutationFn: (neu: { key: string; label: string; kind: string; multi: boolean }) =>
      api.post(`/artifact-types/${typ.id}/fields`, neu),
    onSuccess: () => { setDialog(null); onOk(); }, onError: onFail,
  });
  const aendern = useMutation({
    mutationFn: ({ id, ...rest }: { id: number } & Record<string, any>) =>
      api.put(`/artifact-fields/${id}`, rest),
    onSuccess: () => { setDialog(null); onOk(); }, onError: onFail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-fields/${id}`),
    onSuccess: () => { setLoeschFeld(null); onOk(); }, onError: onFail,
  });

  if (!offen) {
    return (
      <div className="mt-2 text-xs text-muted">
        {typ.fields.map((f) => f.label).join(" · ") || tr("artifact_types_panel.keine_felder")}
      </div>
    );
  }
  return (
    <div className="mt-3 border-t border-line pt-3">
      <div className="mb-2 text-xs font-medium text-muted">{tr("artifact_types_panel.felder")}</div>

      <div className="space-y-1">
        {typ.fields.map((f) => (
          <div key={f.id}
            className={`flex items-center gap-2 rounded border border-line px-2 py-1.5 ${f.enabled ? "bg-surface" : "bg-surface/40"}`}>
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
              <span className="font-mono text-xs text-muted" title={f.key}>{f.key}</span>
              <span className={f.enabled ? "" : "line-through"}>{f.label}</span>
              <span className="text-xs text-muted">
                {tr(FELDTYP.find(([k]) => k === f.kind)?.[1] || "")}
              </span>
              {f.multi && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("artifact_types_panel.mehrfach")}</span>}
              {f.required && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("artifact_types_panel.pflicht")}</span>}
              {f.builtin && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("artifact_types_panel.eingebaut")}</span>}
              {f.options_source && (
                <span className="text-xs text-muted">
                  {tr("artifact_types_panel.werte_aus_projekt")} ({HERKUNFT[f.options_source] ? tr(HERKUNFT[f.options_source]) : f.options_source})
                </span>
              )}
              {!f.options_source && f.kind === "select" && f.options.length > 0 && (
                <span className="truncate text-xs text-muted">
                  {f.options.filter((o) => o.enabled).map((o) => o.label || o.value).join(" · ")}
                </span>
              )}
            </div>
            <Aktionen>
              <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")} onClick={() => setDialog(f)} />
              {!f.builtin && (
                <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={() => setLoeschFeld(f)} />
              )}
            </Aktionen>
          </div>
        ))}
        {typ.fields.length === 0 && (
          <div className="text-xs text-muted">{tr("artifact_types_panel.noch_keine_felder")}</div>
        )}
      </div>

      <button onClick={() => setDialog({})}
        className="mt-2 rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink">
        {ICON.neu} {tr("artifact_types_panel.feld_anlegen")}
      </button>

      {dialog && (
        <FeldDialog feld={"id" in dialog ? (dialog as Feld) : null}
          onClose={() => setDialog(null)}
          onAnlegen={(neu) => anlegen.mutate(neu)}
          onAendern={(id, patch) => aendern.mutate({ id, ...patch })}
          onFail={onFail} onOk={onOk}
          laeuft={anlegen.isPending || aendern.isPending} />
      )}
      {loeschFeld && (
        <LoeschDialog was={loeschFeld.label} laeuft={loeschen.isPending}
          onClose={() => setLoeschFeld(null)} onLoeschen={() => loeschen.mutate(loeschFeld.id)} />
      )}
    </div>
  );
}

/**
 * One field, with its value list where it has one.
 *
 * A built-in field writes into a real column, so its key and its type are locked; only
 * label, requirement and visibility belong to whoever set it up.
 */
function FeldDialog({ feld, laeuft, onClose, onAnlegen, onAendern, onFail, onOk }: {
  feld: Feld | null;
  laeuft: boolean;
  onClose: () => void;
  onAnlegen: (neu: { key: string; label: string; kind: string; multi: boolean }) => void;
  onAendern: (id: number, patch: Record<string, any>) => void;
  onFail: (e: unknown) => void;
  onOk: () => void;
}) {
  const [key, setKey] = useState(feld?.key || "");
  const [label, setLabel] = useState(feld?.label || "");
  const [kind, setKind] = useState(feld?.kind || "text");
  const [multi, setMulti] = useState(!!feld?.multi);
  const [required, setRequired] = useState(!!feld?.required);
  const [enabled, setEnabled] = useState(feld ? feld.enabled : true);

  const speichern = () => {
    if (feld) onAendern(feld.id, { label, kind, multi, required, enabled });
    else onAnlegen({ key: key.trim(), label: label.trim(), kind, multi });
  };

  return (
    <Dialog titel={feld ? tr("artifact_types_panel.feld_bearbeiten") : tr("artifact_types_panel.feld_anlegen")}
      onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} laeuft={laeuft}
        deaktiviert={!label.trim() || (!feld && !key.trim())}
        speichernText={feld ? undefined : tr("common.anlegen")} onSpeichern={speichern} />}>
      <div className="space-y-3">
        <Formularfeld label={tr("artifact_types_panel.schluessel_komponente")}
          hinweis={feld?.builtin ? tr("artifact_types_panel.eingebaut_hinweis", { spalte: feld.source }) : undefined}>
          <input value={key} disabled={!!feld} autoFocus={!feld}
            onChange={(e) => setKey(e.target.value)} className={`${EINGABE} font-mono disabled:opacity-60`} />
        </Formularfeld>
        <Formularfeld label={tr("artifact_types_panel.bezeichnung_komponente")}>
          <input value={label} autoFocus={!!feld} onChange={(e) => setLabel(e.target.value)} className={EINGABE} />
        </Formularfeld>
        <Formularfeld label={tr("artifact_types_panel.typ")}>
          <select value={kind} disabled={!!feld?.builtin} onChange={(e) => setKind(e.target.value)}
            className={`${EINGABE} disabled:opacity-60`}>
            {FELDTYP.map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}
          </select>
        </Formularfeld>
        <div className="space-y-1.5">
          <label className="flex items-center gap-2 text-sm text-ink"
            title={tr("artifact_types_panel.darf_ein_exemplar_mehrere_werte_gleichze")}>
            <input type="checkbox" checked={multi} disabled={!!feld?.builtin}
              onChange={(e) => setMulti(e.target.checked)} />
            {tr("artifact_types_panel.mehrfachauswahl")}
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />
            {tr("artifact_types_panel.pflicht")}
          </label>
          {feld && (
            <label className="flex items-center gap-2 text-sm text-ink"
              title={tr("artifact_types_panel.abgeschaltete_felder_werden_nicht_mehr_a")}>
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              {tr("artifact_types_panel.aktiv")}
            </label>
          )}
        </div>

        {feld && feld.options_source && (
          <p className="text-xs text-muted">
            {tr("artifact_types_panel.werte_aus_projekt")} ({HERKUNFT[feld.options_source] ? tr(HERKUNFT[feld.options_source]) : feld.options_source})
            — {tr("artifact_types_panel.nichts_zu_pflegen")}
          </p>
        )}
        {feld && !feld.options_source && feld.kind === "select" && (
          <div className="border-t border-line pt-3">
            <div className="mb-1 text-xs font-medium text-muted">{tr("artifact_types_panel.werte")}</div>
            <Werteliste feld={feld} onFail={onFail} onOk={onOk} />
          </div>
        )}
      </div>
    </Dialog>
  );
}

function Werteliste({ feld, onFail, onOk }: {
  feld: Feld; onFail: (e: unknown) => void; onOk: () => void;
}) {
  const [wert, setWert] = useState("");
  // With the state field the values additionally carry a board category and "waiting"; and
  // nothing is deleted there, because the keys correspond to real database values.
  const istStatus = feld.key === "status";
  const anlegen = useMutation({
    mutationFn: () => api.post(`/artifact-fields/${feld.id}/options`, { value: wert.trim() }),
    onSuccess: () => { setWert(""); onOk(); }, onError: onFail,
  });
  const aendern = useMutation({
    mutationFn: ({ id, ...rest }: { id: number } & Record<string, any>) =>
      api.put(`/artifact-field-options/${id}`, rest),
    onSuccess: onOk, onError: onFail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-field-options/${id}`),
    onSuccess: onOk, onError: onFail,
  });

  return (
    <div className="space-y-1">
      {feld.options.map((o) => (
        <div key={o.id} className="flex items-center gap-2 rounded border border-line px-2 py-1 text-sm">
          <span className={o.enabled ? "flex-1" : "flex-1 text-muted line-through"}>{o.label || o.value}</span>
          {istStatus && (
            <>
              <select value={o.category || "in_progress"}
                onChange={(e) => aendern.mutate({ id: o.id, category: e.target.value })}
                title={tr("artifact_types_panel.board_kategorie")} className={`text-xs ${inp}`}>
                {KATEGORIE.map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}
              </select>
              <IconKnopf icon={o.waiting ? "⏸" : "▷"} aktiv={o.waiting}
                titel={tr(o.waiting ? "artifact_types_panel.wartet_auf_mensch" : "artifact_types_panel.laeuft_allein")}
                onClick={() => aendern.mutate({ id: o.id, waiting: !o.waiting })} />
            </>
          )}
          <IconKnopf icon={o.enabled ? "○" : "●"}
            titel={tr(o.enabled ? "artifact_types_panel.nicht_mehr_anbieten" : "artifact_types_panel.wieder_anbieten")}
            onClick={() => aendern.mutate({ id: o.id, enabled: !o.enabled })} />
          {!istStatus && (
            <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
              onClick={() => loeschen.mutate(o.id)} />
          )}
        </div>
      ))}
      <div className="flex items-center gap-2">
        <input value={wert} onChange={(e) => setWert(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && wert.trim() && anlegen.mutate()}
          placeholder={tr("artifact_types_panel.wert_enter")} className={`flex-1 text-sm ${inp}`} />
        <IconKnopf icon={ICON.neu} titel={tr("common.anlegen")} disabled={!wert.trim()}
          onClick={() => wert.trim() && anlegen.mutate()} />
      </div>
    </div>
  );
}
