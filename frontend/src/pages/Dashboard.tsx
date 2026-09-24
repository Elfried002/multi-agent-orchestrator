/**
 * Tableau de bord : état de l'orchestrateur, effectifs d'agents, répartition des
 * tâches par état, alertes de sécurité et événements récents.
 *
 * Toutes les valeurs affichées proviennent d'appels réels :
 * - `GET /api/v1/settings/orchestrator` et `GET /health` pour l'état du service ;
 * - `GET /api/v1/agents` (total et par état via le champ `total`) ;
 * - `GET /api/v1/tasks` filtré par état (champ `total`) ;
 * - `GET /api/v1/logs` pour les événements récents et les alertes.
 */

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { OrchestratorStateCard } from '../components/dashboard/OrchestratorStateCard';
import { TaskStatusStats } from '../components/dashboard/TaskStatusStats';
import { EventTable } from '../components/logs/EventTable';
import { EventDetailModal } from '../components/logs/EventDetailModal';
import { ErrorAlert } from '../components/ui/Alert';
import { LoadingState } from '../components/ui/LoadingState';
import { PageHeader } from '../components/ui/PageHeader';
import { StatCard } from '../components/ui/StatCard';
import { useAsyncData } from '../hooks/useAsyncData';
import { agentsApi, healthApi, logsApi, settingsApi, tasksApi } from '../services/api';
import type { EventItem, HealthResponse } from '../types';
import { describeError } from '../utils/errors';

const TASK_OVERVIEW_STATUSES = ['PENDING', 'ASSIGNED', 'RUNNING', 'COMPLETED', 'FAILED'] as const;

