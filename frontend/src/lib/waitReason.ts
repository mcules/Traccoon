import type { Issue } from "../api";
import { tr } from "../i18n";

/** One key per hold reason (the server's `hold_reason` values). Translation happens in
 *  waitInfo(): a table at module level would freeze the language of the first call. */
export const HOLD_LABEL: Record<string, string> = {
  plan_review: "lifecycle.plan_approval", plan_split: "lifecycle.split",
  question: "lifecycle.question",
  review: "lifecycle.review_findings", permission: "lifecycle.permission",
  merge: "lifecycle.merge_conflict", verify: "lifecycle.verification",
  incomplete: "lifecycle.incomplete", stuck: "wait.stuck", cap: "wait.limit",
  interrupted: "wait.stopped",
};

export type WaitKind = "error" | "question" | "external";

/** Which hold reason and agent status belongs to which of the three waiting kinds. */
const QUESTION_REASONS = new Set(["question", "permission", "plan_review", "plan_split", "review"]);
const ERROR_REASONS = new Set(["stuck", "incomplete"]);
// merge, verify, cap, interrupted = waiting for something outside (git, test env, cost cap, system)

export interface WaitInfo {
  kind: WaitKind;
  icon: string;
  label: string;
  title: string;
}

const KIND_META: Record<WaitKind, { icon: string; title: string }> = {
  error: { icon: "⚠️", title: "wait.something_failed" },
  question: { icon: "❓", title: "wait.question" },
  external: { icon: "⏳", title: "wait.waiting_something_outside" },
};

const translated = (m: { icon: string; title: string }) => ({ icon: m.icon, title: tr(m.title) });

/** Icon and reason for a waiting or blocked ticket, otherwise null. */
export function waitInfo(issue: Pick<Issue, "agent_status" | "hold_reason">): WaitInfo | null {
  if (issue.agent_status === "failed") {
    return { kind: "error", ...translated(KIND_META.error), label: tr("common.error") };
  }
  const reason = issue.hold_reason;
  if (!reason) return null;
  const label = HOLD_LABEL[reason] ? tr(HOLD_LABEL[reason]) : reason;
  if (ERROR_REASONS.has(reason)) return { kind: "error", ...translated(KIND_META.error), label };
  if (QUESTION_REASONS.has(reason)) return { kind: "question", ...translated(KIND_META.question), label };
  return { kind: "external", ...translated(KIND_META.external), label };
}
