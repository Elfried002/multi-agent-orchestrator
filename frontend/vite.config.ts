import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Configuration Vite du frontend d'administration.
 *
 * En développement, le backend FastAPI écoute sur http://127.0.0.1:8000.
 * Les requêtes `/api/*` (préfixe API `/api/v1`) et `/health` (route publique de
 * santé, hors préfixe) sont relayées vers le backend par le proxy de
 * développement : la session administrateur repose ainsi sur un cookie HttpOnly
 * émis sur la même origine que le frontend, sans aucun jeton stocké en
 * JavaScript (SECURITY.md §4, §13).
 *
 * En production, `dist/` est servi en statique et le reverse proxy expose
 * `/api` et `/health` sur la même origine que l'interface.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        secure: false,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        secure: false,
      },
    },
  },
  preview: {
    port: 4173,
    strictPort: false,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2020',
  },
});
