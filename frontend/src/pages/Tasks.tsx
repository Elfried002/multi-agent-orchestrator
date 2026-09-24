/**
 * Gestion des tâches (docs/API.md §8) : création, liste filtrable paginée,
 * détail avec résultat, et annulation proposée uniquement hors état terminal.
 */

import { useState } from 'react';
import { TaskCreateForm } from '../components/tasks/TaskCreateForm';
import { TaskDetailModal } from '../components/tasks/TaskDetailModal';
import { ErrorAlert, Notice } from '../components/ui/Alert';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
import { DataTable, type Column } from '../components/ui/DataTable';
import { PageHeader } from '../components/ui/PageHeader';
import { Pagination } from '../components/ui/Pagination';
import { StatusBadge } from '../components/ui/StatusBadge';
import { useAction } from '../hooks/useAction';
import { useAsyncData } from '../hooks/useAsyncData';
import { agentsApi, tasksApi } from '../services/api';
import { TASK_PRIORITIES, TASK_STATUSES, type Task } from '../types';
import { canCancelTask, describePriority, describeTaskStatus } from '../utils/display';
import { formatDateTime, formatText } from '../utils/format';

const PAGE_SIZE = 20;

export function Tasks(): React.ReactElement {
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [agentFilter, setAgentFilter] = useState<string>('');
  const [priorityFilter, setPriorityFilter] = useState<string>('');
  const [page, setPage] = useState<number>(1);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [cancelTarget, setCancelTarget] = useState<Task | null>(null);
  const [detailToken, setDetailToken] = useState<number>(0);
  const [notice, setNotice] = useState<string | null>(null);

  const agentsList = useAsyncData('tasks-agents', (signal) =>
    agentsApi.list({ page: 1, page_size: 100 }, signal),
  );

  const tasks = useAsyncData(
    `tasks:${statusFilter}:${agentFilter}:${priorityFilter}:${page}`,
    (signal) =>
      tasksApi.list(
        {
          status: statusFilter,
          agent_id: agentFilter,
          priority: priorityFilter,
          page,
          page_size: PAGE_SIZE,
        },
        signal,
      ),
  );

  const cancelAction = useAction(async (taskId: string) => {
    await tasksApi.cancel(taskId);
  });

  const handleConfirmCancel = async (): Promise<void> => {
    if (!cancelTarget) return;
    const target = cancelTarget;
    const succeeded = await cancelAction.run(target.id);
    if (!succeeded) return;
    setCancelTarget(null);
    setNotice(
      `Annulation demandée pour la tâche « ${target.title} » (${target.id}). L'état affiché est celui renvoyé par le backend.`,
    );
    tasks.reload();
    setDetailToken((value) => value + 1);
  };

  const columns: Column<Task>[] = [
    { key: 'title', header: 'Titre', render: (task) => formatText(task.title) },
    {
      key: 'id',
      header: 'Identifiant',
      render: (task) => (
        <span className="mono-sm cell-truncate" title={task.id}>
          {task.id}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'État',
      render: (task) => {
        const descriptor = describeTaskStatus(task.status);
        return <StatusBadge label={descriptor.label} tone={descriptor.tone} />;
      },
    },
    { key: 'priority', header: 'Priorité', render: (task) => describePriority(task.priority).label },
    {
      key: 'assigned_agent_id',
      header: 'Agent assigné',
      render: (task) => <span className="mono-sm">{formatText(task.assigned_agent_id)}</span>,
    },
    { key: 'created_at', header: 'Créée le', render: (task) => formatDateTime(task.created_at) },
    {
      key: 'actions',
      header: 'Actions',
      render: (task) => (
        <span className="actions-inline">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => setSelectedTask(task)}
          >
            Détails
          </button>
          {canCancelTask(task.status) ? (
            <button
              type="button"
              className="btn btn--danger btn--sm"
              onClick={() => {
                cancelAction.reset();
                setCancelTarget(task);
              }}
            >
              Annuler
            </button>
          ) : null}
        </span>
      ),
    },
  ];

  const filtersActive =
    statusFilter.length > 0 || agentFilter.length > 0 || priorityFilter.length > 0;

  return (
    <>
      <PageHeader
        title="Tâches"
        description="Création, suivi et annulation des tâches orchestrées."
        actions={
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={tasks.reload}
            disabled={tasks.loading}
          >
            {tasks.loading ? 'Actualisation…' : 'Rafraîchir'}
          </button>
        }
      />

      {notice ? <Notice tone="success">{notice}</Notice> : null}

      <TaskCreateForm
        agents={agentsList.data?.items ?? []}
        agentsLoading={agentsList.loading}
        agentsError={agentsList.error}
        onCreated={tasks.reload}
      />

      <section className="card" aria-labelledby="filtres-taches">
        <h2 className="sr-only" id="filtres-taches">
          Filtres des tâches
        </h2>
        <div className="filters">
          <div className="field field--inline">
            <label className="field__label" htmlFor="tache-filtre-statut">
              État
            </label>
            <select
              id="tache-filtre-statut"
              className="input"
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value);
                setPage(1);
              }}
            >
              <option value="">Tous les états</option>
              {TASK_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {describeTaskStatus(status).label} ({status})
                </option>
              ))}
            </select>
          </div>

          <div className="field field--inline">
            <label className="field__label" htmlFor="tache-filtre-agent">
              Agent
            </label>
            <select
              id="tache-filtre-agent"
              className="input"
              value={agentFilter}
              disabled={agentsList.loading}
              onChange={(event) => {
                setAgentFilter(event.target.value);
                setPage(1);
              }}
            >
              <option value="">Tous les agents</option>
              {(agentsList.data?.items ?? []).map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name} ({agent.id})
                </option>
              ))}
            </select>
          </div>

          <div className="field field--inline">
            <label className="field__label" htmlFor="tache-filtre-priorite">
              Priorité
            </label>
            <select
              id="tache-filtre-priorite"
              className="input"
              value={priorityFilter}
              onChange={(event) => {
                setPriorityFilter(event.target.value);
                setPage(1);
              }}
            >
              <option value="">Toutes les priorités</option>
              {TASK_PRIORITIES.map((priority) => (
                <option key={priority} value={priority}>
                  {describePriority(priority).label} ({priority})
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={!filtersActive}
            onClick={() => {
              setStatusFilter('');
              setAgentFilter('');
              setPriorityFilter('');
              setPage(1);
            }}
          >
            Réinitialiser les filtres
          </button>
        </div>
        {agentsList.error ? (
          <p className="alert alert--error" role="alert">
            Liste des agents indisponible : {agentsList.error}
          </p>
        ) : null}
      </section>

      {tasks.error ? <ErrorAlert message={tasks.error} onRetry={tasks.reload} /> : null}

      <section className="card" aria-labelledby="liste-taches">
        <h2 className="sr-only" id="liste-taches">
          Liste des tâches
        </h2>
        <DataTable
          columns={columns}
          rows={tasks.data?.items ?? []}
          rowKey={(task) => task.id}
          caption="Liste des tâches"
          emptyTitle="Aucune tâche"
          emptyDescription={
            filtersActive
              ? 'Aucune tâche ne correspond aux filtres appliqués.'
              : 'Créez une tâche avec le formulaire ci-dessus.'
          }
          loading={tasks.loading}
          loadingLabel="Chargement des tâches…"
          refreshing={tasks.loading && tasks.data !== null}
        />
        {tasks.data ? (
          <Pagination
            page={tasks.data.page || page}
            pageSize={tasks.data.page_size || PAGE_SIZE}
            total={tasks.data.total}
            onPageChange={setPage}
            disabled={tasks.loading}
            itemLabel="tâches"
          />
        ) : null}
      </section>

      {selectedTask ? (
        <TaskDetailModal
          task={selectedTask}
          refreshToken={detailToken}
          onClose={() => setSelectedTask(null)}
          onRequestCancel={(task) => {
            cancelAction.reset();
            setCancelTarget(task);
          }}
        />
      ) : null}

      <ConfirmDialog
        open={cancelTarget !== null}
        title="Annulation de tâche"
        message={
          cancelTarget
            ? `Confirmer l'annulation de la tâche « ${cancelTarget.title} » ? Le backend refuse l'annulation d'une tâche déjà terminée, annulée, en échec ou expirée.`
            : ''
        }
        details={
          cancelTarget ? (
            <p className="text-muted">
              Identifiant : <span className="mono-sm">{cancelTarget.id}</span> — état actuel :{' '}
              {describeTaskStatus(cancelTarget.status).label}
            </p>
          ) : undefined
        }
        confirmLabel="Annuler la tâche"
        danger
        busy={cancelAction.busy}
        error={cancelAction.error}
        onConfirm={() => void handleConfirmCancel()}
        onClose={() => {
          if (!cancelAction.busy) {
            setCancelTarget(null);
            cancelAction.reset();
          }
        }}
      />
    </>
  );
}
