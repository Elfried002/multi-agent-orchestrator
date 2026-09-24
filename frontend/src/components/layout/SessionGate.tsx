/**
 * Écrans d'attente et d'indisponibilité du backend, utilisés avant tout rendu
 * de page protégée. La distinction entre « non authentifié » (401) et
 * « backend injoignable » évite de masquer une panne réseau derrière un
 * formulaire de connexion.
 */

import { ErrorAlert } from '../ui/Alert';
import { LoadingState } from '../ui/LoadingState';

export function FullPageLoading({
  label = 'Vérification de la session administrateur…',
}: {
  label?: string;
}): React.ReactElement {
  return (
    <div className="gate">
      <div className="gate__panel">
        <LoadingState label={label} />
      </div>
    </div>
  );
}

export function ServerUnavailable({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}): React.ReactElement {
  return (
    <div className="gate">
      <div className="gate__panel">
        <img
          className="gate__logo"
          src="/logo-embleme-96.png"
          alt=""
          aria-hidden="true"
          width={72}
          height={72}
        />
        <h1 className="gate__title">Backend injoignable</h1>
        <p className="text-muted">
          L'interface d'administration n'a pas pu joindre l'API. Vérifiez que le service est
          démarré (par défaut : http://127.0.0.1:8000) puis réessayez.
        </p>
        <ErrorAlert message={message} onRetry={onRetry} retryLabel="Réessayer la connexion" />
      </div>
    </div>
  );
}
