import type { AssigneeSpec } from "./types";
import { tr } from "../../i18n";
import type { MemberLite } from "../../api";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  maintainer: "Maintainer",
  member: "Mitglied",
  viewer: "Betrachter",
};

/** Short label of an AssigneeSpec (for the node preview and the config). */
export function assigneeLabel(spec: AssigneeSpec | undefined, members?: MemberLite[]): string {
  if (!spec) return "—";
  switch (spec.mode) {
    case "user": {
      const m = members?.find((x) => x.user_id === spec.user_id);
      return m ? m.display_name || m.username : spec.user_id ? `Nutzer #${spec.user_id}` : "Nutzer?";
    }
    case "role":
      return `Rolle: ${ROLE_LABEL[spec.role || ""] || spec.role || "?"}`;
    case "context":
      return `Kontext: ${spec.context_key || "?"}`;
    case "reporter":
      return "Melder";
    default:
      return "—";
  }
}

/** Combined selection widget for an AssigneeSpec. */
export function AssigneeEditor({
  value,
  onChange,
  members,
}: {
  value: AssigneeSpec | undefined;
  onChange: (v: AssigneeSpec) => void;
  members: MemberLite[];
}) {
  const spec: AssigneeSpec = value || { mode: "role", role: "member" };
  const inp = "rounded border border-line bg-surface px-2 py-1 text-sm text-ink";
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        value={spec.mode}
        onChange={(e) => onChange({ mode: e.target.value as AssigneeSpec["mode"] })}
        className={inp}
      >
        <option value="user">{tr("assignee.bestimmter_nutzer")}</option>
        <option value="role">{tr("assignee.projektrolle")}</option>
        <option value="context">{tr("assignee.aus_kontext")}</option>
        <option value="reporter">{tr("assignee.melder")}</option>
      </select>
      {spec.mode === "user" && (
        <select
          value={spec.user_id ?? ""}
          onChange={(e) => onChange({ mode: "user", user_id: Number(e.target.value) })}
          className={inp}
        >
          <option value="">{tr("action_params.waehlen")}</option>
          {members.map((m) => (
            <option key={m.user_id} value={m.user_id}>
              {m.display_name || m.username}
            </option>
          ))}
        </select>
      )}
      {spec.mode === "role" && (
        <select
          value={spec.role || "member"}
          onChange={(e) => onChange({ mode: "role", role: e.target.value })}
          className={inp}
        >
          {Object.entries(ROLE_LABEL).map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
      )}
      {spec.mode === "context" && (
        <input
          value={spec.context_key || ""}
          onChange={(e) => onChange({ mode: "context", context_key: e.target.value })}
          placeholder={tr("assignee.kontext_schluessel")}
          className={inp}
        />
      )}
    </div>
  );
}
