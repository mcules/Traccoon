import type { WorkflowInstance } from "./types";
import type { RuntimeState } from "./nodes/shared";

/** Derives a runtime state per node from steps plus tokens. */
export function runtimeStates(instance: WorkflowInstance): Record<string, RuntimeState> {
  const out: Record<string, RuntimeState> = {};

  // Neueste StepRun pro Knoten gewinnt.
  const latest: Record<string, (typeof instance.steps)[number]> = {};
  for (const s of instance.steps) {
    const prev = latest[s.node_id];
    if (!prev || s.entered_at >= prev.entered_at) latest[s.node_id] = s;
  }
  for (const [nodeId, s] of Object.entries(latest)) {
    if (s.status === "done" || s.status === "skipped") out[nodeId] = "done";
    else if (s.status === "failed") out[nodeId] = "failed";
    else if (s.status === "running" || s.status === "waiting") out[nodeId] = "active";
    else out[nodeId] = "pending";
  }

  // Active and waiting tokens mark the current node.
  for (const t of instance.tokens) {
    if ((t.state === "active" || t.state === "waiting") && out[t.node_id] !== "done") {
      out[t.node_id] = "active";
    }
  }
  return out;
}
