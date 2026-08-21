// Schema mirror of the backend workflow engine (models/workflow.py plus schemas/workflow.py).
// Has to match the backend exactly: the only contract between editor, runtime and API.

export type WorkflowSubjectKind = "issue" | "hardware_asset" | "standalone";
export type WorkflowVersionStatus = "draft" | "published" | "archived";
export type WorkflowInstanceStatus =
  | "running" | "waiting" | "completed" | "failed" | "cancelled";
export type WorkflowNodeType =
  | "start" | "end" | "human_task" | "decision" | "approval" | "auto_action" | "agent_task"
  | "wait_event" | "subflow" | "loop" | "timer";

/** Firmly named flows Traccoon triggers itself (backend: WorkflowSlot). */
export type WorkflowSlot =
  | "ticket_lifecycle" | "acceptance" | "hardware_procurement" | "ticket_intake"
  | "mail_intake";
export type WorkflowStepStatus =
  | "pending" | "running" | "waiting" | "done" | "failed" | "skipped";

// ── Graph (React Flow native; stored one to one as version.graph) ────────────

/** Resolution of the responsible person for human_task / approval. */
export interface AssigneeSpec {
  mode: "user" | "role" | "context" | "reporter";
  user_id?: number;              // mode=user
  role?: string;                 // mode=role (ProjectRole: owner|maintainer|member|viewer)
  context_key?: string;          // mode=context → context[context_key] = user_id
}

/** One dynamic form field of a human_task. */
export interface FormField {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "date" | "boolean";
  required?: boolean;
  options?: string[];            // type=select
  placeholder?: string;
}

/** JSONLogic-Ausdruck (Operator-Allowlist im Backend): { ">": [{ "var": "budget" }, 1000] }. */
export type JsonLogic = Record<string, any> | boolean;

export interface DecisionBranch {
  handle: string;                // sourceHandle-Name der ausgehenden Kante
  label: string;
  guard?: JsonLogic;             // fehlt = immer wahr
}

export type AutoActionName =
  | "create_ticket" | "notify" | "webhook" | "http_request" | "tool_call"
  | "set_context" | "set_board_status" | "metric_record" | "metric_read"
  | "series_record"
  | "note_append"
  | "comment" | "refresh_facts"
  // Zustand eines Artefakts (Ticket, Hardware, eigene Typen)
  | "set_status" | "set_field"
  // Ticket-Lebenszyklus
  | "assign_agent" | "set_cap_baseline"
  | "start_testenv" | "stop_testenv" | "accept_merge" | "deploy" | "split_tickets"
  | "stop_agent"
  // Mail-Eingang (Slot mail_intake)
  | "mail_classify" | "spam_evaluate" | "spam_card" | "spam_apply"
  | "mail_assistant_task" | "mail_assistant_card" | "mail_assistant_run"
  // Assistent allgemein (ohne Mail, ohne Ticket) und die Antwort eines Ablaufs
  | "assistant_task" | "answer"
  // Was einmal eigene Job-Arten waren: freier Agentenlauf und Skript
  | "agent_run" | "script" | "job_pause"
  // Ablagen: Texte mit Verlauf, das Gegenstück zu den Messreihen
  | "document" | "document_read"
  // Mail-Client: den Anhang einer Mail in den Kontext holen
  | "mail_attachment" | "mail_flag" | "mail_move";

export interface AutoActionConfig {
  action: AutoActionName;
  params: Record<string, any>;   // action-spezifisch (Werte dürfen {{var}} referenzieren)
}

/** node.data.config: a subset of the fields per node type. */
export interface NodeConfig {
  label?: string;
  /** Phase this step belongs to, for the presentation only (bands, filter). */
  group?: string;
  // start
  context_schema?: { key: string; type: string; required?: boolean }[];
  /** Trigger: which event this flow listens for (optionally limited to a project). */
  trigger?: {
    /** Event trigger: what the flow listens for. */
    event?: string;
    project_id?: number;
    filter?: Record<string, any>;
    /** `webhook` = called from outside, `ereignis` = listens for an event,
     *  `mail_action` = a button on a mail respectively on one of its attachments;
     *  missing = by hand respectively over a job. */
    kind?: "webhook" | "ereignis" | "mail_action";
    /** Nur bei `mail_action`: woran der Knopf haengt (Nachricht oder einzelner Anhang). */
    scope?: "message" | "attachment";
    /** Example payload, only for deriving fields in the editor, never at runtime. */
    sample?: Record<string, any>;
    /** Field of the payload the artifact stands in (ticket key or number, unit). */
    subject_field?: string;
  };
  // end
  outcome?: WorkflowInstanceStatus;
  // human_task
  assignee?: AssigneeSpec;
  form?: FormField[];
  handover?: boolean;
  due_in_hours?: number;
  instructions?: string;
  // decision
  branches?: DecisionBranch[];
  default_handle?: string;
  // approval
  approvers?: AssigneeSpec;
  gate?: "ai_assign" | "role" | "none";
  reason_required_on_reject?: boolean;
  // auto_action
  action?: AutoActionConfig;
  // wait_event
  events?: string[];             // comment | answer | manual | any
  // subflow
  // Switched off step: `ueberspringen` continues over the normal exit, `abbrechen` ends the
  // run at this point.
  disabled?: boolean;
  disabled_mode?: "skip" | "abort";
  slot?: WorkflowSlot;
  /** An explicitly named flow instead of a slot, an own one as well. */
  definition_id?: number;
  inherit_context?: boolean;
  // loop: walks through `liste` element by element; the body hangs off the exit `element`
  // and leads back here over a back edge.
  list?: string;                // Kontext-Pfad auf die Liste
  element?: string;              // unter diesem Schlüssel steht das aktuelle Element
  index?: string;                // … und hier der Zähler
  collect?: string;               // Pfad, dessen Wert je Durchlauf eingesammelt wird
  results?: string;           // wohin das Eingesammelte am Ende kommt
  max?: number;                  // Deckel gegen versehentlich riesige Listen
  // timer
  duration?: number;                // Menge …
  unit?: string;              // … in s | m | h | t
  to?: string;                  // …oder ein fester Zeitpunkt (Vorlagen erlaubt)
  // auto_action: retry instead of giving up
  retries?: number;
  retry_wait_sec?: number;
  // agent_task
  agent_role?: string;           // plan_agent | exec_agent | review_agent | assigned | <Rolle>
  phase?: "planning" | "execution";
  outcomes_map?: Record<string, string>;  // z.B. { planned:"ok", done:"ok", failed:"err", blocked:"blocked" }
  timeout_sec?: number;
}

