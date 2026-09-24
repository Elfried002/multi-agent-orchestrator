# Multi-Agent Orchestrator — image du frontend React.
#
# Étape 1 : compilation du frontend avec Node (npm ci puis npm run build).
# Étape 2 : service des fichiers compilés par Nginx, qui relaie l'API au backend.
#
# Pour la production, voir deploy/nginx/orchestrator.conf (Nginx de l'hôte).

FROM node:20-alpine AS compilation

WORKDIR /app

# Le verrou est copié avec le manifeste : l'installation est reproductible.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build


FROM nginx:1.27-alpine AS service

COPY --from=compilation /app/dist /usr/share/nginx/html
COPY nginx.container.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
