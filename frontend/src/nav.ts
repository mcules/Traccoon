import { tr } from "./i18n";

/**
 * The large areas of Traccoon, in one place.
 *
 * They used to live twice: once in the user menu behind the avatar (desktop) and once in the
 * burger menu (phone), in a different order and with different entries, and the inbox
 * appeared in neither of them, only as a badge. Two lists mean two truths, and the one
 * behind the avatar claimed that the office and the administration were personal settings.
 *
 * Labels are fetched inside the function on purpose: a constant at module level would fix
 * the language of the first call and never change again on a language switch.
 */
/**
 * Width of the rail, and the left edge of everything that covers the page.
 *
 * The office and the flow editor lie over the page as `fixed inset-0`: they need the room
 * and they cover the header on purpose. What they must NOT cover is the area navigation,
 * because a full screen one cannot leave except by the browser back button is a trap. Both
 * strings belong together, which is why they stand here and not in three components.
 */
export const SCHIENE_BREITE = "w-[76px]";
export const SCHIENE_FREILASSEN = "md:left-[76px]";

export type NavEintrag = {
  key: string;
  label: string;
  icon: string;
  to: string;
  /** Counter of what waits there (currently only the assistant inbox). */
  zaehler?: "inbox";
};

export function hauptNavigation(istAdmin: boolean): NavEintrag[] {
  return [
    { key: "projekte", label: tr("layout.projekte"), icon: "🗂️", to: "/" },
    { key: "inbox", label: tr("layout.inbox"), icon: "📥", to: "/inbox", zaehler: "inbox" },
    { key: "buero", label: tr("layout.buero_2"), icon: "🏢", to: "/buero" },
    { key: "prozesse", label: tr("layout.prozesse"), icon: "🔀", to: "/processes" },
    { key: "einstellungen", label: tr("layout.einstellungen"), icon: "⚙️", to: "/settings" },
    ...(istAdmin ? [{ key: "admin", label: tr("layout.admin"), icon: "🛠️", to: "/admin" }] : []),
  ];
}

/**
 * Is this area the current one?
 *
 * Over the prefix, not over equality: `/settings/jobs` and `/projects/UNI` belong to their
 * area just as much as the bare path. The start page is the exception, otherwise it would
 * be active everywhere.
 */
export function istBereich(pfad: string, to: string): boolean {
  if (to === "/") return pfad === "/" || pfad.startsWith("/projects");
  return pfad === to || pfad.startsWith(to + "/");
}
