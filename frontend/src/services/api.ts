/**
 * Client HTTP centralisé du frontend d'administration (ARCHITECTURE.md §6).
 *
 * Règles non négociables implémentées ici :
 * - préfixe API `/api/v1` (API.md §1), la route de santé `GET /health` étant
 *   hors préfixe (API.md §4.1) ;
 * - `credentials: 'include'` sur CHAQUE requête : la session administrateur est
 *   portée par un cookie HttpOnly posé par le backend (SECURITY.md §4) ;
 * - aucun jeton n'est lu ni écrit dans localStorage/sessionStorage
 *   (SECURITY.md §13) : l'état de session est obtenu via `GET /api/v1/auth/me` ;
 * - toute réponse non-2xx devient une `ApiError` dont le message provient du
 *   champ `error.message` du backend (API.md §2.3), avec repli sur un message
 *   par code HTTP si le corps n'est pas exploitable ;
 * - les erreurs réseau sont distinguées des erreurs métier (`kind`).
 */

import type {
  Agent,
  AgentQuery,
  CreateTaskPayload,
  EnrollmentKey,
  EventItem,
  HealthResponse,
  LogQuery,
  OrchestratorState,
  OrchestratorStatus,
  Paginated,
  QueryParams,
  Task,
  TaskQuery,
} from '../types';

/** Préfixe de toutes les routes métier (API.md §1). */
export const API_PREFIX = '/api/v1';

/**
 * Origine de l'API. Vide en développement : les requêtes partent sur l'origine
 * du frontend et le proxy Vite les relaie vers http://127.0.0.1:8000.
 * En production, `VITE_API_BASE_URL` peut porter l'origine publique du backend.
 */
const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

/** Nature de l'erreur, pour distinguer panne réseau et refus métier. */
export type ApiErrorKind = 'network' | 'http' | 'parse' | 'aborted';

/** Erreur HTTP typée produite par le client. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly requestId: string | null;
  readonly detail: unknown;

  constructor(
    message: string,
    options: {
      kind: ApiErrorKind;
      status?: number | null;
      code?: string | null;
      requestId?: string | null;
      detail?: unknown;
    },
  ) {
    super(message);
    this.name = 'ApiError';
    this.kind = options.kind;
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.requestId = options.requestId ?? null;
    this.detail = options.detail ?? null;
  }

  /** Session absente ou expirée : l'interface doit rediriger vers /login. */
  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isNetworkError(): boolean {
    return this.kind === 'network';
  }

  get isAborted(): boolean {
    return this.kind === 'aborted';
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Détecte une requête annulée (démontage de composant, changement de filtre). */
export function isAbortError(error: unknown): boolean {
  if (error instanceof ApiError) return error.isAborted;
  if (error instanceof DOMException) return error.name === 'AbortError';
  const candidate = error as { name?: string } | null;
  return typeof candidate?.name === 'string' && candidate.name === 'AbortError';
}

/* ------------------------------------------------------------------ */
/* Gestion globale de l'expiration de session                          */
/* ------------------------------------------------------------------ */

let unauthorizedHandler: (() => void) | null = null;

/**
 * Enregistre le rappel invoqué dès qu'une réponse 401 est reçue, afin que
 * l'application repasse en état « non authentifié » et redirige vers /login.
 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/* ------------------------------------------------------------------ */
/* Utilitaires internes                                                */
/* ------------------------------------------------------------------ */

function apiPath(...segments: string[]): string {
  return `${API_PREFIX}${segments.map((segment) => `/${encodeURIComponent(segment)}`).join('')}`;
}

function buildUrl(path: string, query?: QueryParams): string {
  const url = `${API_BASE_URL}${path}`;
  if (!query) return url;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    search.append(key, String(value));
  }
  const queryString = search.toString();
  return queryString ? `${url}?${queryString}` : url;
}

function readCookie(name: string): string | null {
  if (typeof document === 'undefined' || !document.cookie) return null;
  const prefix = `${name}=`;
  for (const part of document.cookie.split('; ')) {
    if (part.startsWith(prefix)) return decodeURIComponent(part.slice(prefix.length));
  }
  return null;
}

