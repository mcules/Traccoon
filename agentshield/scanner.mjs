// The scanner of the configuration audit.
//
// It answers one question: what does the tool see in the agent configurations right now.
// Nothing else. No schedule, no state, no comparison with yesterday, no message to anybody —
// all of that is in the backend (`services/agentshield.py`), where the rest of Traccoon's
// knowledge lives.
//
// It used to own that knowledge. It kept its own history, decided what counts as the same
// finding, wrote a hundred rows through the plugin store one call at a time and reported its
// own events. That made the piece outside the house the piece that mattered, and it was the
// only piece not in the repository. What is left here is what genuinely cannot be anywhere
// else: the npm tool, and read-only mounts on the `.claude` directories of the host.
//
// Like the shotter and the filmer it carries no authentication: it hangs on the internal
// network, has no port to the outside, and holds nothing worth stealing.
import { createServer } from "node:http";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readdirSync, existsSync } from "node:fs";

const execFileP = promisify(execFile);

const PORT = parseInt(process.env.PORT || "8790", 10);
const ROOTS = (process.env.SCAN_ROOTS || "/scan/home,/scan/stacks/*/.claude")
  .split(",").map((s) => s.trim()).filter(Boolean);

/** Expand exactly ONE '*' segment: "/scan/stacks/*\/.claude" → the directories that exist. */
function expand(root) {
  if (!root.includes("*")) return existsSync(root) ? [root] : [];
  const idx = root.indexOf("*");
  const base = root.slice(0, idx).replace(/\/$/, "");
  const after = root.slice(idx + 1);
  let names = [];
  try {
    names = readdirSync(base, { withFileTypes: true })
      .filter((d) => d.isDirectory()).map((d) => d.name);
  } catch { return []; }
  return names.map((n) => `${base}/${n}${after}`).filter((p) => existsSync(p));
}

/** The name a configuration goes by — without the mount path it happens to lie under. */
function configName(path) {
  // The home directory first, and with a return: otherwise the line below eats its
  // "/.claude" and the audit shows a configuration called "~".
  if (path === "/scan/home") return "~/.claude";
  return path.replace(/^\/scan\/stacks\//, "").replace(/\/\.claude$/, "");
}

async function scanOne(path) {
  try {
    const { stdout } = await execFileP(
      "agentshield", ["scan", "-p", path, "-f", "json", "--min-severity", "info"],
      { maxBuffer: 64 * 1024 * 1024 });
    return JSON.parse(stdout);
  } catch (e) {
    // The tool exits non-zero when it found something. That is not a failure, and its
    // report is on stdout all the same.
    if (e.stdout) { try { return JSON.parse(e.stdout); } catch { /* falls through */ } }
    return { error: String(e.message || e) };
  }
}

/**
 * One pass. The answer is the raw picture, per configuration:
 *
 *   { configs: [ { config, grade, error?, findings: [ {severity, title, file, rule, …} ] } ] }
 *
 * A configuration whose scan broke carries `error` and no findings — deliberately not an
 * empty list, which would read as "looked at it, all clean".
 */
async function scanAll() {
  const targets = [...new Set(ROOTS.flatMap(expand))];
  const configs = [];
  for (const path of targets) {
    const report = await scanOne(path);
    const config = configName(path);
    if (report.error) {
      configs.push({ config, error: String(report.error).slice(0, 300) });
      continue;
    }
    configs.push({
      config,
      grade: (report.score && report.score.grade) || "?",
      findings: report.findings || [],
    });
  }
  return { configs };
}

// A scan takes seconds, and two at once would drive the tool over the same directories
// twice for nothing. One at a time; the second caller gets a refusal, not a queue.
let running = null;

const server = createServer(async (req, res) => {
  const send = (code, obj) => {
    res.writeHead(code, { "content-type": "application/json" });
    res.end(JSON.stringify(obj));
  };
  if (req.method === "GET" && req.url === "/health") {
    return send(200, { ok: true, busy: running !== null, roots: ROOTS });
  }
  if (req.method !== "POST" || !req.url.startsWith("/scan")) {
    return send(404, { error: "unknown" });
  }
  if (running) return send(409, { error: "a scan is already running" });

  const started = new Date();
  running = scanAll();
  try {
    const result = await running;
    console.log(started.toISOString(), "scanned", result.configs.length, "configurations in",
                (Date.now() - started.getTime()) / 1000, "s");
    send(200, result);
  } catch (e) {
    console.error("scan failed", e);
    send(500, { error: String(e.message || e) });
  } finally {
    running = null;
  }
});

server.listen(PORT, () => console.log(`Configuration scanner on :${PORT} · ${ROOTS.join(", ")}`));
