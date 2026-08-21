import { trKnown } from "./i18n";

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
  /** The key of the error text, when the server named one. Lets a view react to the case
   *  itself instead of matching on the wording. */
  key?: string;
  constructor(status: number, message: string, key?: string) {
    super(message);
    this.status = status;
    this.key = key;
  }
}

/**
 * Fetch a file — with the login.
 *
 * An `<a href="/api/…">` does not send the token along: the browser does not know it, it sits
 * in the memory of the application. That is exactly why clicking an attachment produced "Not
 * authenticated" statt der Datei.
 */
export async function fetchFile(path: string): Promise<{ blob: Blob; kind: string }> {
  const token = getToken();
  const res = await fetch(`/api${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (res.status === 401) {
    setToken(null);
    if (location.pathname !== "/login") location.href = "/login";
  }
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  const blob = await res.blob();
  return { blob, kind: res.headers.get("Content-Type") || blob.type || "application/octet-stream" };
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
    let key: string | undefined;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      // The server names its error texts; the English sentence beside it is the fallback for
      // a key this language does not carry.
      if (typeof j.key === "string") {
        key = j.key;
        detail = trKnown(j.key, j.values) ?? detail;
      }
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail, key);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

/** File upload (multipart); the Content-Type is set by the browser itself (boundary). */
async function upload<T = any>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`/api${path}`, { method: "POST", headers, body: fd });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/**
 * Get the server's reasoning out of a failed response.
 *
 * Without it an upload only said "Bad Request" — the information which file is missing in the
 * blieb im Rumpf liegen.
 */
async function errorFrom(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  let key: string | undefined;
  try {
    const j = await res.json();
    if (typeof j.detail === "string") detail = j.detail;
    if (typeof j.key === "string") {
      key = j.key;
      detail = trKnown(j.key, j.values) ?? detail;
    }
  } catch {
    /* keine JSON-Antwort */
  }
  return new ApiError(res.status, detail, key);
}

/** Authenticated download: fetch the blob and open it as an object URL. */
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

/** Load authenticated and return as an object URL (for instance for <img src>). */
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
  timezone?: string;           // IANA zone: times in the UI and of one's own jobs
  mail_last_account_id?: number | null;  // the mailbox opened last
  ticket_open_mode?: string;   // popup | page — how a ticket opens on a left click
  ticket_layout?: { left?: string[]; right?: string[] };  // nutzerspez. Block-Anordnung
  // How this person sorts which list, e.g. {"processes.own": {by: "name", dir: "asc"}}.
  list_sort?: Record<string, { by: string; dir: "asc" | "desc" }>;
  pm_chat_style?: string;      // bubbles | cli — Darstellung des PM-Chats
  locale?: string;             // language of the UI (source: de)
  notify_default?: string;     // telegram | email | destination — the way when the sender names none
  notify_destination_id?: number | null;   // Kanal „ziel“: welches Ziel aufgerufen wird
  notify_email?: string | null;
  telegram_chat_id?: string | null;
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
  artifact_id?: number | null;            // the shared artifact identity (free fields)
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
// The contract lies in components/workflow/types.ts; re-exported here for convenience.
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

/** External counterpart with a stored login (secrets never come back). */
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
  /** `usable` delivers those callable in the context (the primary one per name). */
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

// ---------- Datenreihen ----------

export interface Series {
  id: number;
  key: string;
  kind: "number" | "location" | "text";
  name: string;
  description: string;
  color: string;
  settings: Record<string, any>;
  /** Latest state, depending on the kind: lat/lon/battery for locations, value for numbers. */
  state: Record<string, any>;
  points: number;
  active: boolean;
  last_at: string | null;
  owner_user_id: number | null;
  /** Mine — or only shared with me? */
  own: boolean;
  /** Name of the owning person, when it is not mine. */
  owner: string;
  has_token: boolean;
}

export interface Place {
  id: number;
  key: string;
  name: string;
  lat: number;
  lon: number;
  radius_m: number;
  color: string;
  notify: boolean;
  series_key: string;
}

export interface Grant {
  id: number;
  user_id: number;
  username: string;
  level: "view" | "manage";
}

export const seriesApi = {
  list: (kind?: string) => api.get<Series[]>(`/series${kind ? `?kind=${kind}` : ""}`),
  live: (kind = "location") => api.get<Series[]>(`/series-live?kind=${kind}`),
  create: (body: Record<string, any>) => api.post<Series>("/series", body),
  update: (key: string, body: Record<string, any>) =>
    api.put<Series>(`/series/${encodeURIComponent(key)}`, body),
  del: (key: string) => api.del(`/series/${encodeURIComponent(key)}`),
  points: (key: string, q = "") =>
    api.get<{ series: Series; points: any[] }>(`/series/${encodeURIComponent(key)}/points${q}`),
  /** A fresh token — the old one stops working afterwards. */
  newToken: (key: string) =>
    api.post<{ token: string; path: string }>(`/series/${encodeURIComponent(key)}/token`, {}),
  token: (key: string) =>
    api.get<{ token: string; path: string }>(`/series/${encodeURIComponent(key)}/token`),
  shares: (key: string) => api.get<Grant[]>(`/series/${encodeURIComponent(key)}/shares`),
  share: (key: string, body: { user_id: number; level: string }) =>
    api.post<Grant>(`/series/${encodeURIComponent(key)}/shares`, body),
  unshare: (key: string, id: number) =>
    api.del(`/series/${encodeURIComponent(key)}/shares/${id}`),
  places: () => api.get<Place[]>("/places"),
  placeCreate: (body: Record<string, any>) => api.post<Place>("/places", body),
  placeChange: (id: number, body: Record<string, any>) => api.put<Place>(`/places/${id}`, body),
  placeDelete: (id: number) => api.del(`/places/${id}`),
};

// ---------- Plugins ----------

/** What a plugin contributes: pages only so far, more is not needed yet. */
export interface PluginContribution {
  type: "page";
  path: string;
  label: string;
  icon?: string;
}

export interface PluginInfo {
  slug: string;
  name: string;
  version: string;
  icon: string;
  entry: string;
  contributions: PluginContribution[];
  /** Rights the manifest declares — and those a human has granted of them. */
  reads: string[];
  reads_granted: string[];
}

export interface PluginAdmin extends PluginInfo {
  description: string;
  enabled: boolean;
  all_users: boolean;
  allowed_user_ids: number[];
  csp: Record<string, string[]>;
  allowed_hosts: string[];
}

export const pluginApi = {
  /** What this person may see (enabled and released for them). */
  my: () => api.get<PluginInfo[]>("/plugins"),
  /** Everything, disabled ones included — the administrator's view. */
  all: () => api.get<PluginAdmin[]>("/plugins/all"),
  rights: (slug: string, body: {
    reads_granted?: string[]; enabled?: boolean;
    all_users?: boolean; allowed_user_ids?: number[];
  }) => api.put<PluginAdmin>(`/plugins/${slug}/rights`, body),
  del: (slug: string) => api.del(`/plugins/${slug}`),
  /** Upload a zip; the server evaluates the manifest and the files. */
  upload: (file: File) => upload<{ slug: string; files: number }>("/plugins", file),
};

// ---------- Deployments ----------
// Contract of the read API (`backend/app/api/deployments.py`). Except for `id` and `status`
// everything is optional: the list has to show something honest even when a field is
// missing. The API delivers text fields as an empty string instead of `null`; both are treated the same here.

/** Filter of the read API. `other` = everything that neither runs nor is ok/failed (above all `cancelled`). */
export type GraphSave = {
  result: "layout" | "entwurf" | "neuer_entwurf";
  hint: string;
  version: WfVer;
};

export type WfDiffField = { field: string; before: string; after: string };
export type WfDiffNode = { id: string; label: string; fields: WfDiffField[] };
export type WfDiff = {
  from_version: number | null; to_version: number; identical: boolean;
  nodes_added: WfDiffNode[]; nodes_removed: WfDiffNode[]; nodes_changed: WfDiffNode[];
  edges_added: string[]; edges_removed: string[];
};

export type DeploymentStatusFilter = "all" | "running" | "ok" | "failed" | "other";

export interface DeploymentRow {
  id: number;
  /** Raw status from the database: ok | failed | cancelled | building | pending | pending-check | rolledback. */
  status: string;
  project_id?: number | null;
  project_key?: string | null;
  issue_id?: number | null;
  issue_key?: string | null;
  /** Rough phase, derived from the status. */
  phase?: "queued" | "running" | "done" | "aborted" | null;
  /** Three valued: true = worked, false = did not work, null = **unknown** (not "ok"). */
  ok?: boolean | null;
  /** agent | merge | workflow | maintenance | manual; empty with old rows, so "unknown". */
  source?: string | null;
  kind?: "self" | "check" | "stack" | null;
  stack_dir?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  /** Waiting time in the queue; `null` when a timestamp is missing, never a computed 0. */
  wait_ms?: number | null;
  /** Pure working time; likewise `null` when a timestamp is missing. */
  duration_ms?: number | null;
  log_bytes?: number | null;
  /** The first ~240 characters of the log. Without it "failed" would be misleading. */
  log_head?: string | null;
}

export interface DeploymentListing {
  items: DeploymentRow[];
  count: number;
  truncated?: boolean;
  by_status?: Record<string, number>;
}

/** Only the detail endpoint delivers the full text log (up to ~20 000 characters). */
export interface DeploymentDetail extends DeploymentRow {
  log?: string | null;
}

export const deploymentApi = {
  /** Without `projectId` the global list: the maintenance updates belong to no project. */
  list: (opts: {
    projectId?: number; issueId?: number; limit?: number;
    sinceHours?: number; status?: DeploymentStatusFilter;
  } = {}) => {
    const q = new URLSearchParams();
    if (opts.limit) q.set("limit", String(opts.limit));
    if (opts.sinceHours) q.set("since_hours", String(opts.sinceHours));
    if (opts.status && opts.status !== "all") q.set("status", opts.status);
    // By contract only the project bound route knows `issue_id`; globally it has no effect.
    if (opts.issueId) q.set("issue_id", String(opts.issueId));
    const path = opts.projectId != null ? `/projects/${opts.projectId}/deployments` : "/deployments";
    const s = q.toString();
    return api.get<DeploymentListing>(`${path}${s ? `?${s}` : ""}`);
  },
  get: (id: number) => api.get<DeploymentDetail>(`/deployments/${id}`),
  /** Queue by hand (the button under Settings → Deployment). Only project bound, because a
   *  deployment without a project would have no stack directory to press.
   *  The target is determined by the server from `workspace_dir`; the client sends no path.
   *  400 = no stack directory, 409 = one is already running, 403 = the role is not enough. */
  create: (projectId: number, body: { issue_id?: number } = {}) =>
    api.post<DeploymentRow>(`/projects/${projectId}/deployments`, body),
};

export interface WfWebhook {
  id: number; route: string; public_id: string; url: string;
  secret: string; enabled: boolean; ref_field: string;
}

export const workflowApi = {
  list: (projectId: number) => api.get<WfDef[]>(`/workflows?project_id=${projectId}`),
  /** All definitions, without a project_id filter; for the project-less ones (own processes). */
  listAll: () => api.get<WfDef[]>("/workflows"),
  /** Incoming address of a flow (webhook as the source): read respectively create. */
  webhookGet: (id: number) => api.get<WfWebhook | null>(`/workflows/${id}/webhook`),
  webhookCreate: (id: number) => api.post<WfWebhook>(`/workflows/${id}/webhook`, {}),
  /** Play the flow through without anything happening (draft version). */
  dryrun: (id: number, context: Record<string, unknown>, graph?: unknown) =>
    api.post<{ status: string; error?: string | null;
               steps: { node_id: string; node_type: string; status: string;
                        decision?: string | null; result?: Record<string, any> | null;
                        error?: string | null }[] }>(`/workflows/${id}/dry-run`, { context, graph }),
  /** Have a flow drawn from a description (saves nothing). */
  draft: (id: number, description: string, graph?: unknown) =>
    api.post<{ graph: { nodes: any[]; edges: any[] }; error: string[]; explanation: string }>(
      `/workflows/${id}/draft`, { description: description, graph }),
  /** Which context fields exist, per trigger, action and node type (for the editor). */
  contextFields: () => api.get<import("./components/workflow/contextFields").ContextCatalog>(
    "/workflow-context-fields"),
  /** Finished flows to copy (the description, not the graph). */
  templates: () => api.get<{ key: string; name: string; description: string;
                             subject_kind: WfSubject; hint: string }[]>("/workflow-templates"),
  create: (body: {
    project_id?: number | null; key: string; name: string;
    description?: string; subject_kind: WfSubject; template?: string;
  }) => api.post<WfDef>("/workflows", body),
  get: (id: number) => api.get<WfDef>(`/workflows/${id}`),
  update: (id: number, body: { name?: string; key?: string; description?: string; enabled?: boolean }) =>
    api.put<WfDef>(`/workflows/${id}`, body),
  del: (id: number) => api.del(`/workflows/${id}`),

  versions: (id: number) => api.get<WfVer[]>(`/workflows/${id}/versions`),
  editable: (id: number) => api.get<WfVer>(`/workflows/${id}/editable`),
  saveVersion: (id: number, vid: number, body: { graph: WfGraph; notes?: string }) =>
    api.put<WfVer>(`/workflows/${id}/versions/${vid}`, body),
  /** Saves the editor state. The server decides whether that is worth a version: the same
   *  content means an arrangement (`layout`), otherwise a draft comes into being or grows. */
  saveGraph: (id: number, body: { graph: WfGraph; notes?: string }) =>
    api.put<GraphSave>(`/workflows/${id}/graph`, body),
  discardDraft: (id: number) => api.del(`/workflows/${id}/draft`),
  diff: (id: number, vid: number, against?: number) =>
    api.get<WfDiff>(`/workflows/${id}/versions/${vid}/diff${against ? `?against=${against}` : ""}`),
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

  /** Spacing (px) for "arrange" in the editor, set globally by the admin. */
  layout: () => api.get<{ gap: number }>("/workflow-layout"),
  setLayout: (gap: number) => api.put<{ gap: number }>("/admin/workflow-layout", { gap }),

  // ── Process sets and slots ─────────────────────────────────────────────────
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

// ── Process administration (cross-cutting) ───────────────────────────────────

export interface ProcDeviation {
  project_id: number; project_key: string; project_name: string;
  definition_id: number; published: boolean;
}

export interface ProcSlot {
  slot: string; name: string; description: string; subject_kind: WfSubject;
  definition_id: number | null; definition_name: string | null;
  version: number | null; published: boolean; updated_at: string | null;
  deviations: ProcDeviation[];
}

export interface ProcRun {
  id: number; definition_id: number; definition_name: string; slot: string | null;
  project_id: number | null; project_key: string | null;
  subject_kind: WfSubject; subject_ref: string | null;
  status: "running" | "waiting" | "completed" | "failed" | "cancelled";
  node_label: string | null; waiting_for: string | null;
  since: string | null; hours: number | null; hangs: boolean;
  error: string | null; started_at: string;
}

export interface ProcTrigger {
  definition_id: number; definition_name: string; slot: string | null;
  project_id: number | null; project_key: string | null;
  kind: "event" | "webhook" | "job" | "subflow" | "manual";
  source: string; label: string; only_project_id: number | null; enabled: boolean;
}

export interface ProcEvent { event: string; label: string; listeners: number }

export const processApi = {
  slots: (setId?: number) =>
    api.get<ProcSlot[]>(`/processes/slots${setId ? `?set_id=${setId}` : ""}`),
  running: (opts?: { includeDone?: boolean; onlyStuck?: boolean }) => {
    const q = new URLSearchParams();
    if (opts?.includeDone) q.set("include_done", "true");
    if (opts?.onlyStuck) q.set("only_stuck", "true");
    const s = q.toString();
    return api.get<ProcRun[]>(`/processes/running${s ? `?${s}` : ""}`);
  },
  triggers: () => api.get<ProcTrigger[]>("/processes/triggers"),
  events: () => api.get<ProcEvent[]>("/processes/events"),
};
