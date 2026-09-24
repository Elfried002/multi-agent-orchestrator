/**
 * Formatage des valeurs affichées (dates ISO 8601 UTC, valeurs absentes).
 * Aucune valeur de remplacement n'est inventée : un champ absent est rendu
 * par le tiret cadratin et signalé comme « non fourni ».
 */

/** Représentation d'un champ absent de la réponse du backend. */
export const EMPTY_VALUE = '—';

const dateTimeFormatter = new Intl.DateTimeFormat('fr-FR', {
  dateStyle: 'short',
  timeStyle: 'medium',
  timeZone: 'UTC',
});

const dateFormatter = new Intl.DateTimeFormat('fr-FR', {
  dateStyle: 'medium',
  timeZone: 'UTC',
});

function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** `2026-09-24T12:00:00Z` → `24/09/2026 12:00:00 UTC`. */
export function formatDateTime(value: string | null | undefined): string {
  const parsed = parse(value);
  return parsed ? `${dateTimeFormatter.format(parsed)} UTC` : EMPTY_VALUE;
}

/** Date seule, en UTC. */
export function formatDate(value: string | null | undefined): string {
  const parsed = parse(value);
  return parsed ? dateFormatter.format(parsed) : EMPTY_VALUE;
}

/**
 * Convertit la valeur d'un `<input type="datetime-local">` en horodatage
 * ISO 8601 UTC, format attendu par l'API (docs/API.md §2.1).
 * La valeur saisie est interprétée dans le fuseau du navigateur puis convertie.
 */
export function localDateTimeToIso(value: string): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

/** Affiche une valeur textuelle facultative. */
export function formatText(value: string | null | undefined): string {
  return value && value.trim().length > 0 ? value : EMPTY_VALUE;
}

/** Affiche une liste de capacités déclarées, ou le tiret si non fournie. */
export function formatList(values: string[] | null | undefined): string {
  if (!values || values.length === 0) return EMPTY_VALUE;
  return values.join(', ');
}

/** Sérialise un résultat ou des métadonnées JSON pour affichage. */
export function formatJson(value: unknown): string {
  if (value === null || value === undefined) return EMPTY_VALUE;
  if (typeof value === 'string') return value.length > 0 ? value : EMPTY_VALUE;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
