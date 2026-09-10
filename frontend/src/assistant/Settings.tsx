import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { toast } from "../toast";
import { assistant, type Choices, type Session } from "./api";

/**
 * What this conversation runs on: model, thinking depth, writing speed.
 *
 * Per conversation and not per person, because the reason to reach for a deeper
 * level or a faster model is the subject at hand — it differs between "sort this
 * post" and "think this through with me". A change acts from the next message
 * on; what has been answered stays answered the way it was.
 *
 * Nothing here is offered that the model cannot do. A thinking level a model
 * does not know is a 400 from the provider in the middle of somebody's sentence,
 * so the levels come from the same table the server checks against, and the
 * speed switch is only live where the model has it and the agent allows it.
 * Choosing a model that takes neither clears both rather than refusing: the
 * change that was asked for is the model.
 */
export default function AssistantSettings(
  { session, onChanged }: { session: Session; onChanged: (s: Session) => void },
) {
  const [choices, setChoices] = useState<Choices | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    assistant.choices(session.id)
      .then((c) => { if (alive) setChoices(c); })
      .catch(() => { if (alive) setChoices(null); });
    return () => { alive = false; };
  }, [session.id]);

  const save = async (data: Partial<{ model: string; effort: string; fast: boolean }>) => {
    setBusy(true);
    try {
      const s = await assistant.patch(session.id, data);
      onChanged(s);
      // The answer decides, not the click: the server clears a level or the speed
      // when the new model does not have them, and the picker has to show that.
      setChoices(await assistant.choices(session.id));
    } catch (e: any) {
      toast(e?.message || tr("common.error"), "error");
    } finally {
      setBusy(false);
    }
  };

  if (!choices) return null;

  const current = choices.models.find((m) => m.model === (session.model || choices.agent_model));
  const levels = current?.effort_levels ?? [];
  const mayFast = Boolean(current?.fast) && choices.agent_may_fast;
  const asAgent = choices.models.find((m) => m.model === choices.agent_model);

  const SELECT = `min-w-0 rounded border border-line bg-surface px-1.5 py-0.5 text-xs
                  text-ink disabled:opacity-40`;

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line
                    px-2 py-1 text-xs text-muted">
      <label className="flex min-w-0 items-center gap-1">
        <span className="shrink-0">{tr("assistant.model")}</span>
        <select className={SELECT} value={session.model} disabled={busy}
          onChange={(e) => void save({ model: e.target.value })}>
          {/* The empty entry says which model it means. "As the agent" without a
              name is a setting nobody can check. */}
          <option value="">
            {tr("assistant.as_agent", {
              m: asAgent?.display_name || choices.agent_model || "—",
            })}
          </option>
          {choices.models.map((m) => (
            <option key={m.model} value={m.model}>{m.display_name}</option>
          ))}
        </select>
      </label>

      <label className="flex min-w-0 items-center gap-1">
        <span className="shrink-0">{tr("assistant.effort")}</span>
        <select className={SELECT} value={session.effort} disabled={busy || !levels.length}
          title={levels.length ? "" : tr("assistant.effort_none")}
          onChange={(e) => void save({ effort: e.target.value })}>
          <option value="">
            {tr("assistant.as_agent", { m: choices.agent_effort || tr("assistant.effort_high") })}
          </option>
          {levels.map((l) => (
            <option key={l} value={l}>{tr(`assistant.effort_${l}`)}</option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-1"
        title={mayFast ? tr("assistant.fast_hint")
          : choices.agent_may_fast ? tr("assistant.fast_model_no") : tr("assistant.fast_agent_no")}>
        <input type="checkbox" checked={session.fast} disabled={busy || !mayFast}
          onChange={(e) => void save({ fast: e.target.checked })} />
        <span className={mayFast ? "" : "opacity-40"}>{tr("assistant.fast")}</span>
      </label>
    </div>
  );
}
