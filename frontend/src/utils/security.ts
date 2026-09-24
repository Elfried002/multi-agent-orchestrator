/**
 * Classification des événements de sécurité (page Sécurité).
 *
 * L'API ne fournit pas d'énumération fermée des `event_type` (docs/API.md §10).
 * Ces fonctions dérivent donc, dans le navigateur, les catégories attendues par
 * la mission (§9 : connexions échouées, alertes) à partir des événements
 * RÉELLEMENT renvoyés par `GET /api/v1/logs`. Aucun événement n'est fabriqué :
 * seuls les enregistrements reçus sont filtrés, et ce classement heuristique est
 * signalé explicitement dans l'interface.
 */

import type { EventItem } from '../types';

const AUTH_EVENT_PATTERN = /(auth|login|log_in|log-in|signin|sign_in|session|credential|password|mot_de_passe)/i;
const FAILURE_PATTERN = /(fail|failed|failure|echec|échec|invalide|invalid|denied|refus|unauthor|forbidden|expired|expiré|bloque|blocked)/i;

/** L'événement concerne-t-il l'authentification ou la gestion des sessions ? */
export function isAuthRelatedEvent(event: EventItem): boolean {
  const type = event.event_type ?? '';
  const message = event.message ?? '';
  return AUTH_EVENT_PATTERN.test(type) || AUTH_EVENT_PATTERN.test(message);
}

/**
 * L'événement ressemble-t-il à une tentative de connexion échouée ou à une
 * anomalie d'authentification ? Signaux utilisés : type d'événement explicite,
 * message, ou gravité non informative sur un événement d'authentification.
 */
export function looksLikeAuthenticationFailure(event: EventItem): boolean {
  if (!isAuthRelatedEvent(event)) return false;
  const type = event.event_type ?? '';
  const message = event.message ?? '';
  if (FAILURE_PATTERN.test(type) || FAILURE_PATTERN.test(message)) return true;
  return event.severity === 'WARNING' || event.severity === 'ERROR' || event.severity === 'CRITICAL';
}

/** Gravités considérées comme des alertes de sécurité (SECURITY.md §11). */
export function isAlertSeverity(severity: string | null | undefined): boolean {
  return severity === 'WARNING' || severity === 'ERROR' || severity === 'CRITICAL';
}

/** Gravités critiques, mises en avant en priorité. */
export function isCriticalSeverity(severity: string | null | undefined): boolean {
  return severity === 'CRITICAL';
}
