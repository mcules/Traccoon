import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";

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

const KATEGORIE: [string, string][] = [
  ["todo", "Offen"],
  ["in_progress", "In Arbeit"],
  ["done", "Abgeschlossen"],
];

const FELDTYP: [string, string][] = [
  ["text", "Text"],
  ["number", "Zahl"],
  ["date", "Datum"],
  ["boolean", "Ja/Nein"],
  ["select", "Auswahl"],
];

const HERKUNFT: Record<string, string> = {
  issue_type: "Vorgangsarten des Projekts",
  board_status: "Board-Spalten des Projekts",
  sprint: "Sprints des Boards",
  member: "Mitglieder des Projekts",
  location: "Standorte",
};

const BACKING_LABEL: Record<string, string> = {
  issue: "Tickets (Board, Sprints, KI-Lebenszyklus)",
  hardware_asset: "Hardware-Bestand (Beschaffungskette)",
  generic: "eigene Ablage",
};

const inp = "rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

/**
 * Artefakt-Register: was Traccoon verwaltet, welche Zustände es kennt — und welche Felder
 * es trägt.
 *
 * Drei Ebenen nach ALMEX-Vorbild: ein **Artefakt-Typ** ordnet (Vorgang, Gegenstand), ein
 * **Artefakt** ist die Sache selbst (Ticket, Hardware), und darunter hängen die **Felder**
 * mit ihrer **Werteliste**. Am Feld steht, ob ein einzelnes Exemplar einen oder mehrere
 * Werte daraus tragen darf.
 */
