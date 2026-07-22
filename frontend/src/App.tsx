import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Login from "./pages/Login";
import AcceptInvite from "./pages/AcceptInvite";
import Projects from "./pages/Projects";
import ProjectView from "./pages/ProjectView";
import Settings from "./pages/Settings";
import Admin from "./pages/Admin";
import Layout from "./components/Layout";

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
    <Layout>
      <Routes>
        <Route path="/" element={<Projects />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/projects/:key" element={<ProjectView />} />
        <Route path="/accept-invite" element={<AcceptInvite />} />
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
