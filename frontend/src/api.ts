const TOKEN_KEY = "traccoon_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string | null) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function request<T = any>(
  path: string,
  opts: { method?: string; body?: any } = {}
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`/api${path}`, {
    method: opts.method || "GET",
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && !path.startsWith("/auth/")) {
    setToken(null);
    if (location.pathname !== "/login") location.href = "/login";
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

/** Datei-Upload (multipart) — Content-Type setzt der Browser selbst (boundary). */
async function upload<T = any>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`/api${path}`, { method: "POST", headers, body: fd });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return res.json();
}

/** Authentifizierter Download: Blob holen und als Object-URL öffnen. */
async function download(path: string, filename: string): Promise<void> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`/api${path}`, { headers });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Authentifiziert laden und als Object-URL zurückgeben (z. B. für <img src>). */
async function blobUrl(path: string): Promise<string> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`/api${path}`, { headers });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return URL.createObjectURL(await res.blob());
}

export const api = {
  get: <T = any>(p: string) => request<T>(p),
  upload,
  download,
  blobUrl,
  post: <T = any>(p: string, body?: any) => request<T>(p, { method: "POST", body }),
  put: <T = any>(p: string, body?: any) => request<T>(p, { method: "PUT", body }),
  patch: <T = any>(p: string, body?: any) => request<T>(p, { method: "PATCH", body }),
  del: <T = any>(p: string) => request<T>(p, { method: "DELETE" }),
};

// ---------- Typen ----------
export interface User {
  id: number; email: string | null; username: string; display_name: string;
  global_role: string; status: string; onboarded: boolean; theme: string;
  ticket_open_mode?: string;   // popup | page — wie ein Ticket per Linksklick öffnet
  ticket_layout?: { left?: string[]; right?: string[] };  // nutzerspez. Block-Anordnung
  pm_chat_style?: string;      // bubbles | cli — Darstellung des PM-Chats
}
export interface Project {
  id: number; key: string; name: string; description: string;
  parent_id?: number | null; inherit_members?: boolean;
  managed: boolean; pm_chat_enabled: boolean; has_hardware: boolean; git_enabled?: boolean;
  testenv_enabled?: boolean;   // Testumgebungs-Schritt vor „Fertig“ (ABC-18)
  my_role: string; my_ai_assign: boolean; my_role_inherited?: boolean;
  is_member: boolean; is_new: boolean;
}
export interface IssueType { id: number; name: string; icon: string; color: string; category: string; order: number; }
export interface Status { id: number; name: string; category: string; order: number; }
export interface Board { id: number; name: string; columns: { status_id: number; order: number }[]; }
export interface Sprint { id: number; name: string; state: string; }
export interface MemberLite { user_id: number; username: string; display_name: string; role: string; ai_assign: boolean; status: string; }
export interface PlaceholderUser { id: number; username: string; display_name: string; status: string; }
export interface ProjectMeta {
  types: IssueType[]; statuses: Status[]; boards: Board[]; sprints: Sprint[];
  members: MemberLite[]; my_ai_assign: boolean; my_role: string;
}
export interface Issue {
  id: number; key: string; number: number; project_id: number;
  type_id: number; status_id: number; agent_status: string | null; hold_reason: string | null;
  priority: string; summary: string; description: string | null;
  reporter_id: number; assignee_user_id: number | null;
  assigned_agent: string | null; assigned_by_user_id: number | null; assigned_at: string | null;
  plan: string | null;
  testenv_status?: string | null; testenv_url?: string | null; testenv_error?: string | null;
  parent_ticket_id: number | null; split_order: number | null;
  sprint_id: number | null; story_points: number | null; rank: string; agent_working: boolean;
  workflow_instance_id?: number | null;   // laufender Lebenszyklus-Prozess
  artifact_id?: number | null;            // gemeinsame Artefakt-Identität (freie Felder)
  asset_id?: number | null;   // Hardware-Bezug (Exemplar), nur in Hardware-Projekten
  archived?: boolean;
  resolved_at?: string | null;
}
export interface FileChange { id: number; path: string; status: string; additions: number; deletions: number; }
export interface AttachmentInfo { id: number; filename: string; mime_type: string; size: number; created_at: string; }
export interface Comment { id: number; issue_id: number; author_id: number | null; author_label: string; body: string; kind: string; created_at: string; }
export interface CostByModel { provider: string; model: string; usd: number; input_tokens: number; output_tokens: number; calls: number; }
export interface IssueCosts { total_usd: number; input_tokens: number; output_tokens: number; by_model: CostByModel[]; }
export interface ProjectCosts {
  total_usd: number; input_tokens: number; output_tokens: number;
  by_agent: { agent: string; usd: number; calls: number }[];
  by_model: CostByModel[];
}
export interface MyTicket {
  key: string; summary: string; priority: string;
  agent_status: string | null; hold_reason: string | null;
  assigned_agent: string | null; agent_working: boolean;
  category: string; updated_at: string;
  project_id: number; project_key: string; project_name: string;
}
export interface MyDashboard {
  action: MyTicket[]; assigned: MyTicket[];
  stats: { projects: number; action: number; assigned: number;
    working: number; unread: number; done_7d: number };
}

