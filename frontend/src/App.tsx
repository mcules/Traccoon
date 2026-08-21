import { Suspense, lazy } from "react";
import { tr } from "./i18n";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { useAuth } from "./auth";
import { useLanguage, useLanguageFromUser } from "./i18n/useLanguage";
import Login from "./pages/Login";
import AcceptInvite from "./pages/AcceptInvite";
import Projects from "./pages/Projects";
import ProjectView from "./pages/ProjectView";
import TicketView from "./pages/TicketView";
import WorkflowEditor from "./pages/WorkflowEditor";
import Processes from "./pages/Processes";
import StorePage from "./pages/Store";
import Settings from "./pages/Settings";
import Admin from "./pages/Admin";
import Account from "./pages/Account";
import Inbox from "./pages/Inbox";
import Mail from "./pages/Mail";
import PluginHost from "./pages/PluginHost";
// Canvas, pixel world and engine of the office are a chunk of their own. Imported
// statically it would lie in the main bundle and the split in ProjectView would be in vain.
const Office = lazy(() => import("./pages/Office"));
import Layout from "./components/Layout";
import { PageChromeProvider } from "./pageChrome";

/**
 * Addresses of the old shape, kept alive.
 *
 * `/profil` and `/settings/prefs` have become `/account`, the own flows have moved from the
 * settings to `/processes/own`, and the destinations of the administration are a scope
 * switch under the settings now. Every one of these paths stands in bookmarks, in tickets
 * and in the vault.
 */
function OldAddress({ to }: { to: string }) {
  return <Navigate to={to} replace />;
}

/**
 * Addresses are English — the sections in them too.
 *
 * Gewachsen war beides gemischt: `/account/meldungen` neben `/settings/webhooks`, im
 * a project `arbeit` next to `board`. Whoever reads or types an address should not have to
 * guess which language this particular section is meant in. The old ones redirect, because they
 * stand in bookmarks, in tickets and in the vault.
 */
function AlterSection({ karte, target: target }: { karte: Record<string, string>; target: string }) {
  const params = useParams();
  // A section that was English already (person, mail) goes along unchanged — otherwise
  // landete `/account/person` im Nichts statt auf `/account/person`.
  const section = karte[params.tab || ""] || params.tab || "";
  return <Navigate to={section ? `${target}/${section}` : target} replace />;
}

/** `/settings/prefs` split in two: what belongs to the person went to the account. */
function SettingsTab() {
  const { tab } = useParams();
  if (tab === "prefs") return <Navigate to="/account/agents" replace />;
  if (tab === "processes") return <Navigate to="/processes/own" replace />;
  return <Settings />;
}

function AdminTab() {
  const { tab } = useParams();
  if (tab === "destinations") return <Navigate to="/settings/destinations" replace />;
  return <Admin />;
}

const ACCOUNT_OLD = { view: "appearance", reports: "notifications", agents: "agents" };
const FLOWS_OLD = { own: "own", standard: "default", operation: "operations",
                       trigger: "triggers", metricseries: "metrics", stores: "documents" };

function AccountPage() {
  const { tab } = useParams();
  return tab && tab in ACCOUNT_OLD
    ? <AlterSection karte={ACCOUNT_OLD} target="/account" /> : <Account />;
}

function FlowsPage() {
  const { tab } = useParams();
  return tab && tab in FLOWS_OLD
    ? <AlterSection karte={FLOWS_OLD} target="/processes" /> : <Processes />;
}

export default function App() {
  const { user, loading } = useAuth();
  // The language hangs off the logged-in human; without a login the browser decides.
  useLanguageFromUser(user?.locale);
  useLanguage();

  if (loading) return <div className="p-8 text-muted">{tr("common.loading")}</div>;
  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/accept-invite" element={<AcceptInvite />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <PageChromeProvider>
      <Layout>
        <Routes>
        <Route path="/" element={<Projects />} />
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/mail" element={<Mail />} />
        <Route path="/account" element={<Account />} />
        <Route path="/account/:tab" element={<AccountPage />} />
        <Route path="/konto" element={<OldAddress to="/account" />} />
        <Route path="/account/:tab" element={<AlterSection karte={ACCOUNT_OLD} target="/account" />} />
        <Route path="/profil" element={<OldAddress to="/account" />} />
        {/* Plugins liegen unter einem eigenen kurzen Praefix — sie sind Bereiche,
            but no built-in ones. */}
        <Route path="/p/:slug" element={<PluginHost />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/settings/:tab" element={<SettingsTab />} />
        <Route path="/documents/:key" element={<StorePage />} />
        <Route path="/documents/:key/:id" element={<StorePage />} />
        <Route path="/processes" element={<Processes />} />
        <Route path="/processes/:tab" element={<FlowsPage />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/admin/:tab" element={<AdminTab />} />
        <Route path="/projects/:key/workflows/:id" element={<WorkflowEditor />} />
        {/* Vorlagen eines Prozess-Satzes gehören zu keinem Projekt. */}
        <Route path="/workflows/:id" element={<WorkflowEditor />} />
        <Route path="/projects/:key/tickets/:ticketKey" element={<TicketView />} />
        {/* Area and view stand in the path (`/projects/UNI/operations/office`); the old
            `?tab=` ProjectView redirects itself. */}
        <Route path="/projects/:key" element={<ProjectView />} />
        <Route path="/projects/:key/:tab" element={<ProjectView />} />
        <Route path="/projects/:key/:tab/:unter" element={<ProjectView />} />
        <Route path="/buero" element={<OldAddress to="/office" />} />
        <Route path="/office" element={
          <Suspense fallback={<div className="p-4 text-sm text-muted">{tr("common.loading")}</div>}>
            <Office />
          </Suspense>
        } />
        <Route path="/accept-invite" element={<AcceptInvite />} />
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </PageChromeProvider>
  );
}