/**
 * Protection CSRF (SECURITY.md §4). Si le backend accompagne le cookie de
 * session HttpOnly d'un cookie CSRF lisible (double-submit), sa valeur est
 * renvoyée dans l'en-tête `X-CSRF-Token`. Aucun secret n'est conservé en
 * JavaScript : la valeur est relue dans le cookie à chaque requête mutative.
 */
function readCsrfToken(): string | null {
  return readCookie('csrf_token') ?? readCookie('csrfToken') ?? readCookie('XSRF-TOKEN');
}

/** Message de repli lorsqu'une erreur HTTP n'a pas de corps exploitable. */
function fallbackMessage(status: number): string {
  switch (status) {
    case 400:
      return 'Requête invalide.';
    case 401:
      return 'Session administrateur absente ou expirée.';
    case 403:
      return 'Autorisation insuffisante pour cette opération.';
    case 404:
      return 'Ressource introuvable.';
    case 409:
      return "Conflit d'état : l'opération n'est pas possible dans l'état actuel.";
    case 422:
      return 'Données invalides.';
    case 429:
      return 'Trop de requêtes : réessayez dans quelques instants.';
    case 500:
      return 'Erreur interne du serveur.';
    case 503:
      return 'Service temporairement indisponible.';
    default:
      return `Erreur HTTP ${status}.`;
  }
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function asNonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null;
}

/** Extrait le message du format d'erreur normalisé `{ "error": {...} }`. */
async function toHttpError(response: Response): Promise<ApiError> {
  let code: string | null = null;
  let message: string | null = null;
  let requestId: string | null = null;

  try {
    const payload: unknown = await response.json();
    const body = asRecord(payload);
    const errorBody = asRecord(body?.error);
    code = asNonEmptyString(errorBody?.code);
    message = asNonEmptyString(errorBody?.message);
    requestId = asNonEmptyString(errorBody?.request_id);
  } catch {
    // Corps absent ou non JSON : le message de repli par code HTTP est utilisé.
  }

  return new ApiError(message ?? fallbackMessage(response.status), {
    kind: 'http',
    status: response.status,
    code,
    requestId,
  });
}

export interface RequestOptions {
  method?: HttpMethod;
  query?: QueryParams;
  body?: unknown;
  signal?: AbortSignal;
  /** Empêche la notification globale 401 (sonde de session, échec de connexion). */
  suppressUnauthorizedHandler?: boolean;
}

/**
 * Exécute une requête HTTP vers l'API et renvoie la charge utile JSON.
 * `undefined` est renvoyé pour les réponses 204 ou vides.
 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', query, body, signal, suppressUnauthorizedHandler = false } = options;

  const headers = new Headers({ Accept: 'application/json' });
  if (body !== undefined) headers.set('Content-Type', 'application/json');
  if (method !== 'GET') {
    const csrfToken = readCsrfToken();
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'include',
      cache: 'no-store',
      signal,
    });
  } catch (error) {
    if (isAbortError(error)) {
      throw new ApiError('Requête annulée.', { kind: 'aborted' });
    }
    throw new ApiError(
      "Impossible de contacter le serveur. Vérifiez que le backend est démarré et que le réseau est disponible.",
      { kind: 'network', detail: error },
    );
  }

  if (!response.ok) {
    if (response.status === 401 && !suppressUnauthorizedHandler) {
      unauthorizedHandler?.();
    }
    throw await toHttpError(response);
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  if (text.length === 0) return undefined as T;

  try {
    return JSON.parse(text) as T;
  } catch (error) {
    throw new ApiError('Réponse du serveur illisible (JSON invalide).', {
      kind: 'parse',
      status: response.status,
      detail: error,
    });
  }
}

/**
 * Normalise une réponse de liste en enveloppe paginée.
 * API.md §7.1 documente `{ items, total, page, page_size }` ; les routes §8.2 et
 * §10.1 ne documentent pas l'enveloppe. Un tableau brut est donc également
 * accepté, sans jamais fabriquer de données : les valeurs manquantes sont
 * déduites de la taille réelle de la page reçue.
 */
