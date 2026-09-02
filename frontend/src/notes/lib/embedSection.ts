/**
 * `![[Note#Section]]` embeds only that section, the way the predecessor does it.
 *
 * The target's heading is matched by name, and everything up to the next
 * heading of the same or a higher level belongs to it. A `#^block` reference
 * embeds the line carrying that block id.
 */

/** Strip the frontmatter block; an embed never shows it. */
export function stripFrontmatter(content: string): string {
  return content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '');
}

export function extractEmbedSection(content: string, target: string): string {
  const hash = target.indexOf('#');
  const body = stripFrontmatter(content);
  if (hash < 0) return body;
  const anchor = target.slice(hash + 1).trim();
  if (!anchor) return body;

  const lines = body.split('\n');

  if (anchor.startsWith('^')) {
    const id = anchor.slice(1).toLowerCase();
    const hit = lines.find((l) => l.trimEnd().toLowerCase().endsWith(`^${id}`));
    return hit ?? body;
  }

  const want = anchor.toLowerCase();
  let start = -1;
  let level = 0;
  for (let i = 0; i < lines.length; i++) {
    const m = /^(#{1,6})\s+(.+?)\s*$/.exec(lines[i]);
    if (!m) continue;
    if (m[2].trim().toLowerCase() === want) {
      start = i;
      level = m[1].length;
      break;
    }
  }
  if (start < 0) return body;

  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    const m = /^(#{1,6})\s+/.exec(lines[i]);
    if (m && m[1].length <= level) {
      end = i;
      break;
    }
  }
  return lines.slice(start, end).join('\n');
}
