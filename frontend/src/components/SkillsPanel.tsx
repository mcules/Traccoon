import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import {
  Aktionen, Bereich, Dialog, DialogFuss, EINGABE, Feld, Fehlerzeile, ICON, IconKnopf, Liste,
  ListeLeer, ListenZeile, LoeschDialog, KNOPF } from "./ui";

/**
 * Skills: reusable prompt blocks, versioned.
 *
 * Saving always writes a NEW version, which is why editing an existing skill is not a
 * correction but a successor. The dialog says so instead of leaving it to be discovered
 * after the fact.
 */
export default function SkillsPanel() {
  const qc = useQueryClient();
  const { data: skills } = useQuery({ queryKey: ["skills"], queryFn: () => api.get<any[]>("/skills") });
  const [dialog, setDialog] = useState<any | null>(null);     // {} = neuer Skill
  const [loeschSkill, setLoeschSkill] = useState<any | null>(null);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["skills"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));

  const save = useMutation({
    mutationFn: (f: { name: string; body: string; autostart: boolean }) =>
      api.post("/skills", f),        // Key leitet das Backend ab
    onSuccess: () => { setDialog(null); setErr(""); inv(); }, onError: fail,
  });
  const del = useMutation({
    mutationFn: (id: number) => api.del(`/skills/${id}`),
    onSuccess: () => { setLoeschSkill(null); inv(); }, onError: fail });

  return (
    <Bereich hinweis={tr("skills_panel.wiederverwendbare_prompt_bausteine_versi")}>
      <Fehlerzeile text={err} />
      <Liste className="mb-4">
        {skills?.map((s) => (
          <ListenZeile key={s.id}>
            <div className="flex items-center gap-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2">
              <span className="font-mono">{s.key}</span>
              <span className="text-muted">v{s.version}</span>
              <span>{s.name}</span>
              {s.autostart && <span className="rounded bg-surface px-1 text-xs">auto</span>}
            </div>
            <Aktionen>
              <IconKnopf icon={ICON.bearbeiten} titel={tr("skills_panel.neue_version")}
                onClick={() => { setErr(""); setDialog(s); }} />
              <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={() => setLoeschSkill(s)} />
            </Aktionen>
            </div>
          </ListenZeile>
        ))}
        {skills?.length === 0 && <ListeLeer>{tr("skills_panel.keine_skills")}</ListeLeer>}
      </Liste>
      <button onClick={() => { setErr(""); setDialog({}); }}
        className={KNOPF.haupt}>
        {ICON.neu} {tr("skills_panel.skill_anlegen")}
      </button>

      {dialog && (
        <SkillDialog skill={dialog.id ? dialog : null} fehler={err} laeuft={save.isPending}
          onClose={() => { setDialog(null); setErr(""); }}
          onSpeichern={(f) => save.mutate(f)} />
      )}
      {loeschSkill && (
        <LoeschDialog was={loeschSkill.name} laeuft={del.isPending}
          onClose={() => setLoeschSkill(null)} onLoeschen={() => del.mutate(loeschSkill.id)} />
      )}
    </Bereich>
  );
}

function SkillDialog({ skill, fehler, laeuft, onClose, onSpeichern }: {
  skill: any | null; fehler: string; laeuft: boolean;
  onClose: () => void;
  onSpeichern: (f: { name: string; body: string; autostart: boolean }) => void;
}) {
  const [name, setName] = useState(skill?.name || "");
  const [body, setBody] = useState(skill?.body || "");
  const [autostart, setAutostart] = useState(!!skill?.autostart);
  // Preview of the derived key
  const keyVorschau = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

  return (
    <Dialog breit titel={skill ? tr("skills_panel.neue_version") : tr("skills_panel.skill_anlegen")} onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} deaktiviert={!name.trim() || !body.trim()} laeuft={laeuft}
        onSpeichern={() => onSpeichern({ name: name.trim(), body, autostart })}
        speichernText={skill ? tr("skills_panel.version_speichern") : tr("common.anlegen")} />}>
      <Fehlerzeile text={fehler} />
      <div className="space-y-3">
        <Feld label={tr("skills_panel.name_z_b_test_driven_development")}
          hinweis={keyVorschau ? tr("skills_panel.key_automatisch", { key: keyVorschau }) : undefined}>
          <input value={name} autoFocus onChange={(e) => setName(e.target.value)} className={EINGABE} />
        </Feld>
        <Feld label={tr("skills_panel.skill_text_markdown")}>
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={12}
            className={`${EINGABE} font-mono text-xs`} />
        </Feld>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={autostart} onChange={(e) => setAutostart(e.target.checked)} />
          {tr("skills_panel.auto")}
        </label>
        {skill && <p className="text-xs text-muted">{tr("skills_panel.version_hinweis", { version: skill.version })}</p>}
      </div>
    </Dialog>
  );
}
