// Thin fetch wrapper around the server API.

export interface TreeNode {
  name: string;
  path: string;
  type: 'file' | 'folder';
  ext?: string;
  size?: number;
  mtime?: number;
  ctime?: number;
  children?: TreeNode[];
}

export interface TrashItem {
  name: string;
  path: string; // includes the .trash/ prefix
  original: string; // where it restores to
  ext: string;
  size: number;
  mtime: number;
}

export interface SearchHit {
  path: string;
  title: string;
  score: number;
  tags: string[];
  snippet: string;
}

export interface MatchContext {
  text: string;
  ranges: [number, number][];
  pre: boolean;
  post: boolean;
}

export interface NoteMatches {
  path: string;
  count: number;
  contexts: MatchContext[];
}

export interface CalEvent {
  id: string;
  uid: string;
  calendar: string;
  title: string;
  date: string;
  time: string;
  endTime: string;
  allDay: boolean;
  start: string;
  end: string;
  location?: string;
  description?: string;
  cancelled: boolean;
}

/** One kept version of a note, as the backup wrote it. */
export interface NoteVersion {
  hash: string;
  date: string;
  message: string;
  author: string;
}

/** A page as the dataview endpoints send it: `file` metadata plus the note's
 *  own frontmatter/inline fields. Values are tagged (see lib/dataview/values). */
export interface DvRawPage {
  file: Record<string, unknown>;
  fields: Record<string, unknown>;
}

export interface DvGroupResult {
  key: unknown;
  rows: unknown[][];
  items: unknown[];
  tasks: unknown[];
}

export type DvTasksResult =
  | {
      kind: 'tasks';
      // `label_key` is what the heading says in the reader's language; `label`
      // is the same thing already written out, for whoever has no catalogue.
      groups: Array<{ key: string; label: string; label_key?: string;
                      link?: string; tasks: any[] }>;
      total: number;
      layout: { hideTaskCount: boolean; hideBacklink: boolean; hideToolbar: boolean; shortMode: boolean };
      warnings: string[];
    }
  | { kind: 'error'; message: string };

export type DvQueryResult =
  | { kind: 'table'; headers: string[]; rows: unknown[][]; groups?: DvGroupResult[] }
  | { kind: 'list'; items: unknown[]; groups?: DvGroupResult[] }
  | { kind: 'task'; groups: DvGroupResult[] }
  | { kind: 'error'; message: string };

/**
 * Where the workspace answers, and who we are while asking.
 *
 * Both come from the house now. Every address below is written as `/api/...`
 * because that is what this code grew up with; the prefix in front of it is what
 * turns it into a request to the bridge, and the token is the one the rest of
 * the application uses. There is no login of its own here any more.
 */
// Where the note routes of the house are. There used to be a second prefix
// beside this one, `/api/notes`, which reached the service being replaced; the
// last call moved off it on 2026-09-03 and nothing here asks it anything any
// more.
const NATIVE = '/api/notes-native';

export function houseToken(): string | null {
  try {
    return localStorage.getItem('traccoon_token');
  } catch {
    return null;  // private mode: no token, and the bridge will say so
  }
}

async function native<T>(url: string, opts: RequestInit = {}): Promise<T> {
  return send<T>(NATIVE + url, opts);
}

/** A route of the house that is not a note route — the assistant, above all. */
async function house<T>(url: string, opts: RequestInit = {}): Promise<T> {
  return send<T>('/api' + url, opts);
}

async function send<T>(url: string, opts: RequestInit = {}): Promise<T> {
  const { headers: optHeaders, ...rest } = opts;
  const token = houseToken();
  const res = await fetch(url, {
    credentials: 'include',
    ...rest,
    // headers MUST be merged last — spreading ...opts after a `headers` literal
    // would drop Content-Type whenever a caller passes its own headers.
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(optHeaders ?? {}),
    },
  });
  if (res.status === 401) {
    throw new ApiError('Unauthorized', 401);
  }
  if (!res.ok) {
    let msg = res.statusText;
    let data: unknown;
    try {
      data = await res.json();
      msg = (data as { error?: string }).error ?? msg;
    } catch {
      /* ignore */
    }
    // The body travels with the error: a conditional write that lost the race
    // answers 409 with the text that is on disk now, and the caller merges it.
    throw new ApiError(msg, res.status, data);
  }
  const ct = res.headers.get('content-type') ?? '';
  return (ct.includes('application/json') ? res.json() : (res.text() as unknown)) as Promise<T>;
}

