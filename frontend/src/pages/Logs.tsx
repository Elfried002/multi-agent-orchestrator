/**
 * Consultation des journaux (docs/API.md §10) : filtres severity / event_type /
 * agent_id / start_date / end_date, pagination côté serveur et détail d'un
 * événement relu via `GET /api/v1/logs/{event_id}`.
 */

import { useState } from 'react';
import { EventDetailModal } from '../components/logs/EventDetailModal';
import { EventTable } from '../components/logs/EventTable';
import { ErrorAlert } from '../components/ui/Alert';
import { PageHeader } from '../components/ui/PageHeader';
import { Pagination } from '../components/ui/Pagination';
import { useAsyncData } from '../hooks/useAsyncData';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { agentsApi, logsApi } from '../services/api';
import { EVENT_SEVERITIES, type EventItem } from '../types';
import { describeSeverity } from '../utils/display';
import { localDateTimeToIso } from '../utils/format';

const PAGE_SIZE = 20;

export function Logs(): React.ReactElement {
  const [severityFilter, setSeverityFilter] = useState<string>('');
  const [eventTypeFilter, setEventTypeFilter] = useState<string>('');
  const [agentFilter, setAgentFilter] = useState<string>('');
  const [startDate, setStartDate] = useState<string>('');
  const [endDate, setEndDate] = useState<string>('');
  const [page, setPage] = useState<number>(1);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);

  const debouncedEventType = useDebouncedValue(eventTypeFilter, 300);
  const startIso = localDateTimeToIso(startDate);
  const endIso = localDateTimeToIso(endDate);

  const agentsList = useAsyncData('logs-agents', (signal) =>
    agentsApi.list({ page: 1, page_size: 100 }, signal),
  );

  const logs = useAsyncData(
    `logs:${severityFilter}:${debouncedEventType}:${agentFilter}:${startIso ?? ''}:${endIso ?? ''}:${page}`,
    (signal) =>
      logsApi.list(
        {
          severity: severityFilter,
          event_type: debouncedEventType,
          agent_id: agentFilter,
          start_date: startIso ?? undefined,
          end_date: endIso ?? undefined,
          page,
          page_size: PAGE_SIZE,
        },
        signal,
      ),
  );

  const filtersActive =
    severityFilter.length > 0 ||
    eventTypeFilter.length > 0 ||
    agentFilter.length > 0 ||
    startDate.length > 0 ||
    endDate.length > 0;

  const handleSelect = (event: EventItem): void => setSelectedEventId(event.id);

  return (
    <>
      <PageHeader
        title="Journaux"
        description="Événements journalisés par le backend (audit, authentification, agents, tâches)."
        actions={
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={logs.reload}
            disabled={logs.loading}
          >
            {logs.loading ? 'Actualisation…' : 'Rafraîchir'}
          </button>
        }
      />

      <section className="card" aria-labelledby="filtres-journaux">
        <h2 className="sr-only" id="filtres-journaux">
          Filtres des journaux
        </h2>
        <div className="filters">
          <div className="field field--inline">
            <label className="field__label" htmlFor="log-filtre-gravite">
              Gravité
            </label>
            <select
              id="log-filtre-gravite"
              className="input"
              value={severityFilter}
              onChange={(event) => {
                setSeverityFilter(event.target.value);
                setPage(1);
              }}
            >
              <option value="">Toutes les gravités</option>
              {EVENT_SEVERITIES.map((severity) => (
                <option key={severity} value={severity}>
                  {describeSeverity(severity).label} ({severity})
                </option>
              ))}
            </select>
          </div>

          <div className="field field--inline">
            <label className="field__label" htmlFor="log-filtre-type">
              Type d'événement
            </label>
            <input
              id="log-filtre-type"
              className="input"
              type="text"
              value={eventTypeFilter}
              onChange={(event) => {
                setEventTypeFilter(event.target.value);
                setPage(1);
              }}
            />
          </div>

          <div className="field field--inline">
            <label className="field__label" htmlFor="log-filtre-agent">
              Agent
            </label>
            <select
              id="log-filtre-agent"
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
            <label className="field__label" htmlFor="log-filtre-debut">
              Du (heure locale)
            </label>
            <input
              id="log-filtre-debut"
              className="input"
              type="datetime-local"
              value={startDate}
              onChange={(event) => {
                setStartDate(event.target.value);
                setPage(1);
              }}
            />
          </div>

          <div className="field field--inline">
            <label className="field__label" htmlFor="log-filtre-fin">
              Au (heure locale)
            </label>
            <input
              id="log-filtre-fin"
              className="input"
              type="datetime-local"
              value={endDate}
              onChange={(event) => {
                setEndDate(event.target.value);
                setPage(1);
              }}
            />
          </div>

          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={!filtersActive}
            onClick={() => {
              setSeverityFilter('');
              setEventTypeFilter('');
              setAgentFilter('');
              setStartDate('');
              setEndDate('');
              setPage(1);
            }}
          >
            Réinitialiser les filtres
          </button>
        </div>
        <p className="text-muted text-small">
          Les dates saisies sont converties en horodatages ISO 8601 UTC avant l'envoi à l'API.
        </p>
        {agentsList.error ? (
          <p className="alert alert--error" role="alert">
            Liste des agents indisponible : {agentsList.error}
          </p>
        ) : null}
      </section>

      {logs.error ? <ErrorAlert message={logs.error} onRetry={logs.reload} /> : null}

      <section className="card" aria-labelledby="liste-journaux">
        <h2 className="sr-only" id="liste-journaux">
          Liste des événements
        </h2>
        <EventTable
          events={logs.data?.items ?? []}
          onSelect={handleSelect}
          loading={logs.loading}
          refreshing={logs.loading && logs.data !== null}
          caption="Événements journalisés"
          emptyTitle="Aucun événement"
          emptyDescription={
            filtersActive
              ? 'Aucun événement ne correspond aux filtres appliqués.'
              : "Aucun événement n'est encore journalisé."
          }
        />
        {logs.data ? (
          <Pagination
            page={logs.data.page || page}
            pageSize={logs.data.page_size || PAGE_SIZE}
            total={logs.data.total}
            onPageChange={setPage}
            disabled={logs.loading}
            itemLabel="événements"
          />
        ) : null}
      </section>

      {selectedEventId ? (
        <EventDetailModal eventId={selectedEventId} onClose={() => setSelectedEventId(null)} />
      ) : null}
    </>
  );
}
