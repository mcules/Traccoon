import type { Issue } from "../api";

/** Lesbare Labels für HoldReason (Backend `hold_reason`-Werte). */
export const HOLD_LABEL: Record<string, string> = {
  plan_review: "Plan-Freigabe", plan_split: "Aufteilung", question: "Rückfrage",
  review: "Review", permission: "Berechtigung", merge: "Merge", verify: "Verifikation",
  incomplete: "unvollständig", stuck: "steckt fest", cap: "Limit", interrupted: "gestoppt",
};

export type WaitKind = "error" | "question" | "external";

/** Zuordnung der HoldReasons/agent_status zu einer der drei Warte-Kategorien. */
const QUESTION_REASONS = new Set(["question", "permission", "plan_review", "plan_split", "review"]);
const ERROR_REASONS = new Set(["stuck", "incomplete"]);
// merge, verify, cap, interrupted = warten auf etwas Externes (Git, Testenv, Kosten-Limit, System)

export interface WaitInfo {
  kind: WaitKind;
  icon: string;
  label: string;
  title: string;
}

const KIND_META: Record<WaitKind, { icon: string; title: string }> = {
  error: { icon: "⚠️", title: "Fehler aufgetreten" },
  question: { icon: "❓", title: "Rückfrage an dich" },
  external: { icon: "⏳", title: "Wartet auf Externes" },
};

/** Liefert Icon + Grund für ein wartendes/blockiertes Ticket, sonst null. */
export function waitInfo(issue: Pick<Issue, "agent_status" | "hold_reason">): WaitInfo | null {
  if (issue.agent_status === "failed") {
    return { kind: "error", ...KIND_META.error, label: "Fehler" };
  }
  const reason = issue.hold_reason;
  if (!reason) return null;
  const label = HOLD_LABEL[reason] || reason;
  if (ERROR_REASONS.has(reason)) return { kind: "error", ...KIND_META.error, label };
  if (QUESTION_REASONS.has(reason)) return { kind: "question", ...KIND_META.question, label };
  return { kind: "external", ...KIND_META.external, label };
}
