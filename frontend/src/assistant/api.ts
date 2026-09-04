import { api } from "../api";

/** One message in a conversation, as the house keeps it. */
export interface Message {
  id: number;
  text: string;
  status: string;
  result: string | null;
  error: string | null;
  pending_tool: string | null;
  created_at: string;
  finished_at: string | null;
  session_id: number | null;
  run_id: number | null;
}

/** One conversation, as its switcher needs it. */
export interface Session {
  id: number;
  agent: string;
  title: string;
  created_at: string;
  last_message_at: string | null;
  closed_at: string | null;
  message_count: number;
  running: boolean;
}

/** One step of a running message: what the console shows, as data. */
export interface Step {
  seq: number;
  kind: string;
  tool: string;
  /** The interesting part of the arguments, if there is one — a path, a query. */
  label: string;
  /** Only the assistant's own narration; a tool's answer is not shown here. */
  text: string;
  ok: boolean | null;
  ms: number | null;
}

export const assistant = {
  sessions: (closed = false) =>
    api.get<Session[]>(`/assistant/sessions${closed ? "?closed=1" : ""}`),
  closeSession: (id: number, close: boolean) =>
    api.post(`/assistant/sessions/${id}/${close ? "close" : "reopen"}`),
  newSession: (title = "") =>
    api.post<Session>("/assistant/sessions", { title }),
  chat: (limit = 30, sessionId?: number) =>
    api.get<{ messages: Message[]; more: boolean }>(
      `/assistant/chat?limit=${limit}${sessionId ? `&session_id=${sessionId}` : ""}`),
  send: (text: string, sessionId?: number) =>
    api.post<Message>("/assistant/chat", { text, session_id: sessionId }),
  decide: (id: number, decision: "once" | "always" | "never") =>
    api.post(`/assistant/chat/${id}/decide`, { decision }),
  /**
   * What the assistant is doing right now. `after` is the last sequence number
   * already held: a message that runs for ten minutes collects hundreds of
   * steps, and re-sending all of them every two seconds would be the same list
   * over and over.
   */
  progress: (id: number, after = 0) =>
    api.get<{ run_id: number | null; running: boolean; steps: Step[] }>(
      `/assistant/chat/${id}/progress?after=${after}`),
};

export const RUNNING = ["new", "approved", "running", "awaiting"];
