import { useQuery, useQueryClient } from "@tanstack/react-query";
import { tr } from "../i18n";
import { Link } from "react-router-dom";
import { api } from "../api";
import { KNOPF_TEXT } from "./ui";

type Step = { key: string; title: string; hint: string; done: boolean; required: boolean };
type Status = { steps: Step[]; ready: boolean; projects: number; dismissed: boolean };

/** Target place per step; otherwise nobody knows where to click. */
const ZIEL: Record<string, { to: string; label: string }> = {
  claude_token: { to: "/settings", label: "onboarding.ziel_tresor" },
  project: { to: "/", label: "onboarding.ziel_projekt_anlegen" },
  git: { to: "/", label: "onboarding.ziel_projekt_oeffnen" },
  verify: { to: "/", label: "onboarding.ziel_projekt_oeffnen" },
  telegram: { to: "/settings", label: "onboarding.ziel_einstellungen" },
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
            {tr(data.ready ? "onboarding.fast_fertig" : "onboarding.einrichtung")}
          </div>
          <p className="mt-0.5 text-xs text-muted">
            {tr(data.ready ? "onboarding.rest_optional" : "onboarding.pflicht_offen")}
          </p>
        </div>
        <button onClick={dismiss} className={KNOPF_TEXT.neben}>{tr("onboarding.ausblenden")}</button>
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
                {tr(ZIEL[s.key].label)} →
              </Link>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
