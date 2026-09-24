#!/usr/bin/env python3
"""Diagnostic de couture : clés réellement exposées vs clés lues par le frontend."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parent
sys.path.insert(0, str(RACINE))
os.environ.setdefault("ORCHESTRATOR_ENV", "test")
os.environ.setdefault("SECRET_KEY", "cle-de-diagnostic-0123456789abcdefghijkl")
os.environ.setdefault("MONITOR_ENABLED", "false")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")
os.environ.setdefault(
    "DATABASE_PATH", str(Path(tempfile.gettempdir()) / "mo-couture" / "diag.db")
)

from fastapi.testclient import TestClient  # noqa: E402

from app.database.connection import session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.services.admin_service import creer_admin  # noqa: E402

MDP = "Diagnostic-Couture-2026!"
FRONTEND = RACINE.parent / "frontend"

ENDPOINTS = [
    "/api/v1/settings/orchestrator",
    "/api/v1/settings/service",
    "/api/v1/settings/parameters",
    "/api/v1/settings/security",
    "/api/v1/logs/reference",
    "/api/v1/tasks/state-machine",
    "/api/v1/agents",
    "/api/v1/tasks",
    "/api/v1/logs",
    "/api/v1/auth/me",
]


def cles_lues_par_le_frontend() -> set[str]:
    """Clés d'objet lues dans le code du frontend (accès .champ ou ['champ'])."""
    cles: set[str] = set()
    for fichier in list((FRONTEND / "src").rglob("*.ts")) + list((FRONTEND / "src").rglob("*.tsx")):
        contenu = fichier.read_text(encoding="utf-8")
        cles |= set(re.findall(r"\.([a-z][a-z0-9_]{2,})\b", contenu))
        cles |= set(re.findall(r'\[["\']([a-z][a-z0-9_]{2,})["\']\]', contenu))
    return cles


def main() -> int:
    with TestClient(app) as client:
        with session_scope() as db:
            creer_admin(db, username="admin", password=MDP)
        connexion = client.post("/api/v1/auth/login", json={"username": "admin", "password": MDP})
        entetes = {"X-CSRF-Token": connexion.json()["csrf_token"]}

        frontend_cles = cles_lues_par_le_frontend()
        print(f"Clés distinctes lues dans le frontend : {len(frontend_cles)}\n")

        for chemin in ENDPOINTS:
            reponse = client.get(chemin, headers=entetes)
            if reponse.status_code != 200:
                print(f"{chemin} → HTTP {reponse.status_code}")
                continue
            corps = reponse.json()
            if isinstance(corps, dict):
                premier = corps
                for valeur in corps.values():
                    if isinstance(valeur, list) and valeur and isinstance(valeur[0], dict):
                        premier = {**corps, **valeur[0]}
                        break
                cles = sorted(premier)
            else:
                cles = sorted(corps[0]) if corps else []
            # Clés exposées que le frontend ne lit pas du tout (candidats à l'écart).
            non_lues = [cle for cle in cles if cle not in frontend_cles]
            print(f"{chemin}")
            print(f"    exposées ({len(cles)}) : {', '.join(cles)}")
            if non_lues:
                print(f"    ⚠ jamais lues par le frontend : {', '.join(non_lues)}")
            print()

        # Clés que le frontend lit et qui n'existent nulle part côté backend.
        toutes_exposees: set[str] = set()
        for chemin in ENDPOINTS:
            reponse = client.get(chemin, headers=entetes)
            if reponse.status_code == 200 and isinstance(reponse.json(), dict):
                toutes_exposees |= set(reponse.json())
        attendues_metier = {
            "desired_state", "service_status", "health_status", "uptime_seconds",
            "database_ok", "migrations_a_jour", "version_schema", "version",
            "previous_state", "updated_at", "updated_by", "reason",
            "enrollment_key", "created_at", "expires_at",
            "items", "total", "page", "page_size",
            "severites", "types_evenement", "types_acteur", "severites_sensibles",
            "etats", "etats_terminaux", "transitions",
            "alertes", "resume", "limites_debit", "securite", "service",
            "id", "username", "is_active", "last_login_at",
            "name", "role", "runtime", "status", "capabilities", "source_ip",
            "last_seen_at", "assigned_agent_id", "priority", "title", "result",
        }
        manquantes = sorted(cle for cle in attendues_metier if cle not in toutes_exposees)
        print(f"Clés métier attendues et absentes de toutes les réponses : {manquantes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