export interface WorkflowNode {
  id: string;
  type: WorkflowNodeType;
  position: { x: number; y: number };
  data: { config: NodeConfig };
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;  // benannter Ausgang (approved/rejected/decision-handle/out)
  targetHandle?: string | null;
  label?: string;
}

export interface WorkflowGraph {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

// ── API-Ressourcen ────────────────────────────────────────────────────────────

export interface WorkflowDefinition {
  id: number;
  project_id: number | null;
  set_id?: number | null;
  slot?: string | null;
  issue_type_id?: number | null;
  archived_at?: string | null;
  key: string;
  name: string;
  description: string;
  subject_kind: WorkflowSubjectKind;
  current_version_id: number | null;
  enabled: boolean;
  created_by: number | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowVersion {
  id: number;
  definition_id: number;
  version: number;
  graph: WorkflowGraph;
  status: WorkflowVersionStatus;
  notes: string;
  created_at: string;
  published_at: string | null;
}

export interface WorkflowStepRun {
  id: number;
  instance_id: number;
  node_id: string;
  node_type: WorkflowNodeType;
  status: WorkflowStepStatus;
  assignee_user_id: number | null;
  form_data: Record<string, any> | null;
  decision: string | null;
  result: Record<string, any> | null;
  error: string | null;
  entered_at: string;
  completed_at: string | null;
  completed_by: number | null;
}

export interface WorkflowTokenLite {
  id: number;
  node_id: string;
  state: "active" | "waiting" | "consumed";
  waiting_for: string | null;
}

export interface WorkflowInstance {
  id: number;
  definition_id: number;
  version_id: number;
  project_id: number | null;
  subject_kind: WorkflowSubjectKind;
  issue_id: number | null;
  hardware_asset_id: number | null;
  status: WorkflowInstanceStatus;
  context: Record<string, any>;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  tokens: WorkflowTokenLite[];
  steps: WorkflowStepRun[];
  graph: WorkflowGraph;        // von der gepinnten Version, für die Read-only-Ansicht
}

/** Entry of the personal task inbox (open human_task/approval). */
export interface WorkflowTaskLite {
  step_id: number;
  instance_id: number;
  definition_name: string;
  node_id: string;
  node_type: "human_task" | "approval";
  node_config: NodeConfig;
  project_id: number | null;
  project_key: string | null;
  subject_kind: WorkflowSubjectKind;
  issue_key: string | null;
  entered_at: string;
}

export const NODE_TYPE_LABELS: Record<WorkflowNodeType, string> = {
  start: "node.start",
  end: "node.end",
  human_task: "node.human_task",
  decision: "node.decision",
  approval: "node.approval",
  auto_action: "node.auto_action",
  agent_task: "node.agent_task",
  wait_event: "node.wait_event",
  subflow: "node.other_flow",
  loop: "node.loop",
  timer: "node.wait",
};

export const SLOT_LABELS: Record<WorkflowSlot, string> = {
  ticket_lifecycle: "slot.ticket_lifecycle",
  acceptance: "slot.acceptance",
  hardware_procurement: "slot.hardware_procurement",
  ticket_intake: "slot.ticket_intake",
  mail_intake: "slot.mail_intake",
};

/** Process set (shipped globally or personal). */
export interface WorkflowSet {
  id: number;
  scope: "global" | "user";
  user_id: number | null;
  key: string;
  name: string;
  description: string;
  is_builtin: boolean;
}

/** One flow slot of a project including the origin of the applicable graph. */
export interface WorkflowSlotInfo {
  slot: WorkflowSlot;
  name: string;
  description: string;
  subject_kind: WorkflowSubjectKind;
  origin: "project" | "user" | "global" | "builtin" | "none";
  set_id: number | null;
  set_name: string | null;
  definition_id: number | null;
  definition_name: string | null;
  published: boolean;
  customizable: boolean;
  /** Flows that apply to one issue type only (bug is not task). */
  per_issue_type?: {
    issue_type_id: number; issue_type_name: string;
    definition_id: number; published: boolean;
  }[];
}
