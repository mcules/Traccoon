/**
 * The two questions a template can ask.
 *
 * Templates in this vault are conversations: creating a person asks ten times —
 * company, salutation, phone, callsign… — so these have to be real dialogs, not
 * `window.prompt`, which is modal to the whole browser and cannot offer a list.
 *
 * Implemented as a promise plus a tiny event: the dialog component listens, and
 * the template simply awaits an answer.
 */

export interface AskRequest {
  kind: 'text' | 'choice';
  label: string;
  fallback?: string;
  multiline?: boolean;
  options?: Array<{ label: string; value: unknown }>;
  resolve: (value: unknown) => void;
}

type Listener = (req: AskRequest | null) => void;
let listener: Listener | null = null;
const queue: AskRequest[] = [];

export function onAsk(fn: Listener | null): void {
  listener = fn;
  if (fn && queue.length) fn(queue[0]);
}

function enqueue(req: AskRequest): void {
  queue.push(req);
  if (queue.length === 1) listener?.(req);
}

/** Called by the dialog once the person has answered (or cancelled). */
export function answer(value: unknown): void {
  const req = queue.shift();
  req?.resolve(value);
  listener?.(queue[0] ?? null);
}

export function askText(label: string, fallback = '', multiline = false): Promise<string | null> {
  return new Promise((resolve) =>
    enqueue({ kind: 'text', label, fallback, multiline, resolve: (v) => resolve(v as string | null) }),
  );
}

export function askChoice(labels: string[], values: unknown[], label = ''): Promise<unknown> {
  return new Promise((resolve) =>
    enqueue({
      kind: 'choice',
      label,
      options: labels.map((l, i) => ({ label: l, value: values[i] })),
      resolve,
    }),
  );
}
