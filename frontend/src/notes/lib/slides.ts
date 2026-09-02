/**
 * Where one slide ends and the next begins.
 *
 * A note that presents well is a note that reads well: `---` on a line of its
 * own is already a break when reading top to bottom, so it is the break here
 * too, and nothing has to be marked up specially to be presentable.
 *
 * Two things are deliberately not breaks: the frontmatter block at the top —
 * its closing `---` would otherwise open an empty first slide — and a `---`
 * inside a fenced code block, where it is code, not a rule.
 */
export function splitSlides(src: string): string[] {
  const body = src.replace(/^---\r?\n[\s\S]*?\r?\n---[ \t]*(\r?\n|$)/, '');
  const out: string[] = [];
  let current: string[] = [];
  let fence: string | null = null;
  for (const line of body.split('\n')) {
    const f = line.match(/^[ \t]*(```+|~~~+)/);
    if (f) {
      if (!fence) fence = f[1][0];
      else if (f[1][0] === fence) fence = null;
    }
    if (!fence && /^[ \t]*---[ \t]*$/.test(line)) {
      out.push(current.join('\n'));
      current = [];
      continue;
    }
    current.push(line);
  }
  out.push(current.join('\n'));
  const slides = out.map((s) => s.trim()).filter(Boolean);
  return slides.length ? slides : ['*(leere Notiz)*'];
}
