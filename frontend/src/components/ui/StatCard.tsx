import type { Tone } from '../../utils/display';

export interface StatCardProps {
  label: string;
  value: number | string;
  hint?: string;
  tone?: Tone;
  /** Donnée en cours de chargement : affiche « … » sans figer une valeur. */
  loading?: boolean;
}

/** Carte de statistique du tableau de bord. */
export function StatCard({
  label,
  value,
  hint,
  tone = 'neutral',
  loading = false,
}: StatCardProps): React.ReactElement {
  return (
    <article className={`stat stat--${tone}`} aria-busy={loading}>
      <p className="stat__label">{label}</p>
      <p className="stat__value">{loading ? '…' : value}</p>
      {hint ? <p className="stat__hint">{hint}</p> : null}
    </article>
  );
}
