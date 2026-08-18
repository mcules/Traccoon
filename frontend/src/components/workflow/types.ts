// Schema-Spiegel der Backend-Workflow-Engine (models/workflow.py + schemas/workflow.py).
// Muss exakt zum Backend passen — der einzige Vertrag zwischen Editor, Runtime und API.

export type WorkflowSubjectKind = "issue" | "hardware_asset" | "standalone";
export type WorkflowVersionStatus = "draft" | "published" | "archived";
export type WorkflowInstanceStatus =
  | "running" | "waiting" | "completed" | "failed" | "cancelled";
export type WorkflowNodeType =
  | "start" | "end" | "human_task" | "decision" | "approval" | "auto_action" | "agent_task"
  | "wait_event" | "subflow";

/** Fest benannte Abläufe, die Traccoon selbst auslöst (Backend: WorkflowSlot). */
export type WorkflowSlot =
  | "ticket_lifecycle" | "acceptance" | "hardware_procurement" | "ticket_intake"
  | "mail_intake";
export type WorkflowStepStatus =
  | "pending" | "running" | "waiting" | "done" | "failed" | "skipped";

// ── Graph (React-Flow-nativ; wird 1:1 als version.graph gespeichert) ──────────

/** Zuständigen-Auflösung für human_task / approval. */
export interface AssigneeSpec {
  mode: "user" | "role" | "context" | "reporter";
  user_id?: number;              // mode=user
  role?: string;                 // mode=role (ProjectRole: owner|maintainer|member|viewer)
  context_key?: string;          // mode=context → context[context_key] = user_id
}

/** Ein dynamisches Formularfeld eines human_task. */
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
  | "create_ticket" | "notify" | "webhook" | "http_request" | "set_context" | "set_board_status"
  | "comment" | "refresh_facts"
  // Zustand eines Artefakts (Ticket, Hardware, eigene Typen)
  | "set_status" | "set_field"
  // Ticket-Lebenszyklus
  | "assign_agent" | "set_cap_baseline"
  | "start_testenv" | "stop_testenv" | "accept_merge" | "deploy" | "split_tickets"
  | "stop_agent"
  // Mail-Eingang (Slot mail_intake)
  | "mail_classify" | "spam_evaluate" | "spam_card" | "spam_apply"
  | "assistant_task" | "assistant_card" | "assistant_run";

export interface AutoActionConfig {
  action: AutoActionName;
  params: Record<string, any>;   // action-spezifisch (Werte dürfen {{var}} referenzieren)
}

/** node.data.config — je Node-Typ eine Teilmenge der Felder. */
export interface NodeConfig {
  label?: string;
  /** Phase, der dieser Schritt angehört — nur für die Darstellung (Bänder/Filter). */
  group?: string;
  // start
  context_schema?: { key: string; type: string; required?: boolean }[];
  /** Auslöser: auf welches Ereignis dieser Ablauf hört (optional projektbegrenzt). */
  trigger?: { event: string; project_id?: number; filter?: Record<string, any> };
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
  slot?: WorkflowSlot;
  inherit_context?: boolean;
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

/** Eintrag der persönlichen Aufgaben-Inbox (offene human_task/approval). */
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
  start: "Start",
  end: "Ende",
  human_task: "Aufgabe",
  decision: "Verzweigung",
  approval: "Freigabe",
  auto_action: "Aktion",
  agent_task: "KI-Agent",
  wait_event: "Warten auf Ereignis",
  subflow: "Anderer Ablauf",
};

export const SLOT_LABELS: Record<WorkflowSlot, string> = {
  ticket_lifecycle: "KI-Ticket-Lebenszyklus",
  acceptance: "Abnahme & Auslieferung",
  hardware_procurement: "Hardware-Beschaffung",
  ticket_intake: "Ticket-Eingang",
  mail_intake: "Mail-Eingang",
};

/** Prozess-Satz (global ausgeliefert oder persönlich). */
export interface WorkflowSet {
  id: number;
  scope: "global" | "user";
  user_id: number | null;
  key: string;
  name: string;
  description: string;
  is_builtin: boolean;
}

/** Ein Ablauf-Platz eines Projekts samt Herkunft des geltenden Graphen. */
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
  /** Abläufe, die nur für eine Vorgangsart gelten (Bug ≠ Aufgabe). */
  per_issue_type?: {
    issue_type_id: number; issue_type_name: string;
    definition_id: number; published: boolean;
  }[];
}
