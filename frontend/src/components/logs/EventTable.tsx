/**
 * Tableau des événements journalisés (docs/API.md §10).
 * Réutilisé par le tableau de bord, la page Journaux et la page Sécurité.
 */

import type { EventItem } from '../../types';
import { describeActorType, describeSeverity } from '../../utils/display';
import { formatDateTime, formatText } from '../../utils/format';
import { DataTable, type Column } from '../ui/DataTable';
import { StatusBadge } from '../ui/StatusBadge';

export interface EventTableProps {
  events: EventItem[];
  onSelect: (event: EventItem) => void;
  loading?: boolean;
  refreshing?: boolean;
  caption?: string;
  emptyTitle?: string;
  emptyDescription?: string;
}

export function EventTable({
  events,
  onSelect,
  loading = false,
  refreshing = false,
  caption = "Liste des événements journalisés",
  emptyTitle = 'Aucun événement',
  emptyDescription,
}: EventTableProps): React.ReactElement {
  const columns: Column<EventItem>[] = [
    {
      key: 'created_at',
      header: 'Horodatage',
      width: '15rem',
      render: (event) =>
        event.created_at ? (
          <time dateTime={event.created_at}>{formatDateTime(event.created_at)}</time>
        ) : (
          formatText(null)
        ),
    },
    {
      key: 'severity',
      header: 'Gravité',
      render: (event) => {
        const descriptor = describeSeverity(event.severity);
        return <StatusBadge label={descriptor.label} tone={descriptor.tone} />;
      },
    },
    {
      key: 'event_type',
      header: "Type d'événement",
      render: (event) => <code className="code">{formatText(event.event_type)}</code>,
    },
    {
      key: 'message',
      header: 'Message',
      render: (event) => (
        <span className="cell-truncate" title={event.message}>
          {formatText(event.message)}
        </span>
      ),
    },
    {
      key: 'actor',
      header: 'Acteur',
      render: (event) => {
        const actor = describeActorType(event.actor_type);
        return (
          <span>
            {actor.label}
            {event.actor_id ? <span className="text-muted"> · {event.actor_id}</span> : null}
          </span>
        );
      },
    },
    {
      key: 'agent_id',
      header: 'Agent',
      render: (event) => <span className="mono-sm">{formatText(event.agent_id)}</span>,
    },
    {
      key: 'source_ip',
      header: 'IP source',
      render: (event) => <span className="mono-sm">{formatText(event.source_ip)}</span>,
    },
    {
      key: 'actions',
      header: 'Actions',
      render: (event) => (
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => onSelect(event)}>
          Détails
        </button>
      ),
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={events}
      rowKey={(event) => event.id}
      caption={caption}
      emptyTitle={emptyTitle}
      emptyDescription={emptyDescription}
      loading={loading}
      loadingLabel="Chargement des événements…"
      refreshing={refreshing}
    />
  );
}
