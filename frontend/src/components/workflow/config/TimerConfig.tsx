import type { NodeConfig } from "../types";
import { tr } from "../../../i18n";

/** Wie lange gewartet wird — eine Dauer ab jetzt oder ein fester Zeitpunkt. */
export default function TimerConfig({
  config,
  onChange,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
}) {
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";
  const set = (p: Partial<NodeConfig>) => onChange({ ...config, ...p });

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <label className="flex-1 text-xs font-medium text-muted">
          Dauer
          <input
            type="number"
            min={0}
            value={(config.dauer as number) ?? ""}
            onChange={(e) => set({ dauer: e.target.value ? Number(e.target.value) : undefined })}
            className={`mt-1 ${inp}`}
          />
        </label>
        <label className="flex-1 text-xs font-medium text-muted">
          Einheit
          <select
            value={(config.einheit as string) || "m"}
            onChange={(e) => set({ einheit: e.target.value })}
            className={`mt-1 ${inp}`}
          >
            <option value="s">{tr("timer_config.sekunden")}</option>
            <option value="m">{tr("timer_config.minuten")}</option>
            <option value="h">{tr("timer_config.stunden")}</option>
            <option value="t">{tr("timer_config.tage")}</option>
          </select>
        </label>
      </div>

      <label className="block text-xs font-medium text-muted">
        {tr("timer.bis_zeitpunkt")}
        <input
          value={(config.bis as string) || ""}
          onChange={(e) => set({ bis: e.target.value.trim() })}
          placeholder="2026-08-19T08:00:00+02:00 oder {{ jetzt | plus_zeit:1,&quot;t&quot; }}"
          className={`mt-1 font-mono ${inp}`}
        />
        <span className="mt-1 block text-[11px] text-muted">
          {tr("timer.hinweis")}
        </span>
      </label>
    </div>
  );
}
