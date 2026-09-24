/**
 * Fiche détaillée d'un agent : informations complètes (`GET /api/v1/agents/{id}`),
 * historique d'activité (`GET /api/v1/logs?agent_id=…`), tâches attribuées
 * (`GET /api/v1/tasks?agent_id=…`) et actions de déconnexion / révocation avec
 * confirmation explicite (docs/API.md §7.3 et §7.4).
 */

import { useState } from 'react';
import { agentsApi, logsApi, tasksApi } from '../../services/api';
import { useAction } from '../../hooks/useAction';
import { useAsyncData } from '../../hooks/useAsyncData';
import type { Agent, EventItem, Task } from '../../types';
import { describeAgentStatus, describePriority, describeTaskStatus } from '../../utils/display';
import { formatDateTime, formatList, formatText } from '../../utils/format';
import { Notice } from '../ui/Alert';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { DataTable, type Column } from '../ui/DataTable';
import { DetailList } from '../ui/DetailList';
import { ErrorAlert } from '../ui/Alert';
import { LoadingState } from '../ui/LoadingState';
import { Modal } from '../ui/Modal';
import { StatusBadge } from '../ui/StatusBadge';
import { EventDetailModal } from '../logs/EventDetailModal';
import { EventTable } from '../logs/EventTable';

export interface AgentDetailModalProps {
  agent: Agent;
  onClose: () => void;
  /** Notifie la liste parente qu'un état a changé (rechargement de la page). */
  onChanged: () => void;
}

type AgentActionKind = 'disconnect' | 'revoke';

