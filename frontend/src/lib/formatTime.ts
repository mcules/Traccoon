/**
 * Times in the timezone of the logged-in person — not in that of the browser.
 *
 * The difference stands out exactly when it counts: on the road, on a machine with a wrongly
 * set zone, or when somebody plans their jobs by German time and the UI claims something else
 * next to it. On the server the same entry decides what "8 o'clock" means in a schedule and in
 * the night window (`users.timezone`).
 *
 * `setTimezone` is set once on login; without it the zone of the browser applies, which is
 * exactly right for the first page build.
 */
let zone: string | undefined;
let locale = "de-DE";

export function setTimezone(name: string | undefined | null): void {
  zone = name || undefined;
}

/** The language the dates are written in — the same one the UI speaks. */
export function setDateLocale(code: string | undefined | null): void {
  locale = code === "en" ? "en-GB" : code ? `${code}-${code.toUpperCase()}` : "de-DE";
}

export function timezone(): string {
  return zone || Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function withZone(opt: Intl.DateTimeFormatOptions): Intl.DateTimeFormatOptions {
  return zone ? { ...opt, timeZone: zone } : opt;
}

/** A format for timestamps in comments and chat (day.month hour:minute). */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString(locale, withZone({
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  }));
}

/** Date and time spelled out (lists, histories, "last run"). */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString(locale, withZone({
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  }));
}

/** The day only (version lists, deadlines). */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString(locale, withZone({
    day: "2-digit", month: "2-digit", year: "numeric",
  }));
}
