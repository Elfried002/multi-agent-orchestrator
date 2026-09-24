/**
 * Routage de l'application et protection des routes.
 *
 * Toute route d'administration est placée derrière `RequireSession`, qui
 * s'appuie exclusivement sur l'état de session obtenu via `GET /api/v1/auth/me` :
 * - 401 → redirection vers /login en mémorisant la page demandée ;
 * - backend injoignable → écran d'erreur avec nouvelle tentative ;
 * - session valide → rendu de la page.
 * L'autorisation réelle reste vérifiée par le backend (docs/ARCHITECTURE.md §3.2).
 */

import { Link, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { AppLayout } from './components/layout/AppLayout';
import { FullPageLoading, ServerUnavailable } from './components/layout/SessionGate';
import { useSession } from './hooks/useSession';
import { Agents } from './pages/Agents';
import { Dashboard } from './pages/Dashboard';
import { Login } from './pages/Login';
import { Logs } from './pages/Logs';
import { Security } from './pages/Security';
import { Settings } from './pages/Settings';
import { Tasks } from './pages/Tasks';

function RequireSession(): React.ReactElement {
  const { status, error, refresh } = useSession();
  const location = useLocation();

  if (status === 'loading') return <FullPageLoading />;
  if (status === 'unavailable') {
    return (
      <ServerUnavailable
        message={error ?? 'Le backend ne répond pas.'}
        onRetry={() => void refresh()}
      />
    );
  }
  if (status === 'anonymous') {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}

function NotFound(): React.ReactElement {
  return (
    <section className="card">
      <h1 className="card__title">Page introuvable</h1>
      <p className="text-muted">
        L'adresse demandée ne correspond à aucune page de la console d'administration.
      </p>
      <p>
        <Link className="link" to="/dashboard">
          Revenir au tableau de bord
        </Link>
      </p>
    </section>
  );
}

export function App(): React.ReactElement {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireSession />}>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/security" element={<Security />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Route>
    </Routes>
  );
}
