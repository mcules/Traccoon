import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import {
  Actions, Area, Dialog, DialogFoot, INPUT_VALUE, Field, Errorrow, ICON, IconButton, Listing,
  ListingEmpty, ListRow, DeleteDialog, BUTTON } from "./ui";

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
  const [deleteSkill, setDeleteSkill] = useState<any | null>(null);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["skills"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  const save = useMutation({
    mutationFn: (f: { name: string; body: string; autostart: boolean }) =>
      api.post("/skills", f),        // Key leitet das Backend ab
    onSuccess: () => { setDialog(null); setErr(""); inv(); }, onError: fail,
  });
  const del = useMutation({
    mutationFn: (id: number) => api.del(`/skills/${id}`),
    onSuccess: () => { setDeleteSkill(null); inv(); }, onError: fail });

  return (
    <Area hint={tr("skills_panel.reusable_prompt_building_blocks_versioned_age")}>
      <Errorrow text={err} />
      <Listing className="mb-4">
        {skills?.map((s) => (
          <ListRow key={s.id}>
            <div className="flex items-center gap-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2">
              <span className="font-mono">{s.key}</span>
              <span className="text-muted">v{s.version}</span>
              <span>{s.name}</span>
              {s.autostart && <span className="rounded bg-surface px-1 text-xs">auto</span>}
            </div>
            <Actions>
              <IconButton icon={ICON.edit} title={tr("skills_panel.new_version")}
                onClick={() => { setErr(""); setDialog(s); }} />
              <IconButton icon={ICON.remove} title={tr("common.delete")} danger onClick={() => setDeleteSkill(s)} />
            </Actions>
            </div>
          </ListRow>
        ))}
        {skills?.length === 0 && <ListingEmpty>{tr("skills_panel.no_skills")}</ListingEmpty>}
      </Listing>
      <button onClick={() => { setErr(""); setDialog({}); }}
        className={BUTTON.primary}>
        {ICON.fresh} {tr("skills_panel.new_skill")}
      </button>

      {dialog && (
        <SkillDialog skill={dialog.id ? dialog : null} error={err} runs={save.isPending}
          onClose={() => { setDialog(null); setErr(""); }}
          onSave={(f) => save.mutate(f)} />
      )}
      {deleteSkill && (
        <DeleteDialog was={deleteSkill.name} runs={del.isPending}
          onClose={() => setDeleteSkill(null)} onDelete={() => del.mutate(deleteSkill.id)} />
      )}
    </Area>
  );
}

function SkillDialog({ skill, error: error, runs: running, onClose, onSave }: {
  skill: any | null; error: string; runs: boolean;
  onClose: () => void;
  onSave: (f: { name: string; body: string; autostart: boolean }) => void;
}) {
  const [name, setName] = useState(skill?.name || "");
  const [body, setBody] = useState(skill?.body || "");
  const [autostart, setAutostart] = useState(!!skill?.autostart);
  // Preview of the derived key
  const keyPreview = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

  return (
    <Dialog wide title={skill ? tr("skills_panel.new_version") : tr("skills_panel.new_skill")} onClose={onClose}
      foot={<DialogFoot onCancel={onClose} disabled={!name.trim() || !body.trim()} runs={running}
        onSave={() => onSave({ name: name.trim(), body, autostart })}
        saveText={skill ? tr("skills_panel.save_version") : tr("common.create")} />}>
      <Errorrow text={error} />
      <div className="space-y-3">
        <Field label={tr("skills_panel.name_z_b_test_driven_development")}
          hint={keyPreview ? tr("skills_panel.key_key_derived", { key: keyPreview }) : undefined}>
          <input value={name} autoFocus onChange={(e) => setName(e.target.value)} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("skills_panel.skill_text_markdown")}>
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={12}
            className={`${INPUT_VALUE} font-mono text-xs`} />
        </Field>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={autostart} onChange={(e) => setAutostart(e.target.checked)} />
          {tr("skills_panel.auto")}
        </label>
        {skill && <p className="text-xs text-muted">{tr("skills_panel.saving_creates_version_version", { version: skill.version })}</p>}
      </div>
    </Dialog>
  );
}
