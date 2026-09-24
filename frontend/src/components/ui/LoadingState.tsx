/**
 * Indicateurs de chargement.
 * Tous portent `role="status"` et `aria-live="polite"` : l'état est annoncé aux
 * technologies d'assistance sans interrompre la lecture en cours.
 */

export interface LoadingStateProps {
  label?: string;
  hint?: string;
}

/** Bloc de chargement occupant la zone de contenu. */
export function LoadingState({
  label = 'Chargement des données…',
  hint,
}: LoadingStateProps): React.ReactElement {
  return (
    <div className="state state--loading" role="status" aria-live="polite">
      <span className="spinner__ring" aria-hidden="true" />
      <p className="state__title">{label}</p>
      {hint ? <p className="state__hint">{hint}</p> : null}
    </div>
  );
}

/** Indicateur discret, utilisé lors d'une actualisation en arrière-plan. */
export function InlineSpinner({ label = 'Actualisation…' }: { label?: string }): React.ReactElement {
  return (
    <span className="spinner spinner--inline" role="status" aria-live="polite">
      <span className="spinner__ring spinner__ring--sm" aria-hidden="true" />
      <span className="spinner__label">{label}</span>
    </span>
  );
}