export default function ArtifactTypesPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [neu, setNeu] = useState({ key: "", name: "", icon: "📦" });

  const { data: typen } = useQuery({
    queryKey: ["artifact-types"], queryFn: () => api.get<Typ[]>("/artifact-types"),
  });

  const inv = () => {
    qc.invalidateQueries({ queryKey: ["artifact-types"] });
  };
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");
  const ok = () => { setErr(""); inv(); };

  const anlegen = useMutation({
    mutationFn: () => api.post("/artifact-types", neu),
    onSuccess: () => { setNeu({ key: "", name: "", icon: "📦" }); ok(); }, onError: fail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-types/${id}`), onSuccess: ok, onError: fail,
  });


  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        Ein <b>{tr("artifact_types_panel.artefakt")}</b> ist zunächst etwas Undefiniertes — seine Bedeutung bekommt es
        erst durch seine <b>{tr("artifact_types_panel.felder")}</b>. Ticket und Hardware sind deshalb nichts Besonderes,
        sondern Artefakte mit einem ausgelieferten Satz fester Felder; auch der Zustand ist
        nur eines davon (<code>status</code>). Hier stehen die Felder, die <i>überall</i>
        gelten — eigene Ergänzungen pflegt jedes Projekt in seinen Einstellungen.
      </p>
      {err && <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-sm text-red-300">{err}</div>}

      <div className="space-y-3">
        {typen?.map((typ) => (
          <ArtefaktKarte key={typ.id} t={typ} onFail={fail} onOk={ok}
                         onDelete={() => loeschen.mutate(typ.id)} />
        ))}
      </div>

      <div>
        <div className="rounded-lg border border-line bg-card p-4">
          <div className="mb-2 text-sm font-medium">{tr("artifact_types_panel.eigenes_artefakt_anlegen")}</div>
          <div className="flex flex-wrap items-center gap-2">
            <input value={neu.icon} onChange={(e) => setNeu({ ...neu, icon: e.target.value })}
              className={`w-14 text-center ${inp}`} />
            <input value={neu.key} onChange={(e) => setNeu({ ...neu, key: e.target.value })}
              placeholder={tr("artifact_types_panel.schluessel_vertrag")} className={`w-36 font-mono ${inp}`} />
            <input value={neu.name} onChange={(e) => setNeu({ ...neu, name: e.target.value })}
              placeholder={tr("artifact_types_panel.name_vertrag")} className={`flex-1 ${inp}`} />
            <button onClick={() => neu.key.trim() && neu.name.trim() && anlegen.mutate()}
              className="rounded bg-brand px-3 py-1.5 text-sm text-white">{tr("artifact_types_panel.anlegen")}</button>
          </div>
          <p className="mt-2 text-[10px] text-muted">
            Bekommt eine eigene Ablage und beliebige Zustände. Board, Sprints und der
            KI-Lebenszyklus bleiben den Tickets vorbehalten.
          </p>
        </div>
      </div>
    </div>
  );
}

// ── Ein Artefakt mit Zuständen und Feldern ───────────────────────────────────

function ArtefaktKarte({ t: typ, onFail, onOk, onDelete }: {
  t: Typ; onFail: (e: unknown) => void; onOk: () => void; onDelete: () => void;
}) {
  const [zeigeFelder, setZeigeFelder] = useState(false);
  return (
    <div className="rounded-lg border border-line bg-card p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-lg">{typ.icon}</span>
        <span className="font-medium">{typ.name}</span>
        <span className="font-mono text-xs text-muted">{typ.key}</span>
        {typ.builtin && (
          <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">eingebaut</span>
        )}
        {typ.fields.length > 0 && (
          <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">
            {typ.fields.length} Feld{typ.fields.length === 1 ? "" : "er"}
          </span>
        )}
        <div className="flex-1" />
        <button onClick={() => setZeigeFelder(!zeigeFelder)}
          className="rounded border border-line px-2 py-0.5 text-xs hover:border-brand">
          {zeigeFelder ? "Felder ausblenden" : "Felder"}
        </button>
        {!typ.builtin && (
          <button onClick={() => confirm(`Artefakt „${typ.name}“ löschen?`) && onDelete()}
            className="rounded border border-line px-2 py-0.5 text-xs hover:border-red-400">
            Löschen
          </button>
        )}
      </div>
      <div className="mb-3 text-xs text-muted">
        {typ.description} · Daten: {BACKING_LABEL[typ.backing] || typ.backing}
      </div>


      <Felder t={typ} onFail={onFail} onOk={onOk} offen={zeigeFelder} />
    </div>
  );
}

// ── Felder eines Artefakts samt Werteliste ───────────────────────────────────

function Felder({ t: typ, onFail, onOk, offen }: {
  t: Typ; onFail: (e: unknown) => void; onOk: () => void; offen: boolean;
}) {
  const [neu, setNeu] = useState({ key: "", label: "", kind: "text", multi: false });

  const anlegen = useMutation({
    mutationFn: () => api.post(`/artifact-types/${typ.id}/fields`, neu),
    onSuccess: () => { setNeu({ key: "", label: "", kind: "text", multi: false }); onOk(); },
    onError: onFail,
  });
  const aendern = useMutation({
    mutationFn: ({ id, ...rest }: { id: number } & Record<string, any>) =>
      api.put(`/artifact-fields/${id}`, rest),
    onSuccess: onOk, onError: onFail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-fields/${id}`), onSuccess: onOk, onError: onFail,
  });

  if (!offen) {
    return (
      <div className="mt-2 text-xs text-muted">
        {typ.fields.map((f) => f.label).join(" · ") || "Noch keine Felder."}
      </div>
    );
  }
  return (
    <div className="mt-3 border-typ border-line pt-3">
      <div className="mb-2 text-xs font-medium text-muted">{tr("artifact_types_panel.felder")}</div>

      <div className="space-y-2">
        {typ.fields.map((f) => (
          <div key={f.id} className={`rounded border border-line px-2 py-1.5 ${f.enabled ? "bg-surface" : "bg-surface/40"}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="w-28 shrink-0 font-mono text-xs text-muted" title={f.key}>{f.key}</span>
              <input defaultValue={f.label}
                onBlur={(e) => e.target.value !== f.label && aendern.mutate({ id: f.id, label: e.target.value })}
                className={`flex-1 ${inp}`} />
              {f.builtin ? (
                <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted"
                      title={`Eingebaut — schreibt in die Spalte ${f.source}. Schlüssel und Typ sind gesperrt.`}>
                  {FELDTYP.find(([k]) => k === f.kind)?.[1]} · eingebaut
                </span>
              ) : (
                <select defaultValue={f.kind}
                  onChange={(e) => aendern.mutate({ id: f.id, kind: e.target.value })} className={inp}>
                  {FELDTYP.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              )}
              <label className="flex items-center gap-1 text-xs text-muted"
                     title={tr("artifact_types_panel.darf_ein_exemplar_mehrere_werte_gleichze")}>
                <input type="checkbox" checked={f.multi} disabled={f.builtin}
                  onChange={(e) => aendern.mutate({ id: f.id, multi: e.target.checked })} />
                Mehrfachauswahl
              </label>
              <label className="flex items-center gap-1 text-xs text-muted">
                <input type="checkbox" checked={f.required}
                  onChange={(e) => aendern.mutate({ id: f.id, required: e.target.checked })} />
                Pflicht
              </label>
              <label className="flex items-center gap-1 text-xs text-muted" title={tr("artifact_types_panel.abgeschaltete_felder_werden_nicht_mehr_a")}>
                <input type="checkbox" checked={f.enabled}
                  onChange={(e) => aendern.mutate({ id: f.id, enabled: e.target.checked })} />
                aktiv
              </label>
              {!f.builtin && (
                <button onClick={() => confirm(`Feld „${f.label}“ löschen?`) && loeschen.mutate(f.id)}
                  className="rounded border border-line px-1.5 py-0.5 text-xs hover:border-red-400">
                  ✕
                </button>
              )}
            </div>
            {f.options_source ? (
              <div className="mt-1 pl-28 text-[10px] text-muted">
                Werte kommen aus dem Projekt ({HERKUNFT[f.options_source] || f.options_source})
                — hier gibt es deshalb nichts zu pflegen.
              </div>
            ) : f.kind === "select" ? (
              <Werteliste feld={f} onFail={onFail} onOk={onOk} />
            ) : null}
          </div>
        ))}
        {typ.fields.length === 0 && (
          <div className="text-xs text-muted">{tr("artifact_types_panel.noch_keine_felder")}</div>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input value={neu.key} onChange={(e) => setNeu({ ...neu, key: e.target.value })}
          placeholder={tr("artifact_types_panel.schluessel_komponente")} className={`w-40 font-mono ${inp}`} />
        <input value={neu.label} onChange={(e) => setNeu({ ...neu, label: e.target.value })}
          placeholder={tr("artifact_types_panel.bezeichnung_komponente")} className={`flex-1 ${inp}`} />
        <select value={neu.kind} onChange={(e) => setNeu({ ...neu, kind: e.target.value })} className={inp}>
          {FELDTYP.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <label className="flex items-center gap-1 text-xs text-muted">
          <input type="checkbox" checked={neu.multi}
            onChange={(e) => setNeu({ ...neu, multi: e.target.checked })} />
          Mehrfachauswahl
        </label>
        <button onClick={() => neu.key.trim() && neu.label.trim() && anlegen.mutate()}
          className="rounded border border-line px-2 py-1 text-xs hover:border-brand">
          Feld anlegen
        </button>
      </div>
    </div>
  );
}

function Werteliste({ feld, onFail, onOk }: {
  feld: Feld; onFail: (e: unknown) => void; onOk: () => void;
}) {
  const [wert, setWert] = useState("");
  // Beim Zustands-Feld tragen die Werte zusätzlich Board-Kategorie und „wartet"; und
  // gelöscht wird dort nicht, weil die Schlüssel echten Datenbank-Werten entsprechen.
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
    <div className="mt-1.5 flex flex-wrap items-center gap-1.5 pl-28">
      <span className="text-[10px] text-muted">{tr("artifact_types_panel.werte")}</span>
      {feld.options.map((o) => (
        <span key={o.id}
          className={`flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ${
            o.enabled ? "bg-brand/15 text-ink" : "bg-surface text-muted line-through"}`}>
          {o.label || o.value}
          {istStatus && (
            <>
              <select value={o.category || "in_progress"}
                onChange={(e) => aendern.mutate({ id: o.id, category: e.target.value })}
                title={tr("artifact_types_panel.board_kategorie")} className="bg-transparent text-[10px] text-muted">
                {KATEGORIE.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
              <button onClick={() => aendern.mutate({ id: o.id, waiting: !o.waiting })}
                title={o.waiting ? "Wartet auf einen Menschen" : "Läuft von allein"}
                className={o.waiting ? "text-amber-300" : "text-muted hover:text-ink"}>
                {o.waiting ? "⏸" : "▷"}
              </button>
            </>
          )}
          <button onClick={() => aendern.mutate({ id: o.id, enabled: !o.enabled })}
            title={o.enabled ? "Nicht mehr anbieten (bleibt an vorhandenen Artefakten)" : "Wieder anbieten"}
            className="text-muted hover:text-ink">{o.enabled ? "○" : "●"}</button>
          {!istStatus && (
            <button onClick={() => loeschen.mutate(o.id)} title={tr("artifact_types_panel.loeschen")}
              className="text-muted hover:text-red-300">✕</button>
          )}
        </span>
      ))}
      <input value={wert} onChange={(e) => setWert(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && wert.trim() && anlegen.mutate()}
        placeholder={tr("artifact_types_panel.wert_enter")} className={`w-32 text-xs ${inp}`} />
    </div>
  );
}
