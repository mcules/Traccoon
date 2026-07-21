import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";

type Step = { key: string; title: string; hint: string; done: boolean; required: boolean };
type Status = { steps: Step[]; ready: boolean; projects: number; dismissed: boolean };

/** Zielort je Schritt — sonst weiß niemand, wo er klicken soll. */
const ZIEL: Record<string, { to: string; label: string }> = {
  claude_token: { to: "/settings", label: "Zum Secret-Tresor" },
  project: { to: "/", label: "Projekt anlegen" },
  git: { to: "/", label: "Projekt öffnen" },
  verify: { to: "/", label: "Projekt öffnen" },
  telegram: { to: "/settings", label: "Zu den Einstellungen" },
};

export default function Onboarding() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["onboarding"], queryFn: () => api.get<Status>("/me/onboarding"),
    refetchInterval: 15000,
  });
  if (!data || data.dismissed) return null;

  const offenPflicht = data.steps.filter((s) => !s.done && s.required);
  const offenOptional = data.steps.filter((s) => !s.done && !s.required);
  if (!offenPflicht.length && !offenOptional.length) return null;

  const dismiss = async () => {
    await api.post("/me/onboarding/dismiss");
    qc.invalidateQueries({ queryKey: ["onboarding"] });
  };

  return (
    <div className="mb-5 rounded-lg border border-brand/40 bg-brand/5 p-4">
      <div className="mb-1 flex items-start gap-3">
        <div className="flex-1">
          <div className="font-medium">
            {data.ready ? "Fast fertig eingerichtet" : "Einrichtung — so laufen echte Agenten"}
          </div>
          <p className="mt-0.5 text-xs text-muted">
            {data.ready
              ? "Das Nötigste steht. Der Rest ist optional."
              : "Solange die Pflichtpunkte offen sind, bleibt jede Zuweisung an einen Agenten liegen."}
          </p>
        </div>
        <button onClick={dismiss} className="text-xs text-muted hover:text-ink">ausblenden</button>
      </div>

      <div className="mt-3 space-y-2">
        {data.steps.map((s) => (
          <div key={s.key} className="flex items-start gap-3 text-sm">
            <span className={s.done ? "text-green-400" : s.required ? "text-yellow-400" : "text-muted"}>
              {s.done ? "✓" : "○"}
            </span>
            <div className="flex-1">
              <span className={s.done ? "text-muted line-through" : "text-ink"}>{s.title}</span>
              {!s.required && !s.done && <span className="ml-1 text-xs text-muted">(optional)</span>}
              {!s.done && <div className="text-xs text-muted">{s.hint}</div>}
            </div>
            {!s.done && ZIEL[s.key] && (
              <Link to={ZIEL[s.key].to} className="whitespace-nowrap text-xs text-brand hover:underline">
                {ZIEL[s.key].label} →
              </Link>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
