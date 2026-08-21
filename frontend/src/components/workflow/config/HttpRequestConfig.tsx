import { useQuery } from "@tanstack/react-query";
import { tr } from "../../../i18n";
import { destinationApi } from "../../../api";
import { KeyValueEditor } from "../kv";

const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];

/**
 * Call of a stored destination: the base URL and the login come from there, while method,
 * path addition, query, headers and body stand here. All text fields understand
 * `{{path}}` from the process context.
 */
export default function HttpRequestConfig({
  params,
  onChange,
  projectId,
}: {
  params: Record<string, any>;
  onChange: (p: Record<string, any>) => void;
  projectId?: number;
}) {
  const { data: targets } = useQuery({
    queryKey: ["destinations-usable", projectId ?? null],
    queryFn: () => destinationApi.list(projectId, true),
  });
  const set = (k: string, v: any) => onChange({ ...params, [k]: v });
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";
  const chosen = targets?.find((d) => d.name === params.destination);

  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-muted">
        Ziel
        <input
          list="ziele"
          value={params.destination || ""}
          onChange={(e) => set("destination", e.target.value)}
          placeholder={tr("http_request_config.name_des_ziels")}
          className={`mt-1 ${inp}`}
        />
        <datalist id="ziele">
          {targets?.map((d) => (
            <option key={d.id} value={d.name}>
              {d.label || d.base_url}
            </option>
          ))}
        </datalist>
        <span className="mt-1 block text-[11px] text-muted">
          {chosen
            ? `${chosen.base_url} · ${chosen.auth_type === "none" ? tr("http_request.ohne_anmeldung") : chosen.auth_type}`
            : tr("http_request.ziele_pflegen")}
        </span>
      </label>

      <div className="grid grid-cols-3 gap-2">
        <label className="block text-xs font-medium text-muted">
          Methode
          <select value={params.method || "POST"} onChange={(e) => set("method", e.target.value)}
            className={`mt-1 ${inp}`}>
            {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label className="col-span-2 block text-xs font-medium text-muted">
          {tr("http_config.pfad")}
          <input value={params.path || ""} onChange={(e) => set("path", e.target.value)}
            placeholder="/api/v2/orders/{{created_ticket.key}}" className={`mt-1 ${inp}`} />
        </label>
      </div>

      <div>
        <div className="mb-1 text-xs font-medium text-muted">{tr("http_request_config.query_parameter_an_die_url")}</div>
        <KeyValueEditor value={params.query || {}} onChange={(q) => set("query", q)} />
      </div>

      <div>
        <div className="mb-1 text-xs font-medium text-muted">{tr("http_request_config.zusaetzliche_kopfzeilen")}</div>
        <KeyValueEditor value={params.headers || {}} onChange={(h) => set("headers", h)} />
      </div>

      <label className="block text-xs font-medium text-muted">
        {tr("http_request.body")}
        <textarea
          rows={4}
          value={typeof params.body === "string" ? params.body : JSON.stringify(params.body ?? "", null, 2)}
          onChange={(e) => {
            const raw = e.target.value;
            try {
              set("body", JSON.parse(raw));
            } catch {
              set("body", raw);
            }
          }}
          placeholder='{"ticket": "{{issue_key}}"}'
          className={`mt-1 ${inp} font-mono`}
        />
      </label>

      <div className="grid grid-cols-2 gap-2">
        <label className="block text-xs font-medium text-muted">
          {tr("http_request.ergebnis_unter")}
          <input value={params.context_key || ""} onChange={(e) => set("context_key", e.target.value)}
            placeholder="http" className={`mt-1 ${inp}`} />
        </label>
        <label className="mt-5 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!params.fail_on_error}
            onChange={(e) => set("fail_on_error", e.target.checked)} />
          Fehlerstatus = Schritt scheitert
        </label>
      </div>
      <p className="text-[11px] text-muted">
        Ohne den Haken läuft der Prozess weiter und kann selbst über
        <code className="mx-1 rounded bg-surface px-1">{"{{http.status_code}}"}</code>
        bzw. <code className="rounded bg-surface px-1">{"{{http.ok}}"}</code> verzweigen.
      </p>
    </div>
  );
}
