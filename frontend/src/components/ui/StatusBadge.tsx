import type { Tone } from '../../utils/display';

/** Badge d'état : libellé textuel + tonalité, jamais une couleur seule. */
export function StatusBadge({
  label,
  tone = 'neutral',
}: {
  label: string;
  tone?: Tone;
}): React.ReactElement {
  return <span className={`badge badge--${tone}`}>{label}</span>;
}
