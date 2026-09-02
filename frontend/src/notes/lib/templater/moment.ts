/**
 * The moment-style date tokens the templates use, on the client side.
 *
 * A copy of the server's formatter rather than a shared module: the two run in
 * different places, and pulling a server file into the bundle would drag its
 * filesystem imports along. The token set is small and stable — if it grows,
 * both change together.
 */

const MONTHS = ['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'];
const DAYS = ['Sonntag','Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag'];
const pad = (n: number, len = 2) => String(n).padStart(len, '0');
const TOKEN = /\[([^\]]*)\]|YYYY|YY|MMMM|MMM|MM|M|dddd|ddd|DD|D|HH|H|mm|m|ss|s/g;

export function formatMoment(date: Date, pattern: string): string {
  return pattern.replace(TOKEN, (token, literal?: string) => {
    if (literal !== undefined) return literal;
    switch (token) {
      case 'YYYY': return String(date.getFullYear());
      case 'YY': return pad(date.getFullYear() % 100);
      case 'MMMM': return MONTHS[date.getMonth()];
      case 'MMM': return MONTHS[date.getMonth()].slice(0, 3);
      case 'MM': return pad(date.getMonth() + 1);
      case 'M': return String(date.getMonth() + 1);
      case 'DD': return pad(date.getDate());
      case 'D': return String(date.getDate());
      case 'dddd': return DAYS[date.getDay()];
      case 'ddd': return DAYS[date.getDay()].slice(0, 2);
      case 'HH': return pad(date.getHours());
      case 'H': return String(date.getHours());
      case 'mm': return pad(date.getMinutes());
      case 'm': return String(date.getMinutes());
      case 'ss': return pad(date.getSeconds());
      case 's': return String(date.getSeconds());
      default: return token;
    }
  });
}

/** Read a date out of text written with `pattern` (numeric parts only). */
export function parseMoment(text: string, pattern: string): Date | null {
  const order: string[] = [];
  let re = '';
  let last = 0;
  TOKEN.lastIndex = 0;
  for (let m = TOKEN.exec(pattern); m; m = TOKEN.exec(pattern)) {
    re += pattern.slice(last, m.index).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    last = m.index + m[0].length;
    if (m[1] !== undefined) { re += m[1].replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); continue; }
    switch (m[0]) {
      case 'YYYY': re += '(\\d{4})'; order.push('Y'); break;
      case 'MM': case 'M': re += '(\\d{1,2})'; order.push('M'); break;
      case 'DD': case 'D': re += '(\\d{1,2})'; order.push('D'); break;
      default: re += '.+?';
    }
  }
  re += pattern.slice(last).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = new RegExp(`^${re}$`).exec(text.trim());
  if (!m) return null;
  const parts: Record<string, number> = {};
  order.forEach((k, i) => { parts[k] = Number(m[i + 1]); });
  if (parts.Y === undefined || parts.M === undefined || parts.D === undefined) return null;
  return new Date(parts.Y, parts.M - 1, parts.D);
}

export function addDays(date: Date, days: number): Date {
  const d = new Date(date);
  d.setDate(d.getDate() + days);
  return d;
}