// ---------- Workflow-Engine ----------
// Vertrag liegt in components/workflow/types.ts; hier für Bequemlichkeit re-exportiert.
export type {
  WorkflowDefinition, WorkflowVersion, WorkflowInstance, WorkflowStepRun,
  WorkflowTokenLite, WorkflowTaskLite, WorkflowGraph, WorkflowNode, WorkflowEdge,
  NodeConfig, WorkflowSubjectKind, WorkflowNodeType, WorkflowVersionStatus,
  WorkflowInstanceStatus, WorkflowStepStatus, AssigneeSpec, FormField,
  DecisionBranch, AutoActionConfig, JsonLogic,
} from "./components/workflow/types";

import type {
  WorkflowDefinition as WfDef, WorkflowVersion as WfVer, WorkflowInstance as WfInst,
  WorkflowTaskLite as WfTask, WorkflowGraph as WfGraph, WorkflowSubjectKind as WfSubject,
  WorkflowSet as WfSet, WorkflowSlotInfo as WfSlot,
} from "./components/workflow/types";

export type DestinationScope = "global" | "user" | "project";

/** Externe Gegenstelle mit hinterlegter Anmeldung (Geheimnisse kommen nie zurück). */
export interface Destination {
  id: number; name: string; label: string; description: string;
  user_id: number | null; project_id: number | null; scope: DestinationScope;
  base_url: string; auth_type: string; username: string; has_secret: boolean;
  api_key_name: string; api_key_in: string;
  hmac_header: string; hmac_algo: string; hmac_prefix: string;
  oauth_token_url: string; oauth_client_id: string; oauth_scope: string; oauth_audience: string;
  default_headers: Record<string, any>; timeout_sec: number; verify_tls: boolean;
  max_response_chars: number;
  enabled: boolean; allow_agents: boolean;
  last_used_at: string | null; created_at: string;
}

export interface HttpCallResult {
  destination: string; method: string; url: string;
  status_code: number; ok: boolean; json?: any; text?: string; error?: string;
}

export const destinationApi = {
  /** `usable` liefert die im Zusammenhang aufrufbaren (je Name das vorrangige). */
  list: (projectId?: number, usable = false) =>
    api.get<Destination[]>(
      `/destinations${projectId ? `?project_id=${projectId}` : ""}${
        usable ? `${projectId ? "&" : "?"}usable=true` : ""}`),
  create: (body: Record<string, any>) => api.post<Destination>("/destinations", body),
  update: (id: number, body: Record<string, any>) => api.put<Destination>(`/destinations/${id}`, body),
  del: (id: number) => api.del(`/destinations/${id}`),
  test: (id: number, body: { method?: string; path?: string; query?: Record<string, any>;
                             headers?: Record<string, any>; body?: any }) =>
    api.post<HttpCallResult>(`/destinations/${id}/test`, body),
};

