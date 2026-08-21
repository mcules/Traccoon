import { useQuery } from "@tanstack/react-query";
import { tr } from "../../i18n";
import { workflowApi, type WfDiff, type WfDiffNode } from "../../api";
import { Dialog, Errorrow } from "../ui";

/**
 * What has changed between two versions.
 *
 * Deliberately no text comparison over the JSON: a slipped bracket does not answer the question
 * "what does the flow do differently now". The comparison happens by nodes and edges, and a
 * changed node says WHICH of its settings is different — that is the line one is looking for.
 * The arrangement stays out of it; it is not worth a version and therefore also
 * kein Unterschied.
 */
export default function VersionsDiff({ defId, versionId, against, title: title, onClose }: {
  defId: number; versionId: number; against?: number; title: string; onClose: () => void;
}) {
  const { data, error, isLoading } = useQuery({
    queryKey: ["wf-diff", defId, versionId, against ?? null],
    queryFn: () => workflowApi.diff(defId, versionId, against),
  });

  return (
    <Dialog wide title={title} onClose={onClose}>
      {isLoading && <p className="text-sm text-muted">{tr("common.loading")}</p>}
      <Errorrow text={error ? String((error as Error).message || error) : ""} />
      {data && <DiffContent d={data} />}
    </Dialog>
  );
}

function DiffContent({ d }: { d: WfDiff }) {
  if (d.identical) {
    return (
      <p className="text-sm text-muted">
        {tr("diff.both_versions_do_same")}
      </p>
    );
  }
  return (
    <div className="space-y-4 text-sm">
      <p className="text-xs text-muted">
        {d.from_version === null
          ? tr("diff.v_first_version", { to: d.to_version })
          : tr("diff.comparing_v_v", { from: d.from_version, to: d.to_version })}
      </p>

      <Group title={tr("diff.new_steps")} entries={d.nodes_added} color="text-green-400" />
      <Group title={tr("diff.removed_steps")} entries={d.nodes_removed} color="text-red-400" />

      {d.nodes_changed.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">
            {tr("diff.changed_steps")}
          </div>
          <div className="space-y-2">
            {d.nodes_changed.map((k) => (
              <div key={k.id} className="rounded border border-line bg-surface p-2">
                <div className="mb-1 flex flex-wrap items-baseline gap-2">
                  <span className="font-medium text-ink">{k.label || k.id}</span>
                  <span className="font-mono text-xs text-muted">{k.id}</span>
                </div>
                <div className="space-y-1.5">
                  {k.fields.map((f) => (
                    <div key={f.field}>
                      <div className="font-mono text-[11px] text-muted">{f.field}</div>
                      {/* Two rows instead of one: with long values one otherwise hunts for the
                          place where they diverge, in the middle of the prose. */}
                      <div className="mt-0.5 break-all rounded bg-red-500/10 px-1.5 py-0.5 text-[11px] text-red-300">
                        − {f.before || "—"}
                      </div>
                      <div className="mt-0.5 break-all rounded bg-green-500/10 px-1.5 py-0.5 text-[11px] text-green-300">
                        + {f.after || "—"}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <Edges title={tr("diff.new_connections")} texts={d.edges_added} color="text-green-400" />
      <Edges title={tr("diff.removed_connections")} texts={d.edges_removed} color="text-red-400" />
    </div>
  );
}

function Group({ title: title, entries: entries, color }: {
  title: string; entries: WfDiffNode[]; color: string;
}) {
  if (entries.length === 0) return null;
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">{title}</div>
      <ul className="space-y-0.5">
        {entries.map((k) => (
          <li key={k.id} className={`text-sm ${color}`}>
            {k.label || k.id} <span className="font-mono text-xs opacity-70">{k.id}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Edges({ title: title, texts, color }: { title: string; texts: string[]; color: string }) {
  if (texts.length === 0) return null;
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">{title}</div>
      <ul className="space-y-0.5">
        {texts.map((t) => <li key={t} className={`font-mono text-xs ${color}`}>{t}</li>)}
      </ul>
    </div>
  );
}
