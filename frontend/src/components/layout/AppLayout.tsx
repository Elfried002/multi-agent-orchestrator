/**
 * Structure d'administration : barre supérieure avec identité de session,
 * navigation latérale (repliée en tiroir sous 900 px) et zone de contenu.
 */

import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAction } from '../../hooks/useAction';
import { useSession } from '../../hooks/useSession';
import { ErrorAlert } from '../ui/Alert';

const NAV_ITEMS: ReadonlyArray<{ to: string; label: string }> = [
  { to: '/dashboard', label: 'Tableau de bord' },
  { to: '/agents', label: 'Agents' },
  { to: '/tasks', label: 'Tâches' },
  { to: '/logs', label: 'Journaux' },
  { to: '/security', label: 'Sécurité' },
  { to: '/settings', label: 'Paramètres' },
];

export function AppLayout(): React.ReactElement {
  const { admin, signOut } = useSession();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState<boolean>(false);
  const { run: runSignOut, busy: signingOut, error: signOutError } = useAction(signOut);

  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  return (
    <div className="layout">
      <a className="skip-link" href="#contenu-principal">
        Aller au contenu principal
      </a>

      <header className="topbar">
        <button
          type="button"
          className="btn btn--ghost btn--sm topbar__menu"
          aria-expanded={menuOpen}
          aria-controls="navigation-principale"
          onClick={() => setMenuOpen((value) => !value)}
        >
          {menuOpen ? 'Fermer le menu' : 'Ouvrir le menu'}
        </button>

        <p className="topbar__brand">
          <img
            className="topbar__logo"
            src="/logo-embleme-96.png"
            alt=""
            width={36}
            height={36}
            aria-hidden="true"
          />
          <span className="topbar__brand-texte">
            Multi-Agent Orchestrator
            <span className="topbar__brand-sub">Console d'administration</span>
          </span>
        </p>

        <div className="topbar__session">
          {admin ? (
            <p className="topbar__user">
              Connecté : <strong>{admin.username}</strong>
              {!admin.is_active ? ' (compte inactif)' : ''}
            </p>
          ) : null}
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => void runSignOut()}
            disabled={signingOut}
          >
            {signingOut ? 'Déconnexion…' : 'Se déconnecter'}
          </button>
        </div>
      </header>

      <div className="layout__body">
        <nav
          id="navigation-principale"
          className={menuOpen ? 'sidebar sidebar--open' : 'sidebar'}
          aria-label="Navigation principale"
        >
          <p className="sidebar__brand">
            <img
              src="/logo-embleme-96.png"
              alt=""
              aria-hidden="true"
              width={30}
              height={30}
            />
            <span className="sidebar__brand-texte">
              Multi-Agent
              <span className="sidebar__brand-suite">Orchestrator</span>
            </span>
          </p>

          <ul className="sidebar__list">
            {NAV_ITEMS.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  className={({ isActive }) =>
                    isActive ? 'sidebar__link sidebar__link--active' : 'sidebar__link'
                  }
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
          <p className="sidebar__note">
            Toutes les données affichées proviennent de l'API du backend. Les autorisations sont
            vérifiées côté serveur.
          </p>
        </nav>

        <main className="content" id="contenu-principal">
          {signOutError ? <ErrorAlert message={signOutError} /> : null}
          <Outlet />
        </main>
      </div>
    </div>
  );
}