export const workflowApi = {
  list: (projectId: number) => api.get<WfDef[]>(`/workflows?project_id=${projectId}`),
  /** Alle Definitionen — ohne project_id-Filter; für die projektlosen (eigene Prozesse). */
  listAll: () => api.get<WfDef[]>("/workflows"),
  create: (body: {
    project_id?: number | null; key: string; name: string;
    description?: string; subject_kind: WfSubject;
  }) => api.post<WfDef>("/workflows", body),
  get: (id: number) => api.get<WfDef>(`/workflows/${id}`),
  update: (id: number, body: { name?: string; description?: string; enabled?: boolean }) =>
    api.put<WfDef>(`/workflows/${id}`, body),
  del: (id: number) => api.del(`/workflows/${id}`),

  versions: (id: number) => api.get<WfVer[]>(`/workflows/${id}/versions`),
  editable: (id: number) => api.get<WfVer>(`/workflows/${id}/editable`),
  saveVersion: (id: number, vid: number, body: { graph: WfGraph; notes?: string }) =>
    api.put<WfVer>(`/workflows/${id}/versions/${vid}`, body),
  validate: (id: number, vid: number) =>
    api.post<{ ok: boolean; errors: string[] }>(`/workflows/${id}/versions/${vid}/validate`),
  publish: (id: number, vid: number) => api.post<WfVer>(`/workflows/${id}/versions/${vid}/publish`),

  createInstance: (id: number, body: {
    subject_kind: WfSubject; issue_id?: number; hardware_asset_id?: number;
    context?: Record<string, any>;
  }) => api.post<WfInst>(`/workflows/${id}/instances`, body),
  instance: (iid: number) => api.get<WfInst>(`/workflow-instances/${iid}`),
  instancesForSubject: (subject: string) =>
    api.get<WfInst[]>(`/workflow-instances?subject=${encodeURIComponent(subject)}`),
  completeStep: (iid: number, sid: number, body: { form_data?: Record<string, any>; next_assignee?: number }) =>
    api.post<WfInst>(`/workflow-instances/${iid}/steps/${sid}/complete`, body),
  approveStep: (iid: number, sid: number, body: { reason?: string }) =>
    api.post<WfInst>(`/workflow-instances/${iid}/steps/${sid}/approve`, body),
  rejectStep: (iid: number, sid: number, body: { reason: string }) =>
    api.post<WfInst>(`/workflow-instances/${iid}/steps/${sid}/reject`, body),
  cancel: (iid: number) => api.post<WfInst>(`/workflow-instances/${iid}/cancel`),
  myTasks: () => api.get<WfTask[]>(`/workflow-instances/tasks?assignee=me`),

  /** Abstand (px) fürs „Anordnen" im Editor — global vom Admin gesetzt. */
  layout: () => api.get<{ gap: number }>("/workflow-layout"),
  setLayout: (gap: number) => api.put<{ gap: number }>("/admin/workflow-layout", { gap }),

  // ── Prozess-Sätze & Slots ──────────────────────────────────────────────────
  sets: () => api.get<WfSet[]>("/workflow-sets"),
  setSlots: (setId: number) => api.get<WfSlot[]>(`/workflow-sets/${setId}/slots`),
  createMySet: (body: { name?: string; source_set_id?: number | null }) =>
    api.post<WfSet>("/me/workflow-set", body),
  dropMySet: () => api.del("/me/workflow-set"),

  projectSlots: (projectId: number) => api.get<WfSlot[]>(`/projects/${projectId}/workflow-slots`),
  customizeSlot: (projectId: number, slot: string, issueTypeId?: number) =>
    api.post<WfDef>(`/projects/${projectId}/workflow-slots/${slot}/customize`
      + (issueTypeId ? `?issue_type_id=${issueTypeId}` : "")),
  resetSlot: (projectId: number, slot: string, issueTypeId?: number) =>
    api.post<{ reset: boolean }>(`/projects/${projectId}/workflow-slots/${slot}/reset`
      + (issueTypeId ? `?issue_type_id=${issueTypeId}` : "")),
  setProjectSet: (projectId: number, setId: number | null) =>
    api.put<WfSlot[]>(`/projects/${projectId}/workflow-set${setId === null ? "" : `?set_id=${setId}`}`),

  rollback: (id: number, vid: number) =>
    api.post<WfVer>(`/workflows/${id}/versions/${vid}/rollback`),
};

// ── Prozess-Verwaltung (übergreifend) ────────────────────────────────────────

export interface ProcAbweichung {
  project_id: number; project_key: string; project_name: string;
  definition_id: number; published: boolean;
}

export interface ProcSlot {
  slot: string; name: string; description: string; subject_kind: WfSubject;
  definition_id: number | null; definition_name: string | null;
  version: number | null; published: boolean; updated_at: string | null;
  abweichungen: ProcAbweichung[];
}

export interface ProcLauf {
  id: number; definition_id: number; definition_name: string; slot: string | null;
  project_id: number | null; project_key: string | null;
  subject_kind: WfSubject; subject_ref: string | null;
  status: "running" | "waiting" | "completed" | "failed" | "cancelled";
  node_label: string | null; waiting_for: string | null;
  seit: string | null; stunden: number | null; haengt: boolean;
  error: string | null; started_at: string;
}

export interface ProcAusloeser {
  definition_id: number; definition_name: string; slot: string | null;
  project_id: number | null; project_key: string | null;
  kind: "event" | "webhook" | "job" | "subflow" | "manual";
  source: string; label: string; only_project_id: number | null; enabled: boolean;
}

export interface ProcEreignis { event: string; label: string; listeners: number }

export const processApi = {
  slots: (setId?: number) =>
    api.get<ProcSlot[]>(`/processes/slots${setId ? `?set_id=${setId}` : ""}`),
  running: (opts?: { includeDone?: boolean; onlyStuck?: boolean }) => {
    const q = new URLSearchParams();
    if (opts?.includeDone) q.set("include_done", "true");
    if (opts?.onlyStuck) q.set("only_stuck", "true");
    const s = q.toString();
    return api.get<ProcLauf[]>(`/processes/running${s ? `?${s}` : ""}`);
  },
  triggers: () => api.get<ProcAusloeser[]>("/processes/triggers"),
  events: () => api.get<ProcEreignis[]>("/processes/events"),
};
