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
export const RAIL_WIDTH = "w-[76px]";
export const RAIL_LEAVEBLANK = "md:left-[76px]";

export type NavEntry = {
  key: string;
  label: string;
  icon: string;
  to: string;
  /** Counter of what waits there: the assistant inbox, or unread mail across all accounts. */
  counter?: "inbox" | "mail";
};

export function primaryNavigation(isAdmin: boolean, plugins: NavEntry[] = []): NavEntry[] {
  return [
    { key: "projekte", label: tr("layout.projekte"), icon: "🗂️", to: "/" },
    { key: "inbox", label: tr("layout.inbox"), icon: "📥", to: "/inbox", counter: "inbox" },
    { key: "mail", label: "Mail", icon: "✉️", to: "/mail", counter: "mail" },
    { key: "buero", label: tr("layout.buero_2"), icon: "🏢", to: "/office" },
    { key: "prozesse", label: tr("layout.prozesse"), icon: "🔀", to: "/processes" },
    // Plugins stehen vor den Einstellungen: Sie sind Bereiche wie die anderen, und die
    // Einstellungen sollen der letzte Punkt vor der Verwaltung bleiben.
    ...plugins,
    { key: "einstellungen", label: tr("layout.einstellungen"), icon: "⚙️", to: "/settings" },
    ...(isAdmin ? [{ key: "admin", label: tr("layout.admin"), icon: "🛠️", to: "/admin" }] : []),
  ];
}

/**
 * Is this area the current one?
 *
 * Over the prefix, not over equality: `/settings/jobs` and `/projects/UNI` belong to their
 * area just as much as the bare path. The start page is the exception, otherwise it would
 * be active everywhere.
 */
export function isArea(path: string, to: string): boolean {
  if (to === "/") return path === "/" || path.startsWith("/projects");
  return path === to || path.startsWith(to + "/");
}
