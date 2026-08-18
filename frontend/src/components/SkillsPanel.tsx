import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

export default function SkillsPanel() {
  const qc = useQueryClient();
  const { data: skills } = useQuery({ queryKey: ["skills"], queryFn: () => api.get<any[]>("/skills") });
  const [name, setName] = useState("");
  const [body, setBody] = useState(""); const [autostart, setAuto] = useState(false);
  const inv = () => qc.invalidateQueries({ queryKey: ["skills"] });
  const create = useMutation({
    mutationFn: () => api.post("/skills", { name, body, autostart }),  // Key leitet das Backend ab
    onSuccess: () => { setName(""); setBody(""); inv(); },
  });
  // Vorschau des abgeleiteten Keys
  const keyPreview = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  const del = useMutation({ mutationFn: (id: number) => api.del(`/skills/${id}`), onSuccess: inv });

  return (
    <div>
      <p className="mb-3 text-sm text-muted">{tr("skills_panel.wiederverwendbare_prompt_bausteine_versi")}</p>
      <div className="mb-4 space-y-2">
        {skills?.map((s) => (
          <div key={s.id} className="flex items-center gap-3 rounded border border-line bg-card p-2 text-sm">
            <span className="font-mono">{s.key}</span> <span className="text-muted">v{s.version}</span>
            <span>{s.name}</span>{s.autostart && <span className="rounded bg-surface px-1 text-xs">auto</span>}
            <div className="flex-1" />
            <button onClick={() => del.mutate(s.id)} className="text-muted hover:text-red-400">löschen</button>
          </div>
        ))}
        {skills?.length === 0 && <div className="text-xs text-muted">{tr("skills_panel.keine_skills")}</div>}
      </div>
      <div className="space-y-2 rounded-lg border border-line bg-card p-3">
        <div className="flex items-center gap-2">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={tr("skills_panel.name_z_b_test_driven_development")} className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
          <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={autostart} onChange={(e) => setAuto(e.target.checked)} />auto</label>
        </div>
        {keyPreview && <div className="text-xs text-muted">Key: <span className="font-mono">{keyPreview}</span> (automatisch)</div>}
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={3} placeholder={tr("skills_panel.skill_text_markdown")} className="w-full rounded border border-line bg-surface px-2 py-1.5 text-sm" />
        <button onClick={() => name && body && create.mutate()} className="rounded bg-brand px-3 py-1.5 text-sm text-white">
          Speichern (neue Version)</button>
      </div>
    </div>
  );
}
