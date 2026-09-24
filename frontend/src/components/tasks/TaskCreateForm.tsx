/**
 * Formulaire de création de tâche (docs/API.md §8.1).
 * Le formulaire ne crée aucune donnée locale : il envoie `POST /api/v1/tasks`
 * et n'affiche le résultat que si le backend l'a confirmé.
 */

import { useState, type FormEvent } from 'react';
import { tasksApi } from '../../services/api';
import { useAction } from '../../hooks/useAction';
import { TASK_PRIORITIES, type Agent, type CreateTaskPayload, type Task, type TaskPriority } from '../../types';
import { describePriority } from '../../utils/display';
import { formatText } from '../../utils/format';
import { Notice } from '../ui/Alert';
import { Field } from '../ui/Field';

export interface TaskCreateFormProps {
  agents: Agent[];
  agentsLoading: boolean;
  agentsError: string | null;
  onCreated: () => void;
}

export function TaskCreateForm({
  agents,
  agentsLoading,
  agentsError,
  onCreated,
}: TaskCreateFormProps): React.ReactElement {
  const [title, setTitle] = useState<string>('');
  const [description, setDescription] = useState<string>('');
  const [priority, setPriority] = useState<TaskPriority>('NORMAL');
  const [assignedAgentId, setAssignedAgentId] = useState<string>('');
  const [validationError, setValidationError] = useState<string | null>(null);
  const [createdTask, setCreatedTask] = useState<Task | null>(null);
  const [createdAgentLabel, setCreatedAgentLabel] = useState<string>('');

  const action = useAction(async (payload: CreateTaskPayload) => {
    const task = await tasksApi.create(payload);
    setCreatedTask(task);
  });

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setValidationError(null);
    setCreatedTask(null);

    if (title.trim().length === 0) {
      setValidationError('Le titre est obligatoire.');
      return;
    }
    if (description.trim().length === 0) {
      setValidationError('La description est obligatoire.');
      return;
    }

    const payload: CreateTaskPayload = {
      title: title.trim(),
      description: description.trim(),
      priority,
    };
    if (assignedAgentId) payload.assigned_agent_id = assignedAgentId;

    const selectedAgent = agents.find((agent) => agent.id === assignedAgentId);
    setCreatedAgentLabel(selectedAgent ? `${selectedAgent.name} (${selectedAgent.id})` : 'non assignée');

    const succeeded = await action.run(payload);
    if (!succeeded) return;

    setTitle('');
    setDescription('');
    setPriority('NORMAL');
    setAssignedAgentId('');
    onCreated();
  };

  return (
    <section className="card" aria-labelledby="creation-tache">
      <header className="card__header">
        <h2 className="card__title" id="creation-tache">
          Créer une tâche
        </h2>
      </header>

      {createdTask ? (
        <Notice tone="success" title="Tâche créée">
          <p>
            Identifiant <span className="mono-sm">{createdTask.id}</span> — état initial{' '}
            <span className="mono-sm">{formatText(createdTask.status)}</span> — agent :{' '}
            {createdAgentLabel}
          </p>
        </Notice>
      ) : null}

      {action.error ? (
        <p className="alert alert--error" role="alert">
          {action.error}
        </p>
      ) : null}

      <form className="form form--grid" onSubmit={(event) => void handleSubmit(event)} noValidate>
        <Field label="Titre" htmlFor="task-title" required>
          <input
            id="task-title"
            name="title"
            className="input"
            type="text"
            value={title}
            maxLength={200}
            required
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>

        <Field label="Priorité" htmlFor="task-priority">
          <select
            id="task-priority"
            name="priority"
            className="input"
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
          >
            {TASK_PRIORITIES.map((value) => (
              <option key={value} value={value}>
                {describePriority(value).label} ({value})
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Agent assigné"
          htmlFor="task-agent"
          hint="Facultatif : laissé vide, la tâche reste non assignée."
        >
          <select
            id="task-agent"
            name="assigned_agent_id"
            className="input"
            value={assignedAgentId}
            disabled={agentsLoading}
            onChange={(event) => setAssignedAgentId(event.target.value)}
          >
            <option value="">— Aucun agent —</option>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name} · {agent.role} · {agent.status}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Description" htmlFor="task-description" required>
          <textarea
            id="task-description"
            name="description"
            className="input input--textarea"
            value={description}
            rows={4}
            required
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>

        {agentsError ? (
          <p className="alert alert--error" role="alert">
            Liste des agents indisponible : {agentsError}
          </p>
        ) : null}

        {validationError ? (
          <p className="alert alert--error" role="alert">
            {validationError}
          </p>
        ) : null}

        <div className="actions-row">
          <button type="submit" className="btn btn--primary" disabled={action.busy}>
            {action.busy ? 'Envoi…' : 'Créer la tâche'}
          </button>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => {
              setTitle('');
              setDescription('');
              setPriority('NORMAL');
              setAssignedAgentId('');
              setValidationError(null);
              setCreatedTask(null);
            }}
            disabled={action.busy}
          >
            Réinitialiser
          </button>
        </div>
      </form>
    </section>
  );
}
