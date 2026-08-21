import { Suspense, lazy } from "react";
import { tr } from "./i18n";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { useAuth } from "./auth";
import { useLanguage, useLanguageFromUser } from "./i18n/useSprache";
import Login from "./pages/Login";
import AcceptInvite from "./pages/AcceptInvite";
import Projects from "./pages/Projects";
import ProjectView from "./pages/ProjectView";
import TicketView from "./pages/TicketView";
import WorkflowEditor from "./pages/WorkflowEditor";
import Processes from "./pages/Processes";
import StorePage from "./pages/Ablage";
import Settings from "./pages/Settings";
import Admin from "./pages/Admin";
import Account from "./pages/Konto";
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
 * Adressen sind englisch — auch die Abschnitte darin.
 *
 * Gewachsen war beides gemischt: `/account/meldungen` neben `/settings/webhooks`, im
 * Projekt `arbeit` neben `board`. Wer eine Adresse liest oder tippt, soll nicht raten
 * müssen, in welcher Sprache dieser eine Abschnitt gemeint ist. Die alten leiten weiter,
 * denn sie stehen in Lesezeichen, in Tickets und im Vault.
 */
function AlterSection({ karte, target: target }: { karte: Record<string, string>; target: string }) {
  const params = useParams();
  // Ein Abschnitt, der schon englisch hieß (person, mail), geht unverändert mit — sonst
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

  if (loading) return <div className="p-8 text-muted">{tr("common.laedt")}</div>;
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
            aber keine eingebauten. */}
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
        {/* Bereich und Ansicht stehen im Pfad (`/projects/UNI/betrieb/buero`); das alte
            `?tab=` leitet ProjectView selbst um. */}
        <Route path="/projects/:key" element={<ProjectView />} />
        <Route path="/projects/:key/:tab" element={<ProjectView />} />
        <Route path="/projects/:key/:tab/:unter" element={<ProjectView />} />
        <Route path="/buero" element={<OldAddress to="/office" />} />
        <Route path="/office" element={
          <Suspense fallback={<div className="p-4 text-sm text-muted">{tr("common.laedt")}</div>}>
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
