import type { ReactNode } from 'react';

export type AlertTone = 'info' | 'success' | 'warning' | 'error';

/** Message d'information, de succès, d'avertissement ou d'erreur. */
export function Notice({
  tone = 'info',
  title,
  children,
}: {
  tone?: AlertTone;
  title?: string;
  children?: ReactNode;
}): React.ReactElement {
  return (
    <div className={`alert alert--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      {title ? <p className="alert__title">{title}</p> : null}
      {children ? <div className="alert__body">{children}</div> : null}
    </div>
  );
}

/**
 * Message d'erreur applicatif. Le texte provient du champ `error.message` du
 * backend (docs/API.md §2.3) ou du message réseau explicite du client HTTP.
 */
export function ErrorAlert({
  message,
  onRetry,
  retryLabel = 'Réessayer',
}: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}): React.ReactElement {
  return (
    <div className="alert alert--error" role="alert">
      <p className="alert__title">Erreur</p>
      <p className="alert__body">{message}</p>
      {onRetry ? (
        <button type="button" className="btn btn--ghost btn--sm" onClick={onRetry}>
          {retryLabel}
        </button>
      ) : null}
    </div>
  );
}
