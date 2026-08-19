/**
 * Uhrzeiten in der Zeitzone der angemeldeten Person — nicht in der des Browsers.
 *
 * Der Unterschied fällt genau dann auf, wenn er zählt: unterwegs, auf einem Rechner mit
 * falsch gestellter Zone, oder wenn jemand seine Jobs nach deutscher Zeit plant und die
 * Oberfläche daneben etwas anderes behauptet. Serverseitig entscheidet dieselbe Angabe, was
 * „8 Uhr" in einem Zeitplan und im Nachtfenster heißt (`users.timezone`).
 *
 * `setzeZeitzone` wird einmal beim Anmelden gesetzt; ohne sie bleibt es bei der Zone des
 * Browsers, was für den ersten Seitenaufbau genau richtig ist.
 */
let zone: string | undefined;

export function setzeZeitzone(name: string | undefined | null): void {
  zone = name || undefined;
}

export function zeitzone(): string {
  return zone || Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function mitZone(opt: Intl.DateTimeFormatOptions): Intl.DateTimeFormatOptions {
  return zone ? { ...opt, timeZone: zone } : opt;
}

/** Ein Format für Zeitstempel in Kommentaren und Chat (Tag.Monat Stunde:Minute). */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString("de-DE", mitZone({
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  }));
}

/** Datum und Uhrzeit ausgeschrieben (Listen, Verläufe, „zuletzt gelaufen"). */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString("de-DE", mitZone({
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  }));
}

/** Nur der Tag (Versionslisten, Stichtage). */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString("de-DE", mitZone({
    day: "2-digit", month: "2-digit", year: "numeric",
  }));
}
