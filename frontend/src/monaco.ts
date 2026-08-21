// Bundle Monaco locally (no CDN): provide the workers over a Vite ?worker import and point
// the @monaco-editor/react loader at the bundled instance.
import * as monaco from "monaco-editor";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import cssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";
import { loader } from "@monaco-editor/react";

(self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    if (label === "json") return new jsonWorker();
    if (label === "css" || label === "scss" || label === "less") return new cssWorker();
    if (label === "html" || label === "handlebars" || label === "razor") return new htmlWorker();
    if (label === "typescript" || label === "javascript") return new tsWorker();
    return new editorWorker();
  },
};

loader.config({ monaco });

// Prewarming in the background: starts the largest workers once so that their JS chunks
// (ts.worker about 6 MB, editor.worker) are downloaded and cached by the browser, so that
// the first real opening of "code" is no longer blocked by the worker download.
// It runs in web worker threads, so it does not block the UI, and ends again after a short time.
let _warmed = false;
export function prewarmMonaco(): void {
  if (_warmed) return;
  _warmed = true;
  try {
    const ts = new tsWorker();
    const ed = new editorWorker();
    setTimeout(() => { try { ts.terminate(); ed.terminate(); } catch { /* egal */ } }, 5000);
  } catch { /* prewarming is optional */ }
}

// Language from the file extension (Monaco derives much over the path; here the most common ones).
export function longOf(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() || "";
  const map: Record<string, string> = {
    ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
    py: "python", json: "json", css: "css", scss: "scss", less: "less",
    html: "html", md: "markdown", markdown: "markdown", yml: "yaml", yaml: "yaml",
    sh: "shell", bash: "shell", sql: "sql", toml: "ini", ini: "ini", env: "ini",
    dockerfile: "dockerfile", go: "go", rs: "rust", java: "java", xml: "xml",
    txt: "plaintext", conf: "ini",
  };
  if (path.toLowerCase().endsWith("dockerfile")) return "dockerfile";
  return map[ext] || "plaintext";
}
