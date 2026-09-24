/**
 * Carte d'état de l'orchestrateur (docs/API.md §9.1 et §4.1).
 * Affiche l'état logique souhaité, l'état du service, l'état de santé du
 * backend ainsi que les informations de la route publique `GET /health`.
 */

import { Link } from 'react-router-dom';
import type { HealthResponse, OrchestratorStatus } from '../../types';
import {
  describeHealthStatus,
  describeOrchestratorState,
  describeServiceStatus,
} from '../../utils/display';
import { formatText } from '../../utils/format';
import { ErrorAlert } from '../ui/Alert';
import { DetailList } from '../ui/DetailList';
import { LoadingState } from '../ui/LoadingState';
import { StatusBadge } from '../ui/StatusBadge';

export interface OrchestratorStateCardProps {
  status: OrchestratorStatus | null;
  health: HealthResponse | null;
  healthError: string | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

export function OrchestratorStateCard({
  status,
  health,
  healthError,
  loading,
  error,
  onRetry,
}: OrchestratorStateCardProps): React.ReactElement {
  const desired = describeOrchestratorState(status?.desired_state);

  return (
    <section className="card" aria-labelledby="etat-orchestrateur">
      <header className="card__header">
        <h2 className="card__title" id="etat-orchestrateur">
          État de l'orchestrateur
        </h2>
        <Link className="link" to="/settings">
          Gérer dans les paramètres
        </Link>
      </header>

      {loading && !status ? <LoadingState label="Lecture de l'état de l'orchestrateur…" /> : null}
      {error ? <ErrorAlert message={error} onRetry={onRetry} /> : null}

      {status ? (
        <>
          <p className="card__lead">
            État logique souhaité : <StatusBadge label={desired.label} tone={desired.tone} />
          </p>
          <DetailList
            items={[
              {
                label: 'État souhaité (desired_state)',
                value: (
                  <StatusBadge
                    label={describeOrchestratorState(status.desired_state).label}
                    tone={describeOrchestratorState(status.desired_state).tone}
                  />
                ),
              },
              {
                label: 'État du service (service_status)',
                value: (
                  <StatusBadge
                    label={describeServiceStatus(status.service_status).label}
                    tone={describeServiceStatus(status.service_status).tone}
                  />
                ),
              },
              {
                label: 'Santé du backend (health_status)',
                value: (
                  <StatusBadge
                    label={describeHealthStatus(status.health_status).label}
                    tone={describeHealthStatus(status.health_status).tone}
                  />
                ),
              },
              {
                label: 'Route publique /health',
                value: health ? (
                  <>
                    <StatusBadge
                      label={health.status === 'ok' ? 'OK' : health.status}
                      tone={health.status === 'ok' ? 'success' : 'warning'}
                    />
                    <span className="details__inline">
                      {formatText(health.service)} — version {formatText(health.version)}
                    </span>
                  </>
                ) : healthError ? (
                  <span className="text-muted">{healthError}</span>
                ) : (
                  <span className="text-muted">Non interrogée.</span>
                ),
              },
            ]}
          />
        </>
      ) : null}
    </section>
  );
}
