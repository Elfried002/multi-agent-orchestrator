/**
 * Traduction d'une erreur technique en message affichable.
 * Le message du backend (champ `error.message`, docs/API.md §2.3) est prioritaire ;
 * une erreur réseau produit un message distinct, jamais un message métier.
 */

import { isApiError } from '../services/api';

export function describeError(error: unknown): string {
  if (isApiError(error)) {
    if (error.isAborted) return 'Requête annulée.';
    return error.message;
  }
  if (error instanceof Error && error.message.length > 0) return error.message;
  return 'Erreur inattendue. Consultez les journaux du backend.';
}

/** Code d'erreur normalisé renvoyé par le backend, s'il est disponible. */
export function errorCode(error: unknown): string | null {
  return isApiError(error) ? error.code : null;
}
