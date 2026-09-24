/**
 * Détail d'une tâche (`GET /api/v1/tasks/{id}`) : état, résultat, erreur
 * éventuelle et proposition d'annulation uniquement lorsque l'état le permet
 * (docs/API.md §8.4 : jamais dans un état terminal).
 */

import { tasksApi } from '../../services/api';
import { useAsyncData } from '../../hooks/useAsyncData';
import type { Task } from '../../types';
import { canCancelTask, describePriority, describeTaskStatus } from '../../utils/display';
import { formatDateTime, formatJson, formatText } from '../../utils/format';
import { ErrorAlert } from '../ui/Alert';
import { DetailList } from '../ui/DetailList';
import { CodeBlock } from '../ui/DetailList';
import { LoadingState } from '../ui/LoadingState';
import { Modal } from '../ui/Modal';
import { StatusBadge } from '../ui/StatusBadge';

export interface TaskDetailModalProps {
  task: Task;
  onClose: () => void;
  /** Ouvre la confirmation d'annulation gérée par la page Tâches. */
  onRequestCancel: (task: Task) => void;
  /** Incrémenté par la page après une action, pour forcer la relecture serveur. */
  refreshToken?: number;
}

export function TaskDetailModal({
  task,
  onClose,
  onRequestCancel,
  refreshToken = 0,
}: TaskDetailModalProps): React.ReactElement {
  const detail = useAsyncData(`task:${task.id}:${refreshToken}`, (signal) =>
    tasksApi.get(task.id, signal),
  );
  const current: Task = detail.data ?? task;
  const statusDescriptor = describeTaskStatus(current.status);
  const cancellable = canCancelTask(current.status);

  return (
    <Modal open title={`Tâche ${formatText(current.title)}`} size="lg" onClose={onClose}>
      {detail.loading && !detail.data ? <LoadingState label="Chargement de la tâche…" /> : null}
      {detail.error ? <ErrorAlert message={detail.error} onRetry={detail.reload} /> : null}

      <DetailList
        items={[
          { label: 'Identifiant', value: <span className="mono-sm">{current.id}</span> },
          { label: 'Titre', value: formatText(current.title) },
          {
            label: 'État',
            value: <StatusBadge label={statusDescriptor.label} tone={statusDescriptor.tone} />,
          },
          { label: 'Priorité', value: describePriority(current.priority).label },
          {
            label: 'Agent assigné',
            value: <span className="mono-sm">{formatText(current.assigned_agent_id)}</span>,
          },
          { label: 'Créée par', value: formatText(current.created_by) },
          { label: 'Créée le', value: formatDateTime(current.created_at) },
          { label: 'Démarrée le', value: formatDateTime(current.started_at) },
          { label: 'Terminée le', value: formatDateTime(current.completed_at) },
        ]}
      />

      <h3 className="section-title">Description</h3>
      <p className="text-block">{formatText(current.description)}</p>

      {current.error_message ? (
        <>
          <h3 className="section-title">Erreur signalée</h3>
          <p className="alert alert--error" role="alert">
            {current.error_message}
          </p>
        </>
      ) : null}

      <h3 className="section-title">Résultat</h3>
      {current.result === null || current.result === undefined ? (
        <p className="text-muted">Aucun résultat transmis pour cette tâche.</p>
      ) : (
        <CodeBlock content={formatJson(current.result)} />
      )}

      <h3 className="section-title">Annulation</h3>
      {cancellable ? (
        <div className="actions-row">
          <button type="button" className="btn btn--danger" onClick={() => onRequestCancel(current)}>
            Annuler la tâche
          </button>
          <span className="text-muted">
            L'annulation est refusée par le backend dans un état terminal.
          </span>
        </div>
      ) : (
        <p className="text-muted">
          L'état « {statusDescriptor.label} » est terminal : aucune annulation n'est proposée.
        </p>
      )}
    </Modal>
  );
}
