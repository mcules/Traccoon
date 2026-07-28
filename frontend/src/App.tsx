import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Login from "./pages/Login";
import AcceptInvite from "./pages/AcceptInvite";
import Projects from "./pages/Projects";
import ProjectView from "./pages/ProjectView";
import TicketView from "./pages/TicketView";
import WorkflowEditor from "./pages/WorkflowEditor";
import Processes from "./pages/Processes";
import Settings from "./pages/Settings";
import Admin from "./pages/Admin";
import Profile from "./pages/Profile";
import Inbox from "./pages/Inbox";
import Layout from "./components/Layout";
import { PageChromeProvider } from "./pageChrome";

export default function App() {
  const { user, loading } = useAuth();

  if (loading) return <div className="p-8 text-muted">Lädt…</div>;
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
        <Route path="/profil" element={<Profile />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/settings/:tab" element={<Settings />} />
        <Route path="/processes" element={<Processes />} />
        <Route path="/processes/:tab" element={<Processes />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/admin/:tab" element={<Admin />} />
        <Route path="/projects/:key/workflows/:id" element={<WorkflowEditor />} />
        {/* Vorlagen eines Prozess-Satzes gehören zu keinem Projekt. */}
        <Route path="/workflows/:id" element={<WorkflowEditor />} />
        <Route path="/projects/:key/tickets/:ticketKey" element={<TicketView />} />
        <Route path="/projects/:key" element={<ProjectView />} />
        <Route path="/accept-invite" element={<AcceptInvite />} />
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </PageChromeProvider>
  );
}
