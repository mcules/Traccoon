import { Suspense, lazy } from "react";
import { tr } from "./i18n";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { useAuth } from "./auth";
import { useSprache, useSpracheVonNutzer } from "./i18n/useSprache";
import Login from "./pages/Login";
import AcceptInvite from "./pages/AcceptInvite";
import Projects from "./pages/Projects";
import ProjectView from "./pages/ProjectView";
import TicketView from "./pages/TicketView";
import WorkflowEditor from "./pages/WorkflowEditor";
import Processes from "./pages/Processes";
import Settings from "./pages/Settings";
import Admin from "./pages/Admin";
import Konto from "./pages/Konto";
import Inbox from "./pages/Inbox";
// Canvas, pixel world and engine of the office are a chunk of their own. Imported
// statically it would lie in the main bundle and the split in ProjectView would be in vain.
const Office = lazy(() => import("./pages/Office"));
import Layout from "./components/Layout";
import { PageChromeProvider } from "./pageChrome";

/**
 * Addresses of the old shape, kept alive.
 *
 * `/profil` and `/settings/prefs` have become `/konto`, the own flows have moved from the
 * settings to `/processes/eigene`, and the destinations of the administration are a scope
 * switch under the settings now. Every one of these paths stands in bookmarks, in tickets
 * and in the vault.
 */
function AlteAdresse({ to }: { to: string }) {
  return <Navigate to={to} replace />;
}

/** `/settings/prefs` split in two: what belongs to the person went to the account. */
function EinstellungenTab() {
  const { tab } = useParams();
  if (tab === "prefs") return <Navigate to="/konto/agenten" replace />;
  if (tab === "processes") return <Navigate to="/processes/eigene" replace />;
  return <Settings />;
}

function AdminTab() {
  const { tab } = useParams();
  if (tab === "destinations") return <Navigate to="/settings/destinations" replace />;
  return <Admin />;
}

export default function App() {
  const { user, loading } = useAuth();
  // The language hangs off the logged-in human; without a login the browser decides.
  useSpracheVonNutzer(user?.locale);
  useSprache();

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
        <Route path="/konto" element={<Konto />} />
        <Route path="/konto/:tab" element={<Konto />} />
        <Route path="/profil" element={<AlteAdresse to="/konto" />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/settings/:tab" element={<EinstellungenTab />} />
        <Route path="/processes" element={<Processes />} />
        <Route path="/processes/:tab" element={<Processes />} />
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
        <Route path="/buero" element={
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
