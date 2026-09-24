/**
 * Types métier partagés du frontend d'administration.
 *
 * Sources de référence : docs/API.md (§5 à §10), docs/ARCHITECTURE.md (§7, §9, §10)
 * et docs/AGENT_CONNECTION.md (§18).
 *
 * Les champs d'état sont typés de manière « tolérante » : les valeurs connues
 * sont proposées en autocomplétion, mais toute autre valeur renvoyée par le
 * backend reste acceptée et affichée telle quelle (aucune donnée n'est inventée
 * ni masquée si le backend renvoie un état non documenté).
 */

/** Valeur acceptée dans une chaîne de requête HTTP. */
export type QueryValue = string | number | boolean | null | undefined;
/** Conteneur de paramètres de requête. */
export type QueryParams = Record<string, QueryValue>;

/* ------------------------------------------------------------------ */
/* États et énumérations documentés                                    */
/* ------------------------------------------------------------------ */

/** ARCHITECTURE.md §9 — présence des agents. */
export const AGENT_STATUSES = ['PENDING', 'ONLINE', 'OFFLINE', 'REVOKED'] as const;
export type AgentStatus = (typeof AGENT_STATUSES)[number] | (string & {});

/** ARCHITECTURE.md §10 — cycle de vie des tâches. */
export const TASK_STATUSES = [
  'PENDING',
  'ASSIGNED',
  'RUNNING',
  'COMPLETED',
  'FAILED',
  'CANCELLED',
  'TIMEOUT',
] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number] | (string & {});

/** États terminaux : l'annulation n'y est jamais proposée (API.md §8.4). */
export const TERMINAL_TASK_STATUSES = ['COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT'] as const;

/**
 * Priorités de tâche. API.md §8.1 ne documente que `NORMAL` ; l'ensemble
 * ci-dessous est la liste de valeurs proposées par l'interface. Le backend
 * reste seul juge : une priorité refusée produit une erreur 422 affichée.
 */
export const TASK_PRIORITIES = ['LOW', 'NORMAL', 'HIGH', 'CRITICAL'] as const;
export type TaskPriority = (typeof TASK_PRIORITIES)[number] | (string & {});

/** SECURITY.md §10 — gravité des événements. */
export const EVENT_SEVERITIES = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const;
export type EventSeverity = (typeof EVENT_SEVERITIES)[number] | (string & {});

/** Gravités considérées comme sensibles (mission §9 — page Sécurité). */
export const SENSITIVE_SEVERITIES = ['WARNING', 'ERROR', 'CRITICAL'] as const;

/** ARCHITECTURE.md §7.4 — nature de l'acteur d'un événement. */
export const ACTOR_TYPES = ['ADMIN', 'AGENT', 'SYSTEM'] as const;
export type ActorType = (typeof ACTOR_TYPES)[number] | (string & {});

/** ARCHITECTURE.md §7.6 — état logique persistant de l'orchestrateur. */
export const ORCHESTRATOR_STATES = ['ONLINE', 'OFFLINE'] as const;
export type OrchestratorState = (typeof ORCHESTRATOR_STATES)[number] | (string & {});

export type ServiceStatus = 'RUNNING' | 'STOPPED' | (string & {});
export type HealthStatus = 'HEALTHY' | 'DEGRADED' | 'UNHEALTHY' | (string & {});

/* ------------------------------------------------------------------ */
/* Entités                                                             */
/* ------------------------------------------------------------------ */

/** API.md §5.2 — session administrateur courante. */
export interface AdminSession {
  id: string;
  username: string;
  is_active: boolean;
  created_at?: string | null;
  last_login_at?: string | null;
}

/**
 * API.md §7.1 / §7.2 — agent enregistré.
 * La liste (`GET /api/v1/agents`) documente un sous-ensemble de champs ;
 * `capabilities`, `created_at` et `revoked_at` sont optionnels : s'ils sont
 * absents de la réponse, l'interface les affiche comme « non fourni » au lieu
 * d'inventer une valeur.
 */
export interface Agent {
  id: string;
  name: string;
  role: string;
  runtime: string;
  status: AgentStatus;
  source_ip?: string | null;
  last_seen_at?: string | null;
  created_at?: string | null;
  capabilities?: string[] | null;
  revoked_at?: string | null;
  credential_id?: string | null;
  metadata?: Record<string, unknown> | null;
}

/** API.md §8 — tâche orchestrée. ARCHITECTURE.md §7.3 pour les champs détaillés. */
export interface Task {
  id: string;
  title: string;
  description?: string | null;
  status: TaskStatus;
  priority?: TaskPriority | null;
  assigned_agent_id?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  result?: unknown;
  error_message?: string | null;
}

/** ARCHITECTURE.md §7.4 — événement journalisé (expurgé de tout secret). */
export interface EventItem {
  id: string;
  event_type: string;
  severity: EventSeverity;
  message: string;
  agent_id?: string | null;
  actor_type?: ActorType | null;
  actor_id?: string | null;
  source_ip?: string | null;
  created_at: string;
  metadata?: Record<string, unknown> | null;
}

/** Enveloppe de pagination renvoyée par les routes de liste (API.md §7.1). */
export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** API.md §9.1 — état de l'orchestrateur. */
export interface OrchestratorStatus {
  desired_state: OrchestratorState;
  service_status: ServiceStatus;
  health_status: HealthStatus;
}

/** API.md §4.1 — route publique de santé. */
export interface HealthResponse {
  status: string;
  service: string;
  version: string;
}

/**
 * API.md §9.3 — clé d'enregistrement.
 * Le contrat ne précise pas le schéma de réponse : le client accepte
 * `enrollment_key` (nom retenu), `key` ou `value`, ainsi qu'une réponse
 * sous forme de chaîne brute.
 */
export interface EnrollmentKey {
  enrollment_key: string;
  created_at?: string | null;
  rotated_at?: string | null;
  expires_at?: string | null;
}

/* ------------------------------------------------------------------ */
/* Paramètres de requête et charges utiles                             */
/* ------------------------------------------------------------------ */

/** API.md §7.1 — filtres de la liste des agents. */
export interface AgentQuery extends QueryParams {
  status?: string;
  role?: string;
  search?: string;
  page?: number;
  page_size?: number;
}

/** API.md §8.2 — filtres de la liste des tâches. */
export interface TaskQuery extends QueryParams {
  status?: string;
  agent_id?: string;
  priority?: string;
  page?: number;
  page_size?: number;
}

/** API.md §10.1 — filtres de la liste des événements. */
export interface LogQuery extends QueryParams {
  severity?: string;
  event_type?: string;
  agent_id?: string;
  start_date?: string;
  end_date?: string;
  page?: number;
  page_size?: number;
}

/** API.md §8.1 — corps de création d'une tâche. */
export interface CreateTaskPayload {
  title: string;
  description: string;
  priority: TaskPriority;
  assigned_agent_id?: string;
}

/** API.md §5.4 — changement de mot de passe administrateur. */
export interface ChangePasswordPayload {
  current_password: string;
  new_password: string;
}
