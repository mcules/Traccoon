import type { Issue } from "../api";
import { tr } from "../i18n";

/** One key per hold reason (the server's `hold_reason` values). Translation happens in
 *  waitInfo(): a table at module level would freeze the language of the first call. */
export const HOLD_LABEL: Record<string, string> = {
  plan_review: "lifecycle.grund_plan_review", plan_split: "lifecycle.grund_plan_split",
  question: "lifecycle.grund_question",
  review: "lifecycle.grund_review", permission: "lifecycle.grund_permission",
  merge: "lifecycle.grund_merge", verify: "lifecycle.grund_verify",
  incomplete: "lifecycle.grund_incomplete", stuck: "wait.steckt_fest", cap: "wait.limit",
  interrupted: "wait.gestoppt",
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
  error: { icon: "⚠️", title: "wait.fehler_aufgetreten" },
  question: { icon: "❓", title: "wait.rueckfrage_an_dich" },
  external: { icon: "⏳", title: "wait.wartet_extern" },
};

const translated = (m: { icon: string; title: string }) => ({ icon: m.icon, title: tr(m.title) });

/** Icon and reason for a waiting or blocked ticket, otherwise null. */
export function waitInfo(issue: Pick<Issue, "agent_status" | "hold_reason">): WaitInfo | null {
  if (issue.agent_status === "failed") {
    return { kind: "error", ...translated(KIND_META.error), label: tr("common.fehler") };
  }
  const reason = issue.hold_reason;
  if (!reason) return null;
  const label = HOLD_LABEL[reason] ? tr(HOLD_LABEL[reason]) : reason;
  if (ERROR_REASONS.has(reason)) return { kind: "error", ...translated(KIND_META.error), label };
  if (QUESTION_REASONS.has(reason)) return { kind: "question", ...translated(KIND_META.question), label };
  return { kind: "external", ...translated(KIND_META.external), label };
}
