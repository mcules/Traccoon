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
  del: <T = any>(p: string) => request<T>(p, { method: "DELETE" }),
};

// ---------- Typen ----------
export interface User {
  id: number; email: string; username: string; display_name: string;
  global_role: string; status: string; onboarded: boolean; theme: string;
}
export interface Project {
  id: number; key: string; name: string; description: string;
  parent_id?: number | null; inherit_members?: boolean;
  managed: boolean; pm_chat_enabled: boolean; has_hardware: boolean; git_enabled?: boolean;
  my_role: string; my_ai_assign: boolean; my_role_inherited?: boolean;
}
export interface IssueType { id: number; name: string; icon: string; color: string; category: string; order: number; }
export interface Status { id: number; name: string; category: string; order: number; }
export interface Board { id: number; name: string; columns: { status_id: number; order: number }[]; }
export interface Sprint { id: number; name: string; state: string; }
export interface MemberLite { user_id: number; username: string; display_name: string; role: string; ai_assign: boolean; }
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
  archived?: boolean;
  resolved_at?: string | null;
}
export interface FileChange { id: number; path: string; status: string; additions: number; deletions: number; }
export interface AttachmentInfo { id: number; filename: string; mime_type: string; size: number; created_at: string; }
export interface Comment { id: number; issue_id: number; author_id: number | null; author_label: string; body: string; kind: string; created_at: string; }
