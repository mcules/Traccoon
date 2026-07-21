// Monaco lokal bündeln (kein CDN): Worker per Vite-?worker-Import bereitstellen
// und den @monaco-editor/react-Loader auf die gebündelte Instanz zeigen lassen.
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

// Sprache aus Dateiendung (Monaco leitet vieles über den Pfad ab; hier die häufigsten).
export function langOf(path: string): string {
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
