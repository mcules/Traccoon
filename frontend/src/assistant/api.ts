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
  /** Waiting for an answer FROM the person — not the same as working. */
  asking: boolean;
  /** Something in there the person has not seen yet. */
  unread: boolean;
  read_at: string | null;
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
  /** Archive or reopen. An archived conversation that was never spoken in is
   *  deleted outright — the answer then says `{deleted: true}`. */
  closeSession: (id: number, close: boolean) =>
    api.post<{ id: number; deleted?: boolean }>(
      `/assistant/sessions/${id}/${close ? "close" : "reopen"}`),
  /** Everything in this conversation has been seen, as of now. */
  markRead: (id: number) => api.post(`/assistant/sessions/${id}/read`),
  newSession: (title = "") =>
    api.post<Session>("/assistant/sessions", { title }),
  chat: (limit = 30, sessionId?: number) =>
    api.get<{ messages: Message[]; more: boolean }>(
      `/assistant/chat?limit=${limit}${sessionId ? `&session_id=${sessionId}` : ""}`),
  send: (text: string, sessionId?: number) =>
    api.post<Message>("/assistant/chat", { text, session_id: sessionId }),
  /** Break off what the assistant is doing. What it already wrote stays
   *  written — this stops the work, it does not undo it. */
  stop: (id: number) => api.post(`/assistant/chat/${id}/stop`),
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

// "queued" belongs in here: a message waiting behind the one being worked on is
// outstanding work, and showing it as idle would misstate the one thing the
// switcher is there to say.
export const RUNNING = ["new", "approved", "queued", "running", "awaiting"];