export function normalizePage<T>(payload: unknown): Paginated<T> {
  if (Array.isArray(payload)) {
    const items = payload.filter((item): item is T => item !== null && typeof item === 'object');
    return { items, total: items.length, page: 1, page_size: items.length };
  }

  const record = asRecord(payload);
  const rawItems = record && Array.isArray(record.items) ? (record.items as unknown[]) : [];
  const items = rawItems.filter((item): item is T => item !== null && typeof item === 'object');
  const total = record && typeof record.total === 'number' ? record.total : items.length;
  const page = record && typeof record.page === 'number' ? record.page : 1;
  const pageSize = record && typeof record.page_size === 'number' ? record.page_size : items.length;

  return { items, total, page, page_size: pageSize };
}

/** Détecte une charge utile de tâche renvoyée par une route d'action. */
function isTaskLike(payload: unknown): payload is Task {
  const record = asRecord(payload);
  return record !== null && typeof record.id === 'string' && typeof record.status === 'string';
}

/* ------------------------------------------------------------------ */
/* Route publique de santé (API.md §4.1)                               */
/* ------------------------------------------------------------------ */

export const healthApi = {
  /** `GET /health` — aucune authentification, hors préfixe `/api/v1`. */
  getHealth(signal?: AbortSignal): Promise<HealthResponse> {
    return apiRequest<HealthResponse>('/health', { signal, suppressUnauthorizedHandler: true });
  },
};

/* ------------------------------------------------------------------ */
/* Agents (API.md §7)                                                  */
/* ------------------------------------------------------------------ */

export const agentsApi = {
  /** `GET /api/v1/agents` — filtres status / role / search + pagination. */
  list(query: AgentQuery = {}, signal?: AbortSignal): Promise<Paginated<Agent>> {
    return apiRequest<unknown>(apiPath('agents'), { query, signal }).then((payload) =>
      normalizePage<Agent>(payload),
    );
  },

  /** `GET /api/v1/agents/{agent_id}` — fiche complète d'un agent. */
  get(agentId: string, signal?: AbortSignal): Promise<Agent> {
    return apiRequest<Agent>(apiPath('agents', agentId), { signal });
  },

  /** `POST /api/v1/agents/{agent_id}/disconnect` — déconnexion logique. */
  disconnect(agentId: string): Promise<void> {
    return apiRequest<void>(apiPath('agents', agentId, 'disconnect'), { method: 'POST' });
  },

  /** `POST /api/v1/agents/{agent_id}/revoke` — révocation définitive des jetons. */
  revoke(agentId: string): Promise<void> {
    return apiRequest<void>(apiPath('agents', agentId, 'revoke'), { method: 'POST' });
  },
};

/* ------------------------------------------------------------------ */
/* Tâches (API.md §8)                                                  */
/* ------------------------------------------------------------------ */

export const tasksApi = {
  /** `GET /api/v1/tasks` — filtres status / agent_id / priority + pagination. */
  list(query: TaskQuery = {}, signal?: AbortSignal): Promise<Paginated<Task>> {
    return apiRequest<unknown>(apiPath('tasks'), { query, signal }).then((payload) =>
      normalizePage<Task>(payload),
    );
  },

  /** `GET /api/v1/tasks/{task_id}` — détail d'une tâche. */
  get(taskId: string, signal?: AbortSignal): Promise<Task> {
    return apiRequest<Task>(apiPath('tasks', taskId), { signal });
  },

  /** `POST /api/v1/tasks` — création d'une tâche par l'administrateur. */
  create(payload: CreateTaskPayload, signal?: AbortSignal): Promise<Task> {
    return apiRequest<Task>(apiPath('tasks'), { method: 'POST', body: payload, signal });
  },

  /**
   * `POST /api/v1/tasks/{task_id}/cancel` — annulation.
   * API.md §8.4 ne précise pas le corps de réponse : la tâche mise à jour est
   * renvoyée si le backend la fournit, `null` sinon.
   */
  async cancel(taskId: string): Promise<Task | null> {
    const payload = await apiRequest<unknown>(apiPath('tasks', taskId, 'cancel'), { method: 'POST' });
    return isTaskLike(payload) ? payload : null;
  },
};

