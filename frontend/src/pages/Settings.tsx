/**
 * Paramètres (docs/API.md §9 et §5.2 à §5.4) :
 * - état logique ONLINE / OFFLINE de l'orchestrateur avec bascule confirmée ;
 * - état du service et santé du backend (`GET /health`) ;
 * - consultation et rotation de la clé d'enregistrement, protégées par
 *   confirmation et affichées uniquement à la demande ;
 * - informations du compte administrateur et changement de mot de passe.
 */

import { useState, type FormEvent } from 'react';
import { ErrorAlert, Notice } from '../components/ui/Alert';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
import { DetailList } from '../components/ui/DetailList';
import { Field } from '../components/ui/Field';
import { LoadingState } from '../components/ui/LoadingState';
import { PageHeader } from '../components/ui/PageHeader';
import { StatusBadge } from '../components/ui/StatusBadge';
import { useAction } from '../hooks/useAction';
import { useAsyncData } from '../hooks/useAsyncData';
import { useSession } from '../hooks/useSession';
import { healthApi, settingsApi } from '../services/api';
import { ORCHESTRATOR_STATES, type EnrollmentKey, type HealthResponse, type OrchestratorState } from '../types';
import { describeHealthStatus, describeOrchestratorState, describeServiceStatus } from '../utils/display';
import { describeError } from '../utils/errors';
import { formatDateTime, formatText } from '../utils/format';

