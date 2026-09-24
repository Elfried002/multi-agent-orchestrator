/**
 * Gestion et supervision des agents (docs/API.md §7).
 * Filtres status / role / search + pagination côté serveur, fiche détaillée avec
 * historique d'activité et tâches, actions de déconnexion et de révocation.
 */

import { useState } from 'react';
import { AgentDetailModal } from '../components/agents/AgentDetailModal';
import { ErrorAlert } from '../components/ui/Alert';
import { DataTable, type Column } from '../components/ui/DataTable';
import { Pagination } from '../components/ui/Pagination';
import { PageHeader } from '../components/ui/PageHeader';
import { StatusBadge } from '../components/ui/StatusBadge';
import { useAsyncData } from '../hooks/useAsyncData';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { agentsApi } from '../services/api';
import { AGENT_STATUSES, type Agent } from '../types';
import { describeAgentStatus } from '../utils/display';
import { formatDateTime, formatList, formatText } from '../utils/format';

const PAGE_SIZE = 20;

export function Agents(): React.ReactElement {
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [roleFilter, setRoleFilter] = useState<string>('');
  const [search, setSearch] = useState<string>('');
  const [page, setPage] = useState<number>(1);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);

  const debouncedSearch = useDebouncedValue(search, 300);
  const debouncedRole = useDebouncedValue(roleFilter, 300);

  const agents = useAsyncData(
    `agents:${statusFilter}:${debouncedRole}:${debouncedSearch}:${page}`,
    (signal) =>
      agentsApi.list(
        {
          status: statusFilter,
          role: debouncedRole,
          search: debouncedSearch,
          page,
          page_size: PAGE_SIZE,
        },
        signal,
      ),
  );

  const columns: Column<Agent>[] = [
    { key: 'name', header: 'Nom', render: (agent) => formatText(agent.name) },
    {
      key: 'id',
      header: 'Identifiant',
      render: (agent) => (
        <span className="mono-sm cell-truncate" title={agent.id}>
          {agent.id}
        </span>
      ),
    },
    { key: 'role', header: 'Rôle', render: (agent) => formatText(agent.role) },
    { key: 'runtime', header: 'Runtime', render: (agent) => formatText(agent.runtime) },
    {
      key: 'capabilities',
      header: 'Capacités déclarées',
      render: (agent) =>
        agent.capabilities && agent.capabilities.length > 0 ? (
          formatList(agent.capabilities)
        ) : (
          <span className="text-muted" title="Champ absent de la réponse GET /api/v1/agents">
            non fourni
          </span>
        ),
    },
    {
      key: 'status',
      header: 'État',
      render: (agent) => {
        const descriptor = describeAgentStatus(agent.status);
        return <StatusBadge label={descriptor.label} tone={descriptor.tone} />;
      },
    },
    {
      key: 'source_ip',
      header: 'IP source',
      render: (agent) => <span className="mono-sm">{formatText(agent.source_ip)}</span>,
    },
    {
      key: 'last_seen_at',
      header: 'Dernier heartbeat',
      render: (agent) => formatDateTime(agent.last_seen_at),
    },
    {
      key: 'created_at',
      header: "Date d'inscription",
      render: (agent) =>
        agent.created_at ? (
          formatDateTime(agent.created_at)
        ) : (
          <span className="text-muted" title="Champ absent de la réponse GET /api/v1/agents">
            non fournie
          </span>
        ),
    },
    {
      key: 'actions',
      header: 'Actions',
      render: (agent) => (
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setSelectedAgent(agent)}>
          Détails
        </button>
      ),
    },
  ];

  const filtersActive =
    statusFilter.length > 0 || roleFilter.length > 0 || search.length > 0;

  return (
    <>
      <PageHeader
        title="Agents"
        description="Agents enregistrés, capacités déclarées, présence et actions d'administration."
        actions={
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={agents.reload}
            disabled={agents.loading}
          >
            {agents.loading ? 'Actualisation…' : 'Rafraîchir'}
          </button>
        }
      />

      <section className="card" aria-labelledby="filtres-agents">
        <h2 className="sr-only" id="filtres-agents">
          Filtres des agents
        </h2>
        <div className="filters">
          <div className="field field--inline">
            <label className="field__label" htmlFor="filtre-statut">
              État
            </label>
            <select
              id="filtre-statut"
              className="input"
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value);
                setPage(1);
              }}
            >
              <option value="">Tous les états</option>
              {AGENT_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {describeAgentStatus(status).label} ({status})
                </option>
              ))}
            </select>
          </div>

          <div className="field field--inline">
            <label className="field__label" htmlFor="filtre-role">
              Rôle
            </label>
            <input
              id="filtre-role"
              className="input"
              type="text"
              value={roleFilter}
              onChange={(event) => {
                setRoleFilter(event.target.value);
                setPage(1);
              }}
            />
          </div>

          <div className="field field--inline">
            <label className="field__label" htmlFor="filtre-recherche">
              Recherche (nom, identifiant)
            </label>
            <input
              id="filtre-recherche"
              className="input"
              type="search"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(1);
              }}
            />
          </div>

          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={!filtersActive}
            onClick={() => {
              setStatusFilter('');
              setRoleFilter('');
              setSearch('');
              setPage(1);
            }}
          >
            Réinitialiser les filtres
          </button>
        </div>
      </section>

      {agents.error ? <ErrorAlert message={agents.error} onRetry={agents.reload} /> : null}

      <section className="card" aria-labelledby="liste-agents">
        <h2 className="sr-only" id="liste-agents">
          Liste des agents
        </h2>
        <DataTable
          columns={columns}
          rows={agents.data?.items ?? []}
          rowKey={(agent) => agent.id}
          caption="Liste des agents enregistrés"
          emptyTitle="Aucun agent enregistré"
          emptyDescription={
            filtersActive
              ? 'Aucun agent ne correspond aux filtres appliqués.'
              : "Les agents apparaîtront ici après leur enregistrement via la clé d'enregistrement."
          }
          loading={agents.loading}
          loadingLabel="Chargement des agents…"
          refreshing={agents.loading && agents.data !== null}
        />
        {agents.data ? (
          <Pagination
            page={agents.data.page || page}
            pageSize={agents.data.page_size || PAGE_SIZE}
            total={agents.data.total}
            onPageChange={setPage}
            disabled={agents.loading}
            itemLabel="agents"
          />
        ) : null}
      </section>

      {selectedAgent ? (
        <AgentDetailModal
          agent={selectedAgent}
          onClose={() => setSelectedAgent(null)}
          onChanged={agents.reload}
        />
      ) : null}
    </>
  );
}
