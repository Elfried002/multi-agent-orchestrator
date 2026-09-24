/**
 * Gestion de l'authentification administrateur (ARCHITECTURE.md §6).
 *
 * L'état de session n'est JAMAIS conservé en JavaScript (SECURITY.md §13) :
 * le backend pose un cookie HttpOnly, et l'identité courante est relue via
 * `GET /api/v1/auth/me` à chaque initialisation de l'application.
 */

import type { AdminSession, ChangePasswordPayload } from '../types';
import { ApiError, apiRequest } from './api';

/**
 * `POST /api/v1/auth/login` (API.md §5.1).
 * API.md ne documente pas de corps de réponse : toute réponse 2xx est
 * considérée comme un succès, puis la session est relue via `getCurrentSession`.
 * Un 401 est remonté sans déclencher la redirection globale (déjà sur /login).
 */
export async function login(username: string, password: string): Promise<void> {
  await apiRequest<unknown>('/api/v1/auth/login', {
    method: 'POST',
    body: { username, password },
    suppressUnauthorizedHandler: true,
  });
}

/**
 * `GET /api/v1/auth/me` (API.md §5.2) — source de vérité de l'état de session.
 * `suppressUnauthorizedHandler` est actif : une 401 signifie ici simplement
 * « pas de session », ce n'est pas une expiration en cours de navigation.
 */
export function getCurrentSession(signal?: AbortSignal): Promise<AdminSession> {
  return apiRequest<AdminSession>('/api/v1/auth/me', { signal, suppressUnauthorizedHandler: true });
}

/**
 * `POST /api/v1/auth/logout` (API.md §5.3) — invalide la session côté serveur.
 * Une erreur réseau n'empêche pas la purge locale de l'état applicatif.
 */
export async function logout(): Promise<void> {
  try {
    await apiRequest<unknown>('/api/v1/auth/logout', { method: 'POST' });
  } catch (error) {
    if (error instanceof ApiError && error.isNetworkError) return;
    // Session déjà invalide côté serveur : la purge locale suffit.
    if (error instanceof ApiError && error.status === 401) return;
    throw error;
  }
}

/**
 * `POST /api/v1/auth/change-password` (API.md §5.4).
 * Le backend vérifie le mot de passe actuel avant d'accepter la modification.
 * Aucun mot de passe n'est conservé côté client après l'appel.
 */
export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  const payload: ChangePasswordPayload = {
    current_password: currentPassword,
    new_password: newPassword,
  };
  await apiRequest<unknown>('/api/v1/auth/change-password', { method: 'POST', body: payload });
}