export class ApiError extends Error {
  constructor(message: string, public status: number, public data?: unknown) {
    super(message);
  }
}

/** One `.base` view, run by the server. */
export interface BaseColumn {
  id: string;
  label: string;
}
export interface BaseRow {
  path: string;
  values: Record<string, unknown>;
}
export interface BaseGroup {
  key: string;
  value: unknown;
  rows: BaseRow[];
}
export interface BaseResult {
  views: Array<{ name: string; type: string }>;
  view: { name: string; type: string };
  columns: BaseColumn[];
  rows: BaseRow[];
  groups: BaseGroup[] | null;
  summaries: Record<string, string>;
  total: number;
  matched: number;
  errors: string[];
}

/** One of the assistant's conversations, as its switcher needs it. */
export interface AssistantSession {
  id: number;
  agent: string;
  title: string;
  created_at: string;
  last_message_at: string | null;
  closed_at: string | null;
  message_count: number;
  /** Something is still being worked on in there — the switcher has to say so,
   *  or one walks away from the answer one is waiting for. */
  running: boolean;
}

/** One step of a running message: what the console shows, as data. */
export interface AssistantStep {
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

/** One message in the assistant's conversation, as it keeps it. */
export interface AssistantMessage {
  id: number;
  text: string;
  session_id: number | null;
  status: string;
  result: string | null;
  error: string | null;
  run_id: number | null;
  pending_tool: string | null;
  created_at: string;
  finished_at: string | null;
}

export const api = {
  // auth






  // bases (.base tables)
  baseView: (path: string, view?: string) =>
    native<BaseResult>(
      `/bases/view?path=${encodeURIComponent(path)}${view ? `&view=${encodeURIComponent(view)}` : ''}`,
    ),

  // drawings
  excalidrawScene: (path: string) =>
    native<{ path: string; hash: string; scene: unknown }>(
      `/drawing?path=${encodeURIComponent(path)}`,
    ),
  excalidrawSave: (path: string, scene: unknown, baseHash: string) =>
    native<{ path: string; hash: string }>('/drawing', {
      method: 'PUT',
      body: JSON.stringify({ path, scene, baseHash }),
    }),

  // The assistant. It is the house's own — until now every one of these went
  // to the bridge, which held a token for this application and relayed them
  // back to it over its public address. The panel here talks to it directly.
  assistantSessions: (closed = false) =>
    house<AssistantSession[]>(`/assistant/sessions${closed ? '?closed=1' : ''}`),

  /** Put a conversation away, or bring it back — nothing is deleted either way. */
  assistantCloseSession: (id: number, close: boolean) =>
    house<unknown>(`/assistant/sessions/${id}/${close ? 'close' : 'reopen'}`, { method: 'POST' }),
  assistantNewSession: (title = '') =>
    house<AssistantSession>('/assistant/sessions', { method: 'POST', body: JSON.stringify({ title }) }),
  assistantChat: (limit = 30, sessionId?: number) =>
    house<{ messages: AssistantMessage[]; more: boolean }>(
      `/assistant/chat?limit=${limit}${sessionId ? `&session_id=${sessionId}` : ''}`,
    ),
  /** What the assistant is doing right now, as steps. `after` is the last
   *  sequence number already seen: a message that runs for ten minutes collects
   *  hundreds of steps and re-sending all of them every two seconds would be
   *  the same list over and over. */
  assistantProgress: (tid: number, after = 0) =>
    house<{ run_id: number | null; running: boolean; steps: AssistantStep[] }>(
      `/assistant/chat/${tid}/progress?after=${after}`),
  assistantSend: (text: string, sessionId?: number) =>
    house<AssistantMessage>('/assistant/chat', {
      method: 'POST',
      body: JSON.stringify({ text, sessionId }),
    }),
  assistantDecide: (id: number, decision: 'once' | 'always' | 'never') =>
    house<unknown>(`/assistant/chat/${id}/decide`, {
      method: 'POST',
      body: JSON.stringify({ decision }),
    }),

  // files
  tree: () => native<TreeNode>('/files/'),
  read: (path: string) =>
    native<{ path: string; content: string; hash?: string }>(
      `/files/content?path=${encodeURIComponent(path)}`,
    ),
  /**
   * `baseHash` makes the write conditional: it only lands while the file still
   * holds the version this editor read. On a mismatch the server answers 409 and
   * hands back the current text, which the caller merges instead of clobbering.
   */
  write: (path: string, content: string, baseHash?: string) =>
    native<{ ok: true; hash?: string }>('/files/content', {
      method: 'PUT',
      body: JSON.stringify({ path, content, baseHash }),
    }),
  /** Open/create the vault's daily note; offset in days from today. */
  dailyNote: (offset = 0) =>
    native<{ path: string; created: boolean; unresolved: string[] }>('/files/daily', {
      method: 'POST',
      body: JSON.stringify({ offset }),
    }),
  // calendar
  calendar: (from: string, to: string) =>
    native<{
      events: CalEvent[];
      errors: Array<{ calendar: string; message: string }>;
      fetchedAt: string;
      calendars: Array<{ name: string; linkTarget?: string }>;
    }>(`/calendar?from=${from}&to=${to}`),
  calendarRefresh: () =>
    native<{ count: number; errors: Array<{ calendar: string; message: string }>; fetchedAt: string }>(
      '/calendar/refresh',
      { method: 'POST' },
    ),
  calendarSyncDay: (date: string, dryRun = false) =>
    native<{ path: string; added: number; updated: number; cancelled: number; written: boolean }>(
      '/calendar/sync-day',
      { method: 'POST', body: JSON.stringify({ date, dryRun }) },
    ),


  testCalendarSource: (url: string, authUser?: string, authPassword?: string) =>
    native<{ ok: boolean; count?: number; message?: string }>('/calendar/test-source', {
      method: 'POST',
      body: JSON.stringify({ url, auth_user: authUser, auth_password: authPassword }),
    }),
  calendarWritable: () =>
    native<{ configured: boolean;
             calendars: Array<{ id: number; name: string; caldav_id: string;
                                server_id: number; server: string }>;
             error?: string }>('/calendar/writable'),
  calendarSaveEvent: (body: {
    /** The calendar as it is set up here, not a collection name on a server:
     *  two servers can hold a collection of the same name. */
    calendar: number;
    uid?: string;
    title: string;
    start: string;
    end: string;
    allDay?: boolean;
    location?: string;
    description?: string;
  }) => native<{ uid: string; url: string; created: boolean }>('/calendar/event', { method: 'POST', body: JSON.stringify(body) }),
  calendarDeleteEvent: (calendar: number, uid: string) =>
    native<{ ok: true }>(`/calendar/event?calendar=${calendar}&uid=${encodeURIComponent(uid)}`, {
      method: 'DELETE',
    }),
  calendarTidy: (dryRun = true) =>
    native<{ files: Array<{ path: string; changed: number }>; total: number; dryRun: boolean }>(
      '/calendar/tidy',
      { method: 'POST', body: JSON.stringify({ dryRun }) },
    ),
  saveHotkeys: (hotkeys: Record<string, Array<{ modifiers?: string[]; key?: string }>>) =>
    native<{ ok: true }>('/vault-config/hotkeys', { method: 'PUT', body: JSON.stringify({ hotkeys }) }),
  appearance: () =>
    native<{
      snippets: string[];
      enabledSnippets: string[];
      rainbow: { style: 'off' | 'default' | 'simple' | 'full'; opacity: number; files: boolean; inheritSubfolders: boolean };
    }>('/appearance'),
  vaultConfig: () =>
    native<{
      app: {
        attachmentFolderPath: string;
        alwaysUpdateLinks: boolean;
        readableLineLength: boolean;
        mobileToolbarCommands: string[];
        useTab: boolean;
        tabSize: number;
        showUnsupportedFiles: boolean;
        showInlineTitle: boolean;
      };
      dailyNotes: { folder: string; format: string; template: string };
      templates: { folder: string; dateFormat: string; timeFormat: string };
      hotkeys: Record<string, Array<{ modifiers?: string[]; key?: string }>>;
    }>('/vault-config'),
  templaterConfig: () =>
    native<{ folderTemplates: Array<{ folder: string; template: string }> }>('/templates/folders'),
  templaterCompile: (path: string) =>
    native<{ id: string; interactive: boolean }>('/templates/compile', {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),
  templates: () =>
    native<{ folder: string; templates: Array<{ path: string; name: string }> }>('/templates'),
  template: (path: string, title: string) =>
    native<{ text: string; unresolved: string[] }>('/templates/fill', {
      method: 'POST',
      body: JSON.stringify({ path, title }),
    }),
  snapshots: (path: string) =>
    native<{ snapshots: Array<{ ts: number; size: number }> }>(
      `/files/recovery?path=${encodeURIComponent(path)}`,
    ),
  snapshotContent: (path: string, ts: number) =>
    native<{ content: string }>(
      `/files/recovery/content?path=${encodeURIComponent(path)}&ts=${ts}`,
    ),
  restoreSnapshot: (path: string, ts: number) =>
    native<{ ok: true }>('/files/recovery/restore', { method: 'POST', body: JSON.stringify({ path, ts }) }),
  createFolder: (path: string) =>
    native<{ ok: true }>('/files/folder', { method: 'POST', body: JSON.stringify({ path }) }),
  rename: (from: string, to: string) =>
    native<{ ok: true }>('/files/rename', { method: 'PATCH', body: JSON.stringify({ from, to }) }),
  copy: (from: string, to: string) =>
    native<{ ok: true }>('/files/copy', { method: 'POST', body: JSON.stringify({ from, to }) }),
  remove: (path: string) =>
    native<{ ok: true; trashed?: string; deleted?: string }>(
      `/files/?path=${encodeURIComponent(path)}`,
      { method: 'DELETE' },
    ),
  // trash (FR-1)
  listTrash: () => native<{ items: TrashItem[] }>('/files/trash'),
  restoreTrash: (path: string) =>
    native<{ ok: true; restored: string }>('/files/trash/restore', {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),
  deleteTrashItem: (path: string) =>
    native<{ ok: true }>(`/files/trash/item?path=${encodeURIComponent(path)}`, { method: 'DELETE' }),
  emptyTrash: () => native<{ ok: true }>('/files/trash', { method: 'DELETE' }),
  uploadUrl: () => '/api/files/upload',
  /**
   * `note` lets the server apply the vault's attachment rule relative to the
   * note the file is going into; `dir` overrides it outright.
   */
  upload: async (file: File, opts: { dir?: string; note?: string } = {}) => {
    const fd = new FormData();
    if (opts.dir !== undefined) fd.append('dir', opts.dir);
    if (opts.note) fd.append('note', opts.note);
    fd.append('file', file);
    // Its own fetch, because a FormData body must not get a Content-Type of
    // ours — the browser sets one with the boundary in it. The token has to be
    // put on by hand for the same reason: this does not go through `send`.
    const token = houseToken();
    const res = await fetch(`${NATIVE}/files/upload`, {
      method: 'POST',
      credentials: 'include',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: fd,
    });
    if (!res.ok) throw new ApiError((await res.json().catch(() => ({}))).error ?? 'Upload failed', res.status);
    return res.json() as Promise<{ ok: true; path: string; size: number }>;
  },
  /** Ask the bridge for the reading cookie. Called once when the area opens. */
  openNotesSession: () => native<{ ok: true }>('/session', { method: 'POST', body: '{}' }),

  // Straight into the `src` of an image, so it carries no token: the reading
  // cookie the page fetches on arrival is what makes this one work.
  // Used as an `<img src>`, so no header of ours goes with it: the browser
  // sends the reading cookie instead. That cookie is now set for this half of
  // the note area too, which is what let this move off the bridge.
  rawUrl: (path: string) => `${NATIVE}/files/content?path=${encodeURIComponent(path)}`,

  // search & links
  // limit omitted → server returns every match (panel renders them incrementally)
  search: (q: string, limit?: number) =>
    native<{ hits: SearchHit[] }>(
      `/search?q=${encodeURIComponent(q)}${limit ? `&limit=${limit}` : ''}`,
    ),
  // per-note highlighted match contexts for the given paths (lazy, batched);
  // phrase=true matches the whole query as one needle (unlinked mentions)
  searchMatches: (query: string, paths: string[], matchCase = false, phrase = false) =>
    native<{ matches: NoteMatches[] }>('/search/matches', {
      method: 'POST',
      body: JSON.stringify({ query, paths, matchCase, phrase }),
    }),
  tags: () => native<{ tags: { tag: string; count: number }[] }>('/tags'),
  properties: () =>
    native<{ properties: { key: string; type: string; count: number }[] }>('/properties'),
  propertyTypes: () => native<{ types: Record<string, string> }>('/property-types'),
  setPropertyType: (key: string, type: string) =>
    native<{ types: Record<string, string> }>('/property-types', {
      method: 'POST',
      body: JSON.stringify({ key, type }),
    }),
  backlinks: (path: string) =>
    native<{ backlinks: string[] }>(`/backlinks?path=${encodeURIComponent(path)}`),
  resolve: (target: string) =>
    native<{ path: string | null }>(`/resolve?target=${encodeURIComponent(target)}`),
  graph: () =>
    native<{
      nodes: { id: string; label: string; kind: 'note' | 'attachment' | 'unresolved'; tags: string[] }[];
      edges: { source: string; target: string }[];
    }>('/graph'),
  reindex: () => native<{ ok: true }>('/reindex', { method: 'POST' }),

  // The workspace, kept on the person rather than in the browser. The first
  // route that comes from the house itself instead of over the bridge — the
  // others follow one at a time as they are ported.
  getUiState: () => native<any>('/uistate'),
  putUiState: (state: any, _clientId: string) =>
    native<{ ok: true }>('/uistate', { method: 'PUT', body: JSON.stringify(state) }),

  // settings



  // The older versions of a note. Reading only: they come out of the hourly
  // backup beside the vault, and nothing here writes to it. The six calls that
  // did — init, clone, pull, commit, push, sync — are gone with the service
  // that answered them.
  historyInfo: () => native<{ has: boolean; last: string | null }>('/history'),
  historyLog: (path: string) =>
    native<{ commits: NoteVersion[] }>(`/history/log?path=${encodeURIComponent(path)}`),
  historyShow: (hash: string, path: string) =>
    native<{ content: string }>(
      `/history/show?hash=${encodeURIComponent(hash)}&path=${encodeURIComponent(path)}`),

  // api keys



  // plugins
  // dataview — DQL blocks and the data behind the `dv` API of ```dataviewjs
  dvQuery: (query: string, path?: string) =>
    native<DvQueryResult>('/dataview/query', { method: 'POST', body: JSON.stringify({ query, path }) }),
  dvPages: (source?: string) =>
    native<{ pages: DvRawPage[]; total: number }>(`/dataview/pages?source=${encodeURIComponent(source ?? '')}`),
  dvPage: (path: string) => native<{ page: DvRawPage | null }>(`/dataview/page?path=${encodeURIComponent(path)}`),
  dvMeta: (path: string) =>
    native<{ path: string | null; headings?: Array<{ heading: string; level: number; line: number }>; tags?: string[] }>(
      `/dataview/meta?path=${encodeURIComponent(path)}`,
    ),
  dvSettings: () => native<{ query: any; tasks: any; statuses: any[] }>('/dataview/settings'),
  dvInline: (expr: string, path?: string) =>
    native<{ ok: boolean; value?: unknown; error?: string }>('/dataview/inline', {
      method: 'POST',
      body: JSON.stringify({ expr, path }),
    }),
  dvInlineBatch: (items: Array<{ expr: string; path?: string }>) =>
    native<{ results: Array<{ ok: boolean; value?: unknown; error?: string }> }>('/dataview/inline', {
      method: 'POST',
      body: JSON.stringify({ items }),
    }),
  dvTasks: (query: string) =>
    native<DvTasksResult>('/dataview/tasks', { method: 'POST', body: JSON.stringify({ query }) }),
  dvRegisterScript: (code: string) =>
    native<{ id: string }>('/dataview/script', { method: 'POST', body: JSON.stringify({ code }) }),
  dvToggleTask: (body: { path: string; line: number; text: string; checked: boolean; mode?: 'tasks' | 'dataview' }) =>
    native<{ ok: true }>('/dataview/task', { method: 'POST', body: JSON.stringify(body) }),
  /** What a tick would make of one task line — computed, not written. */
  dvTaskLines: (body: { line: string; checked: boolean; mode?: 'tasks' | 'dataview' }) =>
    native<{ lines: string[] }>('/dataview/task/lines', { method: 'POST', body: JSON.stringify(body) }),
};
