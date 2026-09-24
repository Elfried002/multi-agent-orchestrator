/**
 * Page Sécurité (mission §9, docs/SECURITY.md §10 et §11) :
 * - tableaux de bord des gravités sensibles (WARNING / ERROR / CRITICAL) ;
 * - événements sensibles paginés avec filtre de gravité ;
 * - tentatives de connexion échouées et anomalies d'authentification.
 *
 * Le regroupement des événements d'authentification est réalisé dans le
 * navigateur à partir des événements RÉELLEMENT renvoyés par l'API ; l'interface
 * indique explicitement le nombre d'événements analysés.
 */

import { useState } from 'react';
import { EventDetailModal } from '../components/logs/EventDetailModal';
import { EventTable } from '../components/logs/EventTable';
import { ErrorAlert } from '../components/ui/Alert';
import { PageHeader } from '../components/ui/PageHeader';
import { Pagination } from '../components/ui/Pagination';
import { StatCard } from '../components/ui/StatCard';
import { useAsyncData } from '../hooks/useAsyncData';
import { logsApi } from '../services/api';
import { SENSITIVE_SEVERITIES, type EventItem } from '../types';
import { describeSeverity } from '../utils/display';
import { looksLikeAuthenticationFailure } from '../utils/security';

const PAGE_SIZE = 20;
const AUTH_SAMPLE_SIZE = 50;

export function Security(): React.ReactElement {
  const [severity, setSeverity] = useState<string>('WARNING');
  const [page, setPage] = useState<number>(1);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);

  const security = useAsyncData(
    `security:${severity}:${page}`,
    async (signal) => {
      const [warnings, errors, criticals, sensitive, sample] = await Promise.all([
        logsApi.list({ severity: 'WARNING', page: 1, page_size: 1 }, signal),
        logsApi.list({ severity: 'ERROR', page: 1, page_size: 1 }, signal),
        logsApi.list({ severity: 'CRITICAL', page: 1, page_size: 1 }, signal),
        logsApi.list({ severity, page, page_size: PAGE_SIZE }, signal),
        logsApi.list({ page: 1, page_size: AUTH_SAMPLE_SIZE }, signal),
      ]);

      const authenticationFailures = sample.items.filter(looksLikeAuthenticationFailure);

      return {
        counts: {
          WARNING: warnings.total,
          ERROR: errors.total,
          CRITICAL: criticals.total,
        },
        sensitive,
        authenticationFailures,
        analysedCount: sample.items.length,
      };
    },
  );

  const data = security.data;

  return (
    <>
      <PageHeader
        title="Sécurité"
        description="Événements sensibles, connexions échouées et alertes relevées dans les journaux d'audit."
        actions={
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={security.reload}
            disabled={security.loading}
          >
            {security.loading ? 'Actualisation…' : 'Rafraîchir'}
          </button>
        }
      />

      {security.error ? <ErrorAlert message={security.error} onRetry={security.reload} /> : null}

      {data ? (
        <>
          <section className="card" aria-labelledby="resume-securite">
            <header className="card__header">
              <h2 className="card__title" id="resume-securite">
                Résumé des événements sensibles
              </h2>
            </header>
            <div className="stat-grid">
              {SENSITIVE_SEVERITIES.map((value) => {
                const descriptor = describeSeverity(value);
                return (
                  <StatCard
                    key={value}
                    label={descriptor.label}
                    hint={value}
                    value={data.counts[value] ?? 0}
                    tone={descriptor.tone}
                    loading={security.loading}
                  />
                );
              })}
            </div>
            <p className="text-muted text-small">
              Totaux fournis par le champ « total » de <code className="code">GET /api/v1/logs</code>{' '}
              filtré sur chaque gravité.
            </p>
          </section>

          <section className="card" aria-labelledby="tentatives-echouees">
            <header className="card__header">
              <h2 className="card__title" id="tentatives-echouees">
                Tentatives de connexion échouées et anomalies d'authentification
              </h2>
            </header>
            <p className="text-muted text-small">
              Regroupement dérivé dans le navigateur à partir des {data.analysedCount} derniers
              événements renvoyés par <code className="code">GET /api/v1/logs</code>. L'API ne
              publie pas d'énumération des types d'événements : le classement repose sur le type,
              le message et la gravité de chaque événement reçu, sans aucune donnée inventée.
            </p>
            {data.authenticationFailures.length > 0 ? (
              <EventTable
                events={data.authenticationFailures}
                onSelect={(event: EventItem) => setSelectedEventId(event.id)}
                loading={security.loading}
                refreshing={security.loading && data.authenticationFailures.length > 0}
                caption="Tentatives de connexion échouées et anomalies d'authentification"
                emptyTitle="Aucune connexion échouée détectée"
              />
            ) : (
              <p className="text-muted">
                Aucune tentative de connexion échouée ni anomalie d'authentification parmi les{' '}
                {data.analysedCount} derniers événements analysés.
              </p>
            )}
          </section>

          <section className="card" aria-labelledby="evenements-sensibles">
            <header className="card__header">
              <h2 className="card__title" id="evenements-sensibles">
                Événements sensibles
              </h2>
            </header>

            <div className="filters">
              <div className="field field--inline">
                <label className="field__label" htmlFor="securite-gravite">
                  Gravité
                </label>
                <select
                  id="securite-gravite"
                  className="input"
                  value={severity}
                  onChange={(event) => {
                    setSeverity(event.target.value);
                    setPage(1);
                  }}
                >
                  {SENSITIVE_SEVERITIES.map((value) => (
                    <option key={value} value={value}>
                      {describeSeverity(value).label} ({value})
                    </option>
                  ))}
                </select>
              </div>
              <p className="text-muted text-small">
                Seules les gravités WARNING, ERROR et CRITICAL sont proposées sur cette page.
              </p>
            </div>

            <EventTable
              events={data.sensitive.items}
              onSelect={(event: EventItem) => setSelectedEventId(event.id)}
              loading={security.loading}
              refreshing={security.loading && data.sensitive.items.length > 0}
              caption="Événements sensibles"
              emptyTitle="Aucun événement sensible"
              emptyDescription={`Aucun événement de gravité ${severity} n'est journalisé.`}
            />

            <Pagination
              page={data.sensitive.page || page}
              pageSize={data.sensitive.page_size || PAGE_SIZE}
              total={data.sensitive.total}
              onPageChange={setPage}
              disabled={security.loading}
              itemLabel="événements sensibles"
            />
          </section>
        </>
      ) : null}

      {selectedEventId ? (
        <EventDetailModal eventId={selectedEventId} onClose={() => setSelectedEventId(null)} />
      ) : null}
    </>
  );
}