export function Settings(): React.ReactElement {
  const { admin, refresh, changePassword } = useSession();

  const [nonce, setNonce] = useState<number>(0);
  const [confirmState, setConfirmState] = useState<OrchestratorState | null>(null);
  const [stateFeedback, setStateFeedback] = useState<string | null>(null);

  const [enrollmentKey, setEnrollmentKey] = useState<EnrollmentKey | null>(null);
  const [keyLoading, setKeyLoading] = useState<boolean>(false);
  const [keyError, setKeyError] = useState<string | null>(null);
  const [copyFeedback, setCopyFeedback] = useState<string | null>(null);
  const [confirmRotate, setConfirmRotate] = useState<boolean>(false);
  const [rotateFeedback, setRotateFeedback] = useState<string | null>(null);

  const [currentPassword, setCurrentPassword] = useState<string>('');
  const [newPassword, setNewPassword] = useState<string>('');
  const [confirmPassword, setConfirmPassword] = useState<string>('');
  const [passwordValidation, setPasswordValidation] = useState<string | null>(null);
  const [passwordFeedback, setPasswordFeedback] = useState<string | null>(null);

  const overview = useAsyncData(`settings:${nonce}`, async (signal) => {
    const status = await settingsApi.getOrchestrator(signal);
    const health = await healthApi.getHealth(signal).then(
      (value: HealthResponse): { value: HealthResponse | null; error: string | null } => ({
        value,
        error: null,
      }),
      (caught: unknown): { value: HealthResponse | null; error: string | null } => ({
        value: null,
        error: describeError(caught),
      }),
    );
    return { status, health };
  });

  const stateAction = useAction(async (state: OrchestratorState) => {
    const updated = await settingsApi.setOrchestratorState(state);
    setStateFeedback(
      updated
        ? `État logique appliqué : ${updated.desired_state}.`
        : `Demande d'état « ${state} » transmise ; l'état affiché provient de GET /api/v1/settings/orchestrator.`,
    );
  });

  const rotateAction = useAction(async () => {
    const rotated = await settingsApi.rotateEnrollmentKey();
    if (rotated) {
      setEnrollmentKey(rotated);
    } else {
      const fresh = await settingsApi.getEnrollmentKey();
      setEnrollmentKey(fresh);
    }
    setRotateFeedback(
      "Nouvelle clé générée : l'ancienne clé est révoquée et les agents qui n'utilisaient que celle-ci ne peuvent plus s'enregistrer.",
    );
  });

  const passwordAction = useAction(async () => {
    await changePassword(currentPassword, newPassword);
    setPasswordFeedback('Mot de passe modifié. La session courante reste active.');
    setCurrentPassword('');
    setNewPassword('');
    setConfirmPassword('');
  });

  const handleStateConfirm = async (): Promise<void> => {
    if (!confirmState) return;
    const target = confirmState;
    const succeeded = await stateAction.run(target);
    if (!succeeded) return;
    setConfirmState(null);
    setNonce((value) => value + 1);
  };

  const handleRotateConfirm = async (): Promise<void> => {
    const succeeded = await rotateAction.run();
    if (!succeeded) return;
    setConfirmRotate(false);
  };

  const loadEnrollmentKey = async (): Promise<void> => {
    setKeyLoading(true);
    setKeyError(null);
    setCopyFeedback(null);
    try {
      const key = await settingsApi.getEnrollmentKey();
      setEnrollmentKey(key);
    } catch (caught: unknown) {
      setKeyError(describeError(caught));
    } finally {
      setKeyLoading(false);
    }
  };

  const copyEnrollmentKey = async (value: string): Promise<void> => {
    setCopyFeedback(null);
    try {
      if (!navigator.clipboard) {
        throw new Error('Presse-papiers indisponible dans ce navigateur.');
      }
      await navigator.clipboard.writeText(value);
      setCopyFeedback('Clé copiée dans le presse-papiers.');
    } catch (caught: unknown) {
      setCopyFeedback(`Copie impossible : ${describeError(caught)}`);
    }
  };

  const handlePasswordSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setPasswordValidation(null);
    setPasswordFeedback(null);

    if (currentPassword.length === 0 || newPassword.length === 0) {
      setPasswordValidation('Le mot de passe actuel et le nouveau mot de passe sont obligatoires.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordValidation('La confirmation ne correspond pas au nouveau mot de passe.');
      return;
    }

    await passwordAction.run();
  };

  const orchestrator = overview.data?.status ?? null;
  const health = overview.data?.health ?? { value: null, error: null };

  return (
    <>
      <PageHeader
        title="Paramètres"
        description="État de l'orchestrateur, clé d'enregistrement et compte administrateur."
        actions={
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => setNonce((value) => value + 1)}
            disabled={overview.loading}
          >
            {overview.loading ? 'Actualisation…' : 'Rafraîchir'}
          </button>
        }
      />

      {overview.error ? <ErrorAlert message={overview.error} onRetry={overview.reload} /> : null}
      {overview.loading && !overview.data ? (
        <LoadingState label="Chargement de l'état de l'orchestrateur…" />
      ) : null}

      <section className="card" aria-labelledby="etat-logique">
        <header className="card__header">
          <h2 className="card__title" id="etat-logique">
            État logique de l'orchestrateur
          </h2>
        </header>

        {stateFeedback ? <Notice tone="success">{stateFeedback}</Notice> : null}
        {stateAction.error ? <ErrorAlert message={stateAction.error} /> : null}

        {orchestrator ? (
          <>
            <DetailList
              items={[
                {
                  label: 'État souhaité',
                  value: (() => {
                    const descriptor = describeOrchestratorState(orchestrator.desired_state);
                    return <StatusBadge label={descriptor.label} tone={descriptor.tone} />;
                  })(),
                },
                {
                  label: 'État du service',
                  value: (() => {
                    const descriptor = describeServiceStatus(orchestrator.service_status);
                    return <StatusBadge label={descriptor.label} tone={descriptor.tone} />;
                  })(),
                },
                {
                  label: 'Santé du backend',
                  value: (() => {
                    const descriptor = describeHealthStatus(orchestrator.health_status);
                    return <StatusBadge label={descriptor.label} tone={descriptor.tone} />;
                  })(),
                },
                {
                  label: 'Route publique /health',
                  value: health.value ? (
                    <>
                      <StatusBadge
                        label={health.value.status === 'ok' ? 'OK' : health.value.status}
                        tone={health.value.status === 'ok' ? 'success' : 'warning'}
                      />
                      <span className="details__inline">
                        {formatText(health.value.service)} — version {formatText(health.value.version)}
                      </span>
                    </>
                  ) : health.error ? (
                    <span className="text-muted">{health.error}</span>
                  ) : (
                    <span className="text-muted">Non interrogée.</span>
                  ),
                },
              ]}
            />

            <div className="actions-row">
              {ORCHESTRATOR_STATES.map((state) => (
                <button
                  key={state}
                  type="button"
                  className={state === 'OFFLINE' ? 'btn btn--danger' : 'btn btn--primary'}
                  disabled={stateAction.busy || orchestrator.desired_state === state}
                  onClick={() => {
                    stateAction.reset();
                    setStateFeedback(null);
                    setConfirmState(state);
                  }}
                >
                  {state === 'OFFLINE' ? 'Passer OFFLINE' : 'Passer ONLINE'}
                </button>
              ))}
            </div>
            <p className="text-muted text-small">
              En état OFFLINE, le backend refuse les enregistrements et les opérations métier des
              agents ; la console d'administration reste accessible.
            </p>
          </>
        ) : null}
      </section>

      <section className="card" aria-labelledby="cle-enregistrement">
        <header className="card__header">
          <h2 className="card__title" id="cle-enregistrement">
            Clé d'enregistrement des agents
          </h2>
        </header>

        <p className="text-muted">
          Cette clé est un secret : sa consultation est journalisée par le backend et elle ne doit
          pas être transmise par un canal non sécurisé.
        </p>

        {keyError ? <ErrorAlert message={keyError} onRetry={() => void loadEnrollmentKey()} /> : null}
        {rotateAction.error ? <ErrorAlert message={rotateAction.error} /> : null}
        {rotateFeedback ? <Notice tone="success">{rotateFeedback}</Notice> : null}
        {copyFeedback ? <Notice tone={copyFeedback.startsWith('Copie impossible') ? 'error' : 'info'}>{copyFeedback}</Notice> : null}

        <div className="actions-row">
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => void loadEnrollmentKey()}
            disabled={keyLoading}
          >
            {keyLoading ? 'Lecture…' : enrollmentKey ? 'Relire la clé' : "Afficher la clé d'enregistrement"}
          </button>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => {
              setEnrollmentKey(null);
              setCopyFeedback(null);
            }}
            disabled={!enrollmentKey}
          >
            Masquer la clé
          </button>
          <button
            type="button"
            className="btn btn--danger"
            onClick={() => {
              rotateAction.reset();
              setRotateFeedback(null);
              setConfirmRotate(true);
            }}
            disabled={rotateAction.busy}
          >
            Révoquer et générer une nouvelle clé
          </button>
        </div>

        {enrollmentKey ? (
          <>
            <h3 className="section-title">Clé courante</h3>
            <pre className="code-block code-block--secret">{enrollmentKey.enrollment_key}</pre>
            <div className="actions-row">
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => void copyEnrollmentKey(enrollmentKey.enrollment_key)}
              >
                Copier la clé
              </button>
            </div>
            <DetailList
              items={[
                { label: 'Créée le', value: formatDateTime(enrollmentKey.created_at) },
                { label: 'Dernière rotation', value: formatDateTime(enrollmentKey.rotated_at) },
                { label: 'Expiration', value: formatDateTime(enrollmentKey.expires_at) },
              ]}
            />
          </>
        ) : (
          <p className="text-muted">
            La clé n'est pas affichée : elle est lue à la demande via
            <code className="code"> GET /api/v1/settings/enrollment-key</code>.
          </p>
        )}
      </section>

      <section className="card" aria-labelledby="compte-admin">
        <header className="card__header">
          <h2 className="card__title" id="compte-admin">
            Compte administrateur
          </h2>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => void refresh()}
          >
            Recharger la session
          </button>
        </header>

        <DetailList
          items={[
            { label: 'Identifiant', value: <span className="mono-sm">{formatText(admin?.id)}</span> },
            { label: "Nom d'utilisateur", value: formatText(admin?.username) },
            {
              label: 'Compte actif',
              value: admin ? (
                <StatusBadge
                  label={admin.is_active ? 'Actif' : 'Désactivé'}
                  tone={admin.is_active ? 'success' : 'danger'}
                />
              ) : (
                <span className="text-muted">Session non chargée</span>
              ),
            },
            { label: 'Créé le', value: formatDateTime(admin?.created_at) },
            { label: 'Dernière connexion', value: formatDateTime(admin?.last_login_at) },
          ]}
        />

        <h3 className="section-title">Changement de mot de passe</h3>
        {passwordFeedback ? <Notice tone="success">{passwordFeedback}</Notice> : null}
        {passwordAction.error ? <ErrorAlert message={passwordAction.error} /> : null}

        <form className="form form--grid" onSubmit={(event) => void handlePasswordSubmit(event)} noValidate>
          <Field label="Mot de passe actuel" htmlFor="mot-de-passe-actuel" required>
            <input
              id="mot-de-passe-actuel"
              name="current_password"
              className="input"
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              required
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
          </Field>

          <Field label="Nouveau mot de passe" htmlFor="nouveau-mot-de-passe" required>
            <input
              id="nouveau-mot-de-passe"
              name="new_password"
              className="input"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              required
              onChange={(event) => setNewPassword(event.target.value)}
            />
          </Field>

          <Field label="Confirmation du nouveau mot de passe" htmlFor="confirmation-mot-de-passe" required>
            <input
              id="confirmation-mot-de-passe"
              name="confirm_password"
              className="input"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              required
              onChange={(event) => setConfirmPassword(event.target.value)}
            />
          </Field>

          {passwordValidation ? (
            <p className="alert alert--error" role="alert">
              {passwordValidation}
            </p>
          ) : null}

          <div className="actions-row">
            <button type="submit" className="btn btn--primary" disabled={passwordAction.busy}>
              {passwordAction.busy ? 'Enregistrement…' : 'Modifier le mot de passe'}
            </button>
          </div>
        </form>
      </section>

      <ConfirmDialog
        open={confirmState !== null}
        title="Changement d'état de l'orchestrateur"
        message={
          confirmState === 'OFFLINE'
            ? "Confirmer le passage de l'orchestrateur en OFFLINE ? Les nouveaux enregistrements d'agents et les opérations métier seront refusés ; le tableau de bord reste accessible."
            : "Confirmer le passage de l'orchestrateur en ONLINE ? Les agents autorisés pourront s'enregistrer, envoyer leurs heartbeats et recevoir des tâches."
        }
        confirmLabel={confirmState === 'OFFLINE' ? 'Passer OFFLINE' : 'Passer ONLINE'}
        danger={confirmState === 'OFFLINE'}
        busy={stateAction.busy}
        error={stateAction.error}
        onConfirm={() => void handleStateConfirm()}
        onClose={() => {
          if (!stateAction.busy) {
            setConfirmState(null);
            stateAction.reset();
          }
        }}
      />

      <ConfirmDialog
        open={confirmRotate}
        title="Rotation de la clé d'enregistrement"
        message="Confirmer la révocation de la clé d'enregistrement actuelle et la génération d'une nouvelle clé ? Les agents qui ne disposent que de l'ancienne clé ne pourront plus s'enregistrer."
        confirmLabel="Révoquer et générer"
        danger
        busy={rotateAction.busy}
        error={rotateAction.error}
        onConfirm={() => void handleRotateConfirm()}
        onClose={() => {
          if (!rotateAction.busy) {
            setConfirmRotate(false);
            rotateAction.reset();
          }
        }}
      />
    </>
  );
}
