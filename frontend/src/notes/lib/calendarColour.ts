/**
 * A stable colour per calendar, wherever a calendar is named.
 *
 * The month grid, the time grid and the appointment lines in a note all show
 * the same appointments, and a reader who has learnt that Vostura is teal
 * should not have to learn it again per view. The hue comes from the name, so
 * nothing has to be stored and a calendar added tomorrow already has one.
 */
export function hueFor(name: string): number {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

/** The badge that carries a calendar's name in a note, in that calendar's hue. */
export function calendarBadgeStyle(name: string): string {
  const h = hueFor(name);
  // Dark enough to read light text on, saturated enough to tell two apart.
  return `border-color:hsl(${h},45%,42%);background:hsl(${h},40%,20%);color:hsl(${h},65%,78%)`;
}
