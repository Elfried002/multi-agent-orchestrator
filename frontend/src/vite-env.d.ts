/// <reference types="vite/client" />

/**
 * Variables d'environnement exposées au bundle par Vite.
 *
 * ATTENTION (SECURITY.md §13 et §15) : ce fichier ne doit contenir AUCUN secret.
 * `VITE_API_BASE_URL` est une simple URL publique ; en développement la valeur
 * par défaut (chaîne vide = même origine) est utilisée et le proxy Vite relaie
 * `/api` et `/health` vers http://127.0.0.1:8000.
 */
interface ImportMetaEnv {
  /** URL de base de l'API en production (ex. https://orchestrateur.example.com). */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