/* ------------------------------------------------------------------ */
/* Journaux (API.md §10)                                               */
/* ------------------------------------------------------------------ */

export const logsApi = {
  /** `GET /api/v1/logs` — filtres severity / event_type / agent_id / dates. */
  list(query: LogQuery = {}, signal?: AbortSignal): Promise<Paginated<EventItem>> {
    return apiRequest<unknown>(apiPath('logs'), { query, signal }).then((payload) =>
      normalizePage<EventItem>(payload),
    );
  },

  /** `GET /api/v1/logs/{event_id}` — détail d'un événement (expurgé). */
  get(eventId: string, signal?: AbortSignal): Promise<EventItem> {
    return apiRequest<EventItem>(apiPath('logs', eventId), { signal });
  },
};

/* ------------------------------------------------------------------ */
/* Orchestrateur et clé d'enregistrement (API.md §9)                   */
/* ------------------------------------------------------------------ */

/**
 * Extrait la clé d'enregistrement de la réponse de `GET /settings/enrollment-key`.
 * Le contrat ne fixe pas le nom du champ : `enrollment_key` est retenu en
 * priorité, puis `key` et `value`, puis une réponse sous forme de chaîne.
 */
export function extractEnrollmentKey(payload: unknown): EnrollmentKey {
  if (typeof payload === 'string' && payload.trim().length > 0) {
    return { enrollment_key: payload };
  }

  const record = asRecord(payload);
  const key =
    asNonEmptyString(record?.enrollment_key) ??
    asNonEmptyString(record?.key) ??
    asNonEmptyString(record?.value);

  if (!key) {
    throw new ApiError(
      "La réponse du serveur ne contient pas de clé d'enregistrement (champ « enrollment_key » attendu).",
      { kind: 'parse', detail: payload },
    );
  }

  return {
    enrollment_key: key,
    created_at: asNonEmptyString(record?.created_at),
    rotated_at: asNonEmptyString(record?.rotated_at),
    expires_at: asNonEmptyString(record?.expires_at),
  };
}

export const settingsApi = {
  /** `GET /api/v1/settings/orchestrator` — état logique, service et santé. */
  getOrchestrator(signal?: AbortSignal): Promise<OrchestratorStatus> {
    return apiRequest<OrchestratorStatus>(apiPath('settings', 'orchestrator'), { signal });
  },

  /** `POST /api/v1/settings/orchestrator/state` — bascule ONLINE / OFFLINE. */
  setOrchestratorState(desiredState: OrchestratorState, signal?: AbortSignal): Promise<OrchestratorStatus | null> {
    return apiRequest<unknown>(apiPath('settings', 'orchestrator', 'state'), {
      method: 'POST',
      body: { desired_state: desiredState },
      signal,
    }).then((payload) => {
      const record = asRecord(payload);
      return record && typeof record.desired_state === 'string'
        ? (payload as OrchestratorStatus)
        : null;
    });
  },

  /** `GET /api/v1/settings/enrollment-key` — consultation journalisée. */
  async getEnrollmentKey(signal?: AbortSignal): Promise<EnrollmentKey> {
    const payload = await apiRequest<unknown>(apiPath('settings', 'enrollment-key'), { signal });
    return extractEnrollmentKey(payload);
  },

  /**
   * `POST /api/v1/settings/enrollment-key/rotate` — révoque l'ancienne clé.
   * Si le backend renvoie la nouvelle clé, elle est exploitée directement ;
   * sinon le client relit `GET /settings/enrollment-key`.
   */
  async rotateEnrollmentKey(): Promise<EnrollmentKey | null> {
    const payload = await apiRequest<unknown>(apiPath('settings', 'enrollment-key', 'rotate'), {
      method: 'POST',
    });
    const record = asRecord(payload);
    if (!record) return null;
    try {
      return extractEnrollmentKey(payload);
    } catch {
      return null;
    }
  },
};
