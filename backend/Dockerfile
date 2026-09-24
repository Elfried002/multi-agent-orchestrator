# Multi-Agent Orchestrator — image du backend (FastAPI + Uvicorn).
#
# Utilisée par docker-compose.yml pour la recette locale et par le service
# d'initialisation. Le déploiement de production n'utilise PAS cette image :
# il suit docs/INSTALLATION.md (Ubuntu Server, venv, systemd, Nginx, HTTPS).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dépendances d'abord : la couche est réutilisée tant que requirements.txt ne change pas.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Uniquement ce qui est nécessaire à l'exécution (pas de tests, pas de venv local).
COPY app ./app
COPY scripts ./scripts

# Données persistantes : base SQLite et journaux, indépendants du code
# (ARCHITECTURE.md §12). Montés sur un volume par docker-compose.yml.
RUN mkdir -p /data/logs

ENV DATABASE_PATH=/data/orchestrator.db \
    LOG_DIR=/data/logs

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
