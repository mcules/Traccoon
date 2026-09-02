#!/usr/bin/env node
// Fails when a forbidden name appears in the repository.
//
// The house rule (AGENTS.md, "No names in the repository") says nothing in here
// names a person, a company, or a project that is not this one. That rule has no
// teeth on its own: a name removed by hand comes back with the next paste.
//
// Two files drive it:
//
//   tools/name-denylist.txt    the words, one per line
//   tools/name-allowlist.txt   the places that may still carry one, with a reason
//
// The allowlist is not an exception mechanism that grows. It is the burn-down
// list of a migration: every line names a place that still has to be dealt with,
// and it is meant to reach zero. A line whose file no longer carries the name is
// reported as stale, so the list cannot rot into a list of lies.
//
//   node tools/check-names.mjs        or, from frontend/, npm run check:names

import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync, statSync, lstatSync } from "node:fs";
import { dirname, join, resolve, extname, basename } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DENYLIST = join(ROOT, "tools", "name-denylist.txt");
const ALLOWLIST = join(ROOT, "tools", "name-allowlist.txt");

// Not our source: dependencies, build output, runtime data, and the agents' own
// checkouts of other projects under data/workspace.
const SKIP_DIRS = new Set(["node_modules", "__pycache__", "dist", "build", "data",
  ".venv", ".pytest_cache", ".mypy_cache"]);
// Binary files, and lock files: a lock file names every package it resolves,
// which is a fact about the registry and not prose we wrote.
const SKIP_EXT = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
  ".pdf", ".woff", ".woff2", ".ttf", ".otf", ".zip", ".gz", ".tar", ".bundle",
  ".mp4", ".webm", ".wav", ".mp3", ".bin", ".so", ".pyc"]);
const SKIP_NAMES = new Set(["package-lock.json", "yarn.lock", "poetry.lock"]);

function lines(file) {
  try {
    return readFileSync(file, "utf8").split("\n");
  } catch {
    return [];
  }
}

const words = lines(DENYLIST)
  .map((l) => l.trim())
  .filter((l) => l && !l.startsWith("#"))
  .map((l) => l.toLowerCase());

const allowed = new Map();
for (const line of lines(ALLOWLIST)) {
  const t = line.trim();
  if (!t || t.startsWith("#")) continue;
  const hash = t.indexOf("#");
  const path = (hash === -1 ? t : t.slice(0, hash)).trim();
  if (path) allowed.set(path, hash === -1 ? "" : t.slice(hash + 1).trim());
}

function keep(name) {
  if (name.split("/").some((p) => SKIP_DIRS.has(p))) return false;
  if (SKIP_EXT.has(extname(name).toLowerCase())) return false;
  if (SKIP_NAMES.has(basename(name))) return false;
  return true;
}

// Fallback for a place without git (the check also runs inside a plain node
// image). Walks the tree and skips what git would not have listed anyway:
// dot directories, and the environment files that hold the passwords.
function onDisk() {
  const files = [];
  const stack = [""];
  while (stack.length) {
    const rel = stack.pop();
    for (const entry of readdirSync(join(ROOT, rel), { withFileTypes: true })) {
      const name = rel ? `${rel}/${entry.name}` : entry.name;
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        if (!entry.name.startsWith(".") && !SKIP_DIRS.has(entry.name)) stack.push(name);
      } else if (entry.isFile() && !entry.name.startsWith(".env") && keep(name)) {
        files.push(name);
      }
    }
  }
  return files;
}

// What would end up in the repository: the tracked files AND the new ones that
// are not ignored. Only tracked would be the wrong question, because a file that
// has just been written is exactly the one this check has to catch, and it is
// not staged yet. `.env` sits next to the compose file and holds passwords; it is
// ignored, so it stays out on its own.
function tracked() {
  let out;
  try {
    out = execFileSync(
      "git", ["-C", ROOT, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
      { encoding: "utf8" });
  } catch {
    return onDisk();
  }
  const files = [];
  for (const name of out.split("\0")) {
    if (!name) continue;
    if (!keep(name)) continue;
    const full = join(ROOT, name);
    try {
      if (lstatSync(full).isSymbolicLink() || !statSync(full).isFile()) continue;
    } catch {
      continue; // tracked but not on the disk right now
    }
    files.push(name);
  }
  return files;
}

if (words.length === 0) {
  console.log("check-names: no denylist, nothing to do");
  process.exit(0);
}

// The two lists themselves have to name what they forbid.
const SELF = new Set(["tools/name-denylist.txt", "tools/name-allowlist.txt",
  "tools/check-names.mjs"]);

const hits = new Map();
const used = new Set();
for (const rel of tracked()) {
  if (SELF.has(rel)) continue;
  let text;
  try {
    text = readFileSync(join(ROOT, rel), "utf8");
  } catch {
    continue; // binary or unreadable: nothing to read a name out of
  }
  const low = text.toLowerCase();
  if (!words.some((w) => low.includes(w))) continue;
  if (allowed.has(rel)) {
    used.add(rel);
    continue;
  }
  const found = [];
  text.split("\n").forEach((line, i) => {
    const l = line.toLowerCase();
    if (words.some((w) => l.includes(w))) found.push([i + 1, line.trim().slice(0, 110)]);
  });
  if (found.length) hits.set(rel, found);
}

const stale = [...allowed.keys()].filter((r) => !used.has(r)).sort();

if (hits.size) {
  let total = 0;
  for (const v of hits.values()) total += v.length;
  console.log(`check-names: ${total} forbidden mention(s) in ${hits.size} file(s)\n`);
  for (const rel of [...hits.keys()].sort()) {
    console.log(`  ${rel}`);
    for (const [n, line] of hits.get(rel).slice(0, 5)) console.log(`    ${n}: ${line}`);
    if (hits.get(rel).length > 5) console.log(`    ... and ${hits.get(rel).length - 5} more`);
  }
  console.log("\nEither remove the name, or put the file on tools/name-allowlist.txt");
  console.log("with the reason it has to carry it.");
}
if (stale.length) {
  console.log(`\ncheck-names: ${stale.length} allowlist entr(y/ies) no longer needed:`);
  for (const rel of stale) console.log(`  ${rel}`);
  console.log("Remove them: an allowlist that lists clean files hides the dirty ones.");
}
if (hits.size || stale.length) process.exit(1);
console.log(`check-names: clean (${allowed.size} allowed place(s) left to deal with)`);