export function Dashboard(): React.ReactElement {
  const [nonce, setNonce] = useState<number>(0);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);

  const overview = useAsyncData(`dashboard:${nonce}`, async (signal) => {
    const [
      orchestrator,
      agentsAll,
      agentsOnline,
      agentsOffline,
      pending,
      assigned,
      running,
      completed,
      failed,
      criticalEvents,
      errorEvents,
      recentEvents,
    ] = await Promise.all([
      settingsApi.getOrchestrator(signal),
      agentsApi.list({ page: 1, page_size: 1 }, signal),
      agentsApi.list({ status: 'ONLINE', page: 1, page_size: 1 }, signal),
      agentsApi.list({ status: 'OFFLINE', page: 1, page_size: 1 }, signal),
      tasksApi.list({ status: 'PENDING', page: 1, page_size: 1 }, signal),
      tasksApi.list({ status: 'ASSIGNED', page: 1, page_size: 1 }, signal),
      tasksApi.list({ status: 'RUNNING', page: 1, page_size: 1 }, signal),
      tasksApi.list({ status: 'COMPLETED', page: 1, page_size: 1 }, signal),
      tasksApi.list({ status: 'FAILED', page: 1, page_size: 1 }, signal),
      logsApi.list({ severity: 'CRITICAL', page: 1, page_size: 5 }, signal),
      logsApi.list({ severity: 'ERROR', page: 1, page_size: 1 }, signal),
      logsApi.list({ page: 1, page_size: 8 }, signal),
    ]);

    const health = await healthApi.getHealth(signal).then(
      (value: HealthResponse): { value: HealthResponse | null; error: string | null } => ({
        value,
        error: null,
      }),
      (caught: unknown): { value: HealthResponse | null; error: string | null } => ({
        value: null,
        error: describeError(caught),
      }),
    );

    return {
      orchestrator,
      health,
      agents: {
        total: agentsAll.total,
        online: agentsOnline.total,
        offline: agentsOffline.total,
      },
      taskTotals: {
        PENDING: pending.total,
        ASSIGNED: assigned.total,
        RUNNING: running.total,
        COMPLETED: completed.total,
        FAILED: failed.total,
      },
      criticalEvents,
      errorEventsTotal: errorEvents.total,
      recentEvents,
    };
  });

  const data = overview.data;

  return (
    <>
      <PageHeader
        title="Tableau de bord"
        description="Vue globale de l'orchestrateur : état logique, agents, tâches et sécurité."
        actions={
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => setNonce((value) => value + 1)}
            disabled={overview.loading}
          >
            {overview.loading ? 'Actualisation…' : 'Rafraîchir'}
          </button>
        }
      />

      {overview.error ? <ErrorAlert message={overview.error} onRetry={overview.reload} /> : null}
      {overview.loading && !data ? <LoadingState label="Chargement du tableau de bord…" /> : null}

      {data ? (
        <div className="stack">
          <OrchestratorStateCard
            status={data.orchestrator}
            health={data.health.value}
            healthError={data.health.error}
            loading={overview.loading}
            error={null}
            onRetry={overview.reload}
          />

          <section className="card" aria-labelledby="effectif-agents">
            <header className="card__header">
              <h2 className="card__title" id="effectif-agents">
                Agents
              </h2>
              <Link className="link" to="/agents">
                Consulter la liste des agents
              </Link>
            </header>
            <div className="stat-grid">
              <StatCard
                label="Agents enregistrés"
                value={data.agents.total}
                loading={overview.loading}
                tone="info"
              />
              <StatCard
                label="Agents en ligne"
                value={data.agents.online}
                loading={overview.loading}
                tone="success"
              />
              <StatCard
                label="Agents hors ligne"
                value={data.agents.offline}
                loading={overview.loading}
                tone="neutral"
              />
            </div>
          </section>

          <TaskStatusStats
            counts={TASK_OVERVIEW_STATUSES.map((status) => ({
              status,
              total: data.taskTotals[status],
            }))}
            loading={overview.loading}
          />

          <section className="card" aria-labelledby="alertes-securite">
            <header className="card__header">
              <h2 className="card__title" id="alertes-securite">
                Alertes de sécurité
              </h2>
              <Link className="link" to="/security">
                Ouvrir la page Sécurité
              </Link>
            </header>
            <div className="stat-grid">
              <StatCard
                label="Événements critiques"
                value={data.criticalEvents.total}
                loading={overview.loading}
                tone="danger"
                hint="Gravité CRITICAL"
              />
              <StatCard
                label="Erreurs journalisées"
                value={data.errorEventsTotal}
                loading={overview.loading}
                tone="warning"
                hint="Gravité ERROR"
              />
            </div>
            {data.criticalEvents.total > 0 ? (
              <EventTable
                events={data.criticalEvents.items}
                onSelect={(event: EventItem) => setSelectedEventId(event.id)}
                loading={overview.loading}
                refreshing={overview.loading && data.criticalEvents.items.length > 0}
                caption="Derniers événements critiques"
                emptyTitle="Aucun événement critique"
              />
            ) : (
              <p className="text-muted">
                Aucun événement de gravité CRITICAL n'est journalisé. Aucune alerte en cours.
              </p>
            )}
          </section>

          <section className="card" aria-labelledby="evenements-recents">
            <header className="card__header">
              <h2 className="card__title" id="evenements-recents">
                Événements récents
              </h2>
              <Link className="link" to="/logs">
                Consulter tous les journaux
              </Link>
            </header>
            <EventTable
              events={data.recentEvents.items}
              onSelect={(event: EventItem) => setSelectedEventId(event.id)}
              loading={overview.loading}
              refreshing={overview.loading && data.recentEvents.items.length > 0}
              caption="Derniers événements journalisés"
              emptyTitle="Aucun événement journalisé"
              emptyDescription="Les journaux d'audit apparaîtront ici dès le premier événement enregistré."
            />
          </section>
        </div>
      ) : null}

      {selectedEventId ? (
        <EventDetailModal eventId={selectedEventId} onClose={() => setSelectedEventId(null)} />
      ) : null}
    </>
  );
}
