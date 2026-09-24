/**
 * Répartition des tâches par état (docs/ARCHITECTURE.md §10).
 * Chaque total provient du champ `total` renvoyé par `GET /api/v1/tasks`
 * filtré sur l'état correspondant : aucune valeur n'est estimée localement.
 */

import { StatCard } from '../ui/StatCard';
import { describeTaskStatus } from '../../utils/display';

export interface TaskStatusCount {
  status: string;
  total: number;
}

export function TaskStatusStats({
  counts,
  loading,
}: {
  counts: TaskStatusCount[];
  loading: boolean;
}): React.ReactElement {
  return (
    <section className="card" aria-labelledby="taches-par-etat">
      <header className="card__header">
        <h2 className="card__title" id="taches-par-etat">
          Tâches par état
        </h2>
      </header>
      <div className="stat-grid">
        {counts.map((entry) => {
          const descriptor = describeTaskStatus(entry.status);
          return (
            <StatCard
              key={entry.status}
              label={descriptor.label}
              hint={entry.status}
              value={entry.total}
              tone={descriptor.tone}
              loading={loading}
            />
          );
        })}
      </div>
    </section>
  );
}
