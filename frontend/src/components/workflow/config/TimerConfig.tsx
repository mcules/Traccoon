import type { NodeConfig } from "../types";
import { tr } from "../../../i18n";

/** How long is waited: a duration from now or a fixed point in time. */
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
            value={(config.duration as number) ?? ""}
            onChange={(e) => set({ duration: e.target.value ? Number(e.target.value) : undefined })}
            className={`mt-1 ${inp}`}
          />
        </label>
        <label className="flex-1 text-xs font-medium text-muted">
          Einheit
          <select
            value={(config.unit as string) || "m"}
            onChange={(e) => set({ unit: e.target.value })}
            className={`mt-1 ${inp}`}
          >
            <option value="s">{tr("timer_config.seconds")}</option>
            <option value="m">{tr("timer_config.minutes")}</option>
            <option value="h">{tr("timer_config.hours")}</option>
            <option value="t">{tr("timer_config.days")}</option>
          </select>
        </label>
      </div>

      <label className="block text-xs font-medium text-muted">
        {tr("timer.until_point_time")}
        <input
          value={(config.to as string) || ""}
          onChange={(e) => set({ to: e.target.value.trim() })}
          placeholder="2026-08-19T08:00:00+02:00 oder {{ jetzt | plus_zeit:1,&quot;t&quot; }}"
          className={`mt-1 font-mono ${inp}`}
        />
        <span className="mt-1 block text-[11px] text-muted">
          {tr("timer.templates_allowed_time_passed")}
        </span>
      </label>
    </div>
  );
}