export function AgentDetailModal({
  agent,
  onClose,
  onChanged,
}: AgentDetailModalProps): React.ReactElement {
  const detail = useAsyncData(`agent:${agent.id}`, (signal) => agentsApi.get(agent.id, signal));
  const activity = useAsyncData(`agent-activity:${agent.id}`, (signal) =>
    logsApi.list({ agent_id: agent.id, page: 1, page_size: 10 }, signal),
  );
  const agentTasks = useAsyncData(`agent-tasks:${agent.id}`, (signal) =>
    tasksApi.list({ agent_id: agent.id, page: 1, page_size: 10 }, signal),
  );

  const [confirmKind, setConfirmKind] = useState<AgentActionKind | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const action = useAction(async (kind: AgentActionKind) => {
    if (kind === 'disconnect') {
      await agentsApi.disconnect(agent.id);
      return;
    }
    await agentsApi.revoke(agent.id);
  });

  const current: Agent = detail.data ?? agent;
  const statusDescriptor = describeAgentStatus(current.status);
  const alreadyRevoked = current.status === 'REVOKED';

  const handleConfirmed = async (): Promise<void> => {
    if (!confirmKind) return;
    const kind = confirmKind;
    const succeeded = await action.run(kind);
    if (!succeeded) return;
    setConfirmKind(null);
    setFeedback(
      kind === 'disconnect'
        ? "Demande de déconnexion transmise. L'enregistrement de l'agent est conservé."
        : 'Agent révoqué. Ses jetons sont invalidés et un nouvel enregistrement est requis.',
    );
    detail.reload();
    activity.reload();
    agentTasks.reload();
    onChanged();
  };

  const taskColumns: Column<Task>[] = [
    { key: 'id', header: 'Identifiant', render: (task) => <span className="mono-sm">{task.id}</span> },
    { key: 'title', header: 'Titre', render: (task) => formatText(task.title) },
    {
      key: 'status',
      header: 'État',
      render: (task) => {
        const descriptor = describeTaskStatus(task.status);
        return <StatusBadge label={descriptor.label} tone={descriptor.tone} />;
      },
    },
    {
      key: 'priority',
      header: 'Priorité',
      render: (task) => describePriority(task.priority).label,
    },
    {
      key: 'created_at',
      header: 'Créée le',
      render: (task) => formatDateTime(task.created_at),
    },
  ];

  return (
    <>
      <Modal open title={`Agent ${formatText(current.name)}`} size="lg" onClose={onClose}>
        {feedback ? <Notice tone="success">{feedback}</Notice> : null}
        {detail.error ? <ErrorAlert message={detail.error} onRetry={detail.reload} /> : null}
        {detail.loading && !detail.data ? <LoadingState label="Chargement de la fiche agent…" /> : null}

        <h3 className="section-title">Informations</h3>
        <DetailList
          items={[
            { label: 'Identifiant', value: <span className="mono-sm">{current.id}</span> },
            { label: 'Nom', value: formatText(current.name) },
            { label: 'Rôle', value: formatText(current.role) },
            { label: 'Runtime', value: formatText(current.runtime) },
            {
              label: 'État',
              value: <StatusBadge label={statusDescriptor.label} tone={statusDescriptor.tone} />,
            },
            {
              label: 'Capacités déclarées',
              value: current.capabilities ? (
                formatList(current.capabilities)
              ) : (
                <span className="text-muted" title="Champ absent de la réponse du backend">
                  non fourni
                </span>
              ),
            },
            { label: 'Dernière IP observée', value: <span className="mono-sm">{formatText(current.source_ip)}</span> },
            { label: 'Dernier heartbeat', value: formatDateTime(current.last_seen_at) },
            {
              label: "Date d'enregistrement",
              value: current.created_at ? (
                formatDateTime(current.created_at)
              ) : (
                <span className="text-muted" title="Champ absent de la réponse du backend">
                  non fournie
                </span>
              ),
            },
            ...(current.revoked_at
              ? [{ label: 'Révoqué le', value: formatDateTime(current.revoked_at) }]
              : []),
          ]}
        />

        <h3 className="section-title">Actions</h3>
        <p className="text-muted">
          Une déconnexion interrompt la session active sans supprimer l'enregistrement ; une
          révocation invalide définitivement les jetons de cet agent.
        </p>
        <div className="actions-row">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => setConfirmKind('disconnect')}
            disabled={action.busy || alreadyRevoked}
          >
            Déconnecter l'agent
          </button>
          <button
            type="button"
            className="btn btn--danger"
            onClick={() => setConfirmKind('revoke')}
            disabled={action.busy || alreadyRevoked}
          >
            Révoquer l'agent
          </button>
        </div>
        {alreadyRevoked ? (
          <p className="text-muted">Cet agent est révoqué : les actions sont sans effet.</p>
        ) : null}

        <h3 className="section-title">Tâches attribuées (10 plus récentes)</h3>
        {agentTasks.error ? <ErrorAlert message={agentTasks.error} onRetry={agentTasks.reload} /> : null}
        <DataTable
          columns={taskColumns}
          rows={agentTasks.data?.items ?? []}
          rowKey={(task) => task.id}
          caption="Tâches attribuées à cet agent"
          emptyTitle="Aucune tâche attribuée à cet agent"
          emptyDescription="Les tâches apparaissent ici dès qu'elles sont assignées à cet agent."
          loading={agentTasks.loading}
          loadingLabel="Chargement des tâches…"
          refreshing={agentTasks.loading && agentTasks.data !== null}
        />

        <h3 className="section-title">Historique d'activité (10 derniers événements)</h3>
        {activity.error ? <ErrorAlert message={activity.error} onRetry={activity.reload} /> : null}
        <EventTable
          events={activity.data?.items ?? []}
          onSelect={(event: EventItem) => setSelectedEventId(event.id)}
          loading={activity.loading}
          refreshing={activity.loading && activity.data !== null}
          caption="Historique d'activité de l'agent"
          emptyTitle="Aucun événement pour cet agent"
          emptyDescription="Aucun événement journalisé ne référence encore cet agent."
        />
      </Modal>

      {selectedEventId ? (
        <EventDetailModal eventId={selectedEventId} onClose={() => setSelectedEventId(null)} />
      ) : null}

      <ConfirmDialog
        open={confirmKind !== null}
        title={
          confirmKind === 'revoke'
            ? "Révocation d'agent"
            : "Déconnexion d'agent"
        }
        message={
          confirmKind === 'revoke'
            ? `Confirmer la révocation de l'agent « ${current.name} » ? Les jetons actifs seront révoqués, ses nouvelles requêtes refusées, et un nouvel enregistrement sera nécessaire pour rétablir l'accès.`
            : `Confirmer la déconnexion de l'agent « ${current.name} » ? La session active sera interrompue ; l'enregistrement de l'agent est conservé et la déconnexion n'est pas une révocation définitive.`
        }
        details={
          <DetailList
            items={[
              { label: 'Agent', value: `${current.name} (${current.id})` },
              { label: 'État actuel', value: statusDescriptor.label },
            ]}
          />
        }
        confirmLabel={confirmKind === 'revoke' ? 'Révoquer définitivement' : 'Déconnecter'}
        danger={confirmKind === 'revoke'}
        busy={action.busy}
        error={action.error}
        onConfirm={() => void handleConfirmed()}
        onClose={() => {
          if (!action.busy) {
            setConfirmKind(null);
            action.reset();
          }
        }}
      />
    </>
  );
}
