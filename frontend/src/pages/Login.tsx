/**
 * Connexion administrateur (docs/API.md §5.1).
 * Le formulaire appelle `POST /api/v1/auth/login` ; la session est matérialisée
 * par un cookie HttpOnly posé par le backend, puis confirmée par
 * `GET /api/v1/auth/me`. Aucun secret n'est conservé dans le navigateur.
 */

import { useState, type FormEvent } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { FullPageLoading, ServerUnavailable } from '../components/layout/SessionGate';
import { ErrorAlert } from '../components/ui/Alert';
import { Field } from '../components/ui/Field';
import { useAction } from '../hooks/useAction';
import { useSession } from '../hooks/useSession';

interface LocationState {
  from?: string;
}

export function Login(): React.ReactElement {
  const { status, error: sessionError, refresh, signIn } = useSession();
  const location = useLocation();

  const [username, setUsername] = useState<string>('');
  const [password, setPassword] = useState<string>('');
  const [validationError, setValidationError] = useState<string | null>(null);

  const action = useAction(async (user: string, secret: string) => {
    await signIn(user, secret);
  });

  const state = location.state as LocationState | null;
  const redirectTo = state?.from && state.from.length > 0 ? state.from : '/dashboard';

  if (status === 'loading') return <FullPageLoading />;
  if (status === 'unavailable') {
    return (
      <ServerUnavailable
        message={sessionError ?? 'Le backend ne répond pas.'}
        onRetry={() => void refresh()}
      />
    );
  }
  if (status === 'authenticated') return <Navigate to={redirectTo} replace />;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setValidationError(null);

    if (username.trim().length === 0 || password.length === 0) {
      setValidationError("Le nom d'utilisateur et le mot de passe sont obligatoires.");
      return;
    }

    await action.run(username.trim(), password);
    setPassword('');
  };

  return (
    <div className="gate">
      <main className="gate__panel gate__panel--login">
        <header className="login__header">
          <h1 className="sr-only">Multi-Agent Orchestrator</h1>
          <p className="login__logo">
            {/* Le logo porte lui-même le nom : il est décoratif ici, le titre
                accessible étant le h1 ci-dessus. La variante « sombre » est
                utilisée car le fichier d'origine a un texte bleu nuit, illisible
                sur cette interface. */}
            <img
              src="/logo-complet-sombre.png"
              alt=""
              aria-hidden="true"
              width={627}
              height={627}
            />
          </p>
          <p className="text-muted">Connexion à la console d'administration.</p>
        </header>

        {action.error ? <ErrorAlert message={action.error} /> : null}
        {sessionError ? <ErrorAlert message={sessionError} /> : null}

        <form
          className="form"
          onSubmit={(event) => void handleSubmit(event)}
          aria-label="Formulaire de connexion administrateur"
        >
          <Field label="Nom d'utilisateur" htmlFor="login-username" required>
            <input
              id="login-username"
              name="username"
              className="input"
              type="text"
              autoComplete="username"
              autoFocus
              value={username}
              required
              onChange={(event) => setUsername(event.target.value)}
            />
          </Field>

          <Field label="Mot de passe" htmlFor="login-password" required>
            <input
              id="login-password"
              name="password"
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              required
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>

          {validationError ? (
            <p className="alert alert--error" role="alert">
              {validationError}
            </p>
          ) : null}

          <button type="submit" className="btn btn--primary btn--block" disabled={action.busy}>
            {action.busy ? 'Connexion…' : 'Se connecter'}
          </button>
        </form>

        <p className="text-muted text-small">
          La session est créée côté serveur et portée par un cookie HttpOnly : aucun jeton n'est
          enregistré dans le navigateur.
        </p>
      </main>
    </div>
  );
}
