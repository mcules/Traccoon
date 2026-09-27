// Prepare the config directory of the session before claude starts.
//
// Merged, never replaced: the directory lives on a volume, and what the person set up in the
// session themselves (settings, their own MCP servers, the conversation history) stays.
// Only the keys Traccoon owns are written on every start: the onboarding flags, the trust of
// the working directory and the `traccoon` MCP server (its token changes with every start).
const fs = require("fs");
const path = require("path");

const dir = process.env.CLAUDE_CONFIG_DIR;
const workdir = process.env.SESSION_WORKDIR || "/workspace";

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return {};
  }
}

function writeJson(file, data) {
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + "\n", { mode: 0o600 });
}

// Onboarding and the trust dialog would otherwise stop the first start at a question that
// nobody is there to answer when a ticket is delivered.
const stateFile = path.join(dir, ".claude.json");
const state = readJson(stateFile);
state.hasCompletedOnboarding = true;
state.projects = state.projects || {};
state.projects[workdir] = {
  ...(state.projects[workdir] || {}),
  hasTrustDialogAccepted: true,
  hasCompletedProjectOnboarding: true,
};
state.mcpServers = state.mcpServers || {};
if (process.env.TRACCOON_MCP_URL && process.env.TRACCOON_MCP_TOKEN) {
  state.mcpServers.traccoon = {
    type: "http",
    url: process.env.TRACCOON_MCP_URL,
    headers: { Authorization: `Bearer ${process.env.TRACCOON_MCP_TOKEN}` },
  };
}
writeJson(stateFile, state);

// The ticket tools may run without asking: they are the session's own way of reporting back.
const settingsFile = path.join(dir, "settings.json");
const settings = readJson(settingsFile);
settings.permissions = settings.permissions || {};
const allow = new Set(settings.permissions.allow || []);
allow.add("mcp__traccoon");
settings.permissions.allow = [...allow];
settings.includeCoAuthoredBy = false;
settings.attribution = { commit: "", pr: "" };
writeJson(settingsFile, settings);

// House rules for every session, written once; after that the file belongs to the person.
const rulesFile = path.join(dir, "CLAUDE.md");
if (!fs.existsSync(rulesFile)) {
  fs.writeFileSync(
    rulesFile,
    `# Working in a Traccoon session

Tickets are delivered into this session by Traccoon. Each one starts with a line
\`[Traccoon ticket KEY-123]\`.

- Work on the ticket in the current working directory.
- Commit your changes yourself, with the ticket key at the start of the message.
  Follow the project's commit conventions. Never add Co-Authored-By or "Generated with" lines.
- When the ticket is finished, or you cannot go on, call the MCP tool
  \`traccoon.ticket_report\` with the ticket key, a status (done, blocked, failed) and a
  short summary. Traccoon only moves the ticket on after that call.
- A deploy job starts with \`[Traccoon release ID]\` and lists the tickets of the release.
  Deploy, check the result, then call \`traccoon.release_report\` with that id, done or failed.
- Questions to the person who released the ticket go into \`ticket_report\` with status
  \`blocked\`, or directly here in the terminal if they are watching.
`,
  );
}
