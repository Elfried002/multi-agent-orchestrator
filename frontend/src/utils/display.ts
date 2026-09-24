/**
 * Libellés français et tonalités visuelles des états renvoyés par le backend.
 * Toute valeur non documentée est affichée telle quelle (tonalité neutre)
 * plutôt que remplacée ou masquée.
 */

import { TERMINAL_TASK_STATUSES } from '../types';

export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

export interface StatusDescriptor {
  label: string;
  tone: Tone;
}

const UNKNOWN: StatusDescriptor = { label: 'Inconnu', tone: 'neutral' };

const AGENT_STATUS: Record<string, StatusDescriptor> = {
  PENDING: { label: 'En attente', tone: 'info' },
  ONLINE: { label: 'En ligne', tone: 'success' },
  OFFLINE: { label: 'Hors ligne', tone: 'neutral' },
  REVOKED: { label: 'Révoqué', tone: 'danger' },
};

const TASK_STATUS: Record<string, StatusDescriptor> = {
  PENDING: { label: 'En attente', tone: 'neutral' },
  ASSIGNED: { label: 'Assignée', tone: 'info' },
  RUNNING: { label: 'En cours', tone: 'info' },
  COMPLETED: { label: 'Terminée', tone: 'success' },
  FAILED: { label: 'En échec', tone: 'danger' },
  CANCELLED: { label: 'Annulée', tone: 'neutral' },
  TIMEOUT: { label: 'Expirée (délai dépassé)', tone: 'warning' },
};

const SEVERITY: Record<string, StatusDescriptor> = {
  DEBUG: { label: 'Débogage', tone: 'neutral' },
  INFO: { label: 'Information', tone: 'info' },
  WARNING: { label: 'Avertissement', tone: 'warning' },
  ERROR: { label: 'Erreur', tone: 'danger' },
  CRITICAL: { label: 'Critique', tone: 'danger' },
};

const PRIORITY: Record<string, StatusDescriptor> = {
  LOW: { label: 'Basse', tone: 'neutral' },
  NORMAL: { label: 'Normale', tone: 'info' },
  HIGH: { label: 'Haute', tone: 'warning' },
  CRITICAL: { label: 'Critique', tone: 'danger' },
};

const ORCHESTRATOR_STATE: Record<string, StatusDescriptor> = {
  ONLINE: { label: 'En ligne', tone: 'success' },
  OFFLINE: { label: 'Hors ligne', tone: 'danger' },
};

const SERVICE_STATUS: Record<string, StatusDescriptor> = {
  RUNNING: { label: 'En exécution', tone: 'success' },
  STOPPED: { label: 'Arrêté', tone: 'neutral' },
  STARTING: { label: 'Démarrage', tone: 'info' },
  ERROR: { label: 'Erreur', tone: 'danger' },
  FAILED: { label: 'En échec', tone: 'danger' },
};

const HEALTH_STATUS: Record<string, StatusDescriptor> = {
  HEALTHY: { label: 'Sain', tone: 'success' },
  DEGRADED: { label: 'Dégradé', tone: 'warning' },
  UNHEALTHY: { label: 'Non sain', tone: 'danger' },
};

const ACTOR_TYPE: Record<string, StatusDescriptor> = {
  ADMIN: { label: 'Administrateur', tone: 'info' },
  AGENT: { label: 'Agent', tone: 'neutral' },
  SYSTEM: { label: 'Système', tone: 'neutral' },
};

function describe(
  table: Record<string, StatusDescriptor>,
  value: string | null | undefined,
): StatusDescriptor {
  if (value === null || value === undefined || value.length === 0) return UNKNOWN;
  return table[value] ?? { label: value, tone: 'neutral' };
}

export function describeAgentStatus(value: string | null | undefined): StatusDescriptor {
  return describe(AGENT_STATUS, value);
}

export function describeTaskStatus(value: string | null | undefined): StatusDescriptor {
  return describe(TASK_STATUS, value);
}

export function describeSeverity(value: string | null | undefined): StatusDescriptor {
  return describe(SEVERITY, value);
}

export function describePriority(value: string | null | undefined): StatusDescriptor {
  return describe(PRIORITY, value);
}

export function describeOrchestratorState(value: string | null | undefined): StatusDescriptor {
  return describe(ORCHESTRATOR_STATE, value);
}

export function describeServiceStatus(value: string | null | undefined): StatusDescriptor {
  return describe(SERVICE_STATUS, value);
}

export function describeHealthStatus(value: string | null | undefined): StatusDescriptor {
  return describe(HEALTH_STATUS, value);
}

export function describeActorType(value: string | null | undefined): StatusDescriptor {
  return describe(ACTOR_TYPE, value);
}

/**
 * API.md §8.4 : l'annulation doit être refusée dans un état terminal.
 * L'interface ne propose donc le bouton que pour un état non terminal.
 */
export function isTerminalTaskStatus(status: string | null | undefined): boolean {
  if (!status) return false;
  return (TERMINAL_TASK_STATUSES as readonly string[]).includes(status);
}

export function canCancelTask(status: string | null | undefined): boolean {
  return !isTerminalTaskStatus(status);
}
