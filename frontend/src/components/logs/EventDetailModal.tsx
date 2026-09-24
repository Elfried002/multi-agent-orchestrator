/**
 * Détail d'un événement : relecture serveur via `GET /api/v1/logs/{event_id}`
 * (docs/API.md §10.2), afin de ne pas se contenter des champs de la liste.
 */

import { logsApi } from '../../services/api';
import { useAsyncData } from '../../hooks/useAsyncData';
import { describeActorType, describeSeverity } from '../../utils/display';
import { formatDateTime, formatJson, formatText } from '../../utils/format';
import { ErrorAlert } from '../ui/Alert';
import { DetailList } from '../ui/DetailList';
import { CodeBlock } from '../ui/DetailList';
import { LoadingState } from '../ui/LoadingState';
import { Modal } from '../ui/Modal';
import { StatusBadge } from '../ui/StatusBadge';

export function EventDetailModal({
  eventId,
  onClose,
}: {
  eventId: string;
  onClose: () => void;
}): React.ReactElement {
  const { data, loading, error, reload } = useAsyncData(`log:${eventId}`, (signal) =>
    logsApi.get(eventId, signal),
  );

  return (
    <Modal open title="Détail de l'événement" size="lg" onClose={onClose}>
      {loading ? <LoadingState label="Chargement de l'événement…" /> : null}
      {error ? <ErrorAlert message={error} onRetry={reload} /> : null}

      {data ? (
        <>
          <DetailList
            items={[
              { label: 'Identifiant', value: <span className="mono-sm">{data.id}</span> },
              {
                label: 'Horodatage',
                value: data.created_at ? (
                  <time dateTime={data.created_at}>{formatDateTime(data.created_at)}</time>
                ) : (
                  formatText(null)
                ),
              },
              { label: "Type d'événement", value: <code className="code">{formatText(data.event_type)}</code> },
              {
                label: 'Gravité',
                value: (() => {
                  const descriptor = describeSeverity(data.severity);
                  return <StatusBadge label={descriptor.label} tone={descriptor.tone} />;
                })(),
              },
              { label: 'Message', value: formatText(data.message) },
              { label: "Type d'acteur", value: describeActorType(data.actor_type).label },
              { label: "Identifiant de l'acteur", value: formatText(data.actor_id) },
              { label: 'Agent concerné', value: <span className="mono-sm">{formatText(data.agent_id)}</span> },
              { label: 'Adresse IP observée', value: <span className="mono-sm">{formatText(data.source_ip)}</span> },
            ]}
          />
          {data.metadata ? (
            <>
              <h3 className="section-title">Métadonnées expurgées</h3>
              <CodeBlock content={formatJson(data.metadata)} />
            </>
          ) : null}
        </>
      ) : null}
    </Modal>
  );
}
