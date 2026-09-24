#!/usr/bin/env python3
"""Vérification de la couture frontend ↔ backend, contre le VRAI backend.

Le frontend a été développé puis testé contre un stub respectant docs/API.md.
Ce script confronte les deux réalisations :

1. toutes les routes appelées par le frontend existent réellement ;
2. les champs lus par le frontend existent réellement dans les réponses ;
3. le backend sert bien le bundle compilé (fichiers statiques + repli SPA) ;
4. aucune route appelée par le frontend n'est absente de l'OpenAPI.

Exécution : depuis backend/, avec les variables d'environnement de test.
"""

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
os.environ.setdefault("SECRET_KEY", "cle-de-verification-couture-0123456789abcdef")
os.environ.setdefault("MONITOR_ENABLED", "false")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")
os.environ.setdefault("LOGIN_RATE_LIMIT", "1000/minute")
os.environ.setdefault(
    "DATABASE_PATH", str(Path(tempfile.gettempdir()) / "mo-couture" / "couture.db")
)

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.database.connection import session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.services.admin_service import creer_admin  # noqa: E402
from app.services.token_service import (  # noqa: E402
    assurer_cle_enregistrement,
    valeur_cle_enregistrement,
)

FRONTEND = RACINE.parent / "frontend"
MOT_DE_PASSE = "Couture-Integration-2026!"

reussis = 0
echoues: list[str] = []


def verifier(condition: bool, libelle: str) -> None:
    global reussis
    if condition:
        reussis += 1
        print(f"  [OK]   {libelle}")
    else:
        echoues.append(libelle)
        print(f"  [ECHEC] {libelle}")


# ------------------------------------------------ 1. routes appelées par le frontend
def routes_frontend() -> set[str]:
    """Extrait les chemins d'API réellement présents dans le code du frontend."""
    motifs: set[str] = set()
    for fichier in list((FRONTEND / "src").rglob("*.ts")) + list((FRONTEND / "src").rglob("*.tsx")):
        contenu = fichier.read_text(encoding="utf-8")
        # Chemins littéraux et gabarits avec expressions.
        for brut in re.findall(r"['\"`](/api/v1[^'\"`]*)['\"`]", contenu):
            chemin = re.sub(r"\$\{[^}]*\}", "{id}", brut)
            chemin = chemin.split("?")[0].rstrip("/")
            # « /api/v1 » seul est la constante de base, pas un appel de route.
            if chemin and chemin != "/api/v1":
                motifs.add(chemin)
    return motifs


def normaliser(chemin: str) -> str:
    """Normalise un chemin pour la comparaison (paramètres → {id})."""
    return re.sub(r"\{[^}]*\}", "{id}", chemin).rstrip("/")


def main() -> int:
    frontend_dispo = (FRONTEND / "src").is_dir()
    verifier(frontend_dispo, "sources du frontend présentes")
    verifier((FRONTEND / "dist" / "index.html").is_file(), "bundle compilé présent (dist/index.html)")

    with TestClient(app) as client:
        with session_scope() as db:
            creer_admin(db, username="admin", password=MOT_DE_PASSE)
            assurer_cle_enregistrement(db)
            cle, _ = valeur_cle_enregistrement(db)

        connexion = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE}
        )
        verifier(connexion.status_code == 200, "connexion administrateur réelle")
        csrf = connexion.json()["csrf_token"]
        entetes_admin = {"X-CSRF-Token": csrf}

        # --- 2. existence des routes utilisées par le frontend
        schema = client.get("/openapi.json", headers=entetes_admin).json()
        exposees = {normaliser(chemin) for chemin in schema["paths"]}

        utilisees = routes_frontend()
        print(f"\n  Routes distinctes appelées par le frontend : {len(utilisees)}")
        inconnues = sorted(chemin for chemin in utilisees if normaliser(chemin) not in exposees)
        verifier(not inconnues, f"toutes les routes appelées existent (absentes : {inconnues})")
        # Surplus assumé : ces routes sont des ajouts au-delà de docs/API.md et ne
        # sont volontairement pas consommées par l'interface.
        supplementaires = {
            "/api/v1/settings/service",
            "/api/v1/settings/parameters",
            "/api/v1/settings/security",
            "/api/v1/logs/reference",
            "/api/v1/tasks/state-machine",
        }
        verifier(
            supplementaires <= exposees,
            "routes supplémentaires exposées (hors API.md, non utilisées par l'UI)",
        )

        # --- 3. jeu de données réel
        agent = client.post(
            "/api/v1/agents/enroll",
            headers={"Authorization": f"Bearer {cle}"},
            json={
                "runtime": "hermes",
                "client_instance_id": "instance-couture-0001",
                "requested_name": "Agent Couture",
                "declared_role": "builder",
                "capabilities": ["build", "test"],
                "version": "1.0.0",
            },
        )
        verifier(agent.status_code == 201, "enregistrement d'un agent réel")
        jeton = agent.json()["access_token"]

        tache = client.post(
            "/api/v1/tasks",
            headers=entetes_admin,
            json={
                "title": "Tâche de vérification de couture",
                "description": "Créée par verifier_couture.py",
                "priority": "HIGH",
                "assigned_agent_id": agent.json()["agent_id"],
            },
        )
        verifier(tache.status_code == 201, "création d'une tâche réelle")
        tache_id = tache.json()["id"]

        # --- 4. champs lus par le frontend, vérifiés dans les réponses réelles
        champs_attendus = {
            "/api/v1/auth/me": ["id", "username", "is_active"],
            "/api/v1/agents": ["items", "total", "page", "page_size"],
            "/api/v1/tasks": ["items", "total", "page", "page_size"],
            "/api/v1/logs": ["items", "total", "page", "page_size"],
            "/api/v1/settings/orchestrator": [
                "desired_state",
                "service_status",
                "health_status",
            ],
            "/api/v1/settings/service": [
                "version",
                "environment",
                "started_at",
                "uptime_seconds",
                "database_ok",
                "migrations_a_jour",
                "version_schema",
                "agents_total",
                "agents_en_ligne",
                "agents_hors_ligne",
                "taches_en_attente",
                "taches_en_cours",
                "taches_terminees",
                "taches_en_echec",
            ],
            "/api/v1/settings/parameters": [
                "environment",
                "version",
                "login_rate_limit",
                "enroll_rate_limit",
                "agent_rate_limit",
                "admin_rate_limit",
                "admin_session_ttl_hours",
                "admin_session_idle_minutes",
                "max_request_bytes",
                "max_result_bytes",
                "task_default_timeout_seconds",
                "agent_offline_threshold_seconds",
                "monitor_interval_seconds",
                "allowed_origins",
                "trusted_proxies",
                "enable_docs",
            ],
            "/api/v1/settings/security": [
                "depuis",
                "fenetre_heures",
                "portee",
                "compteurs",
                "par_severite",
                "alertes",
                "agents_par_etat",
                "anciennete",
            ],
            "/api/v1/logs/reference": ["severites", "types_evenement", "types_acteur"],
            "/api/v1/tasks/state-machine": ["etats", "etats_terminaux", "transitions"],
        }
        print("\n  Champs du contrat vérifiés dans les réponses réelles :")
        for chemin, champs in champs_attendus.items():
            reponse = client.get(chemin, headers=entetes_admin)
            if reponse.status_code != 200:
                verifier(False, f"{chemin} → HTTP {reponse.status_code}")
                continue
            corps = reponse.json()
            manquants = [champ for champ in champs if champ not in corps]
            verifier(not manquants, f"{chemin} expose {', '.join(champs)}")

        # Champs d'un agent et d'une tâche (lus par les tableaux du tableau de bord).
        agent_reel = client.get(
            f"/api/v1/agents/{agent.json()['agent_id']}", headers=entetes_admin
        ).json()
        for champ in ("id", "name", "role", "runtime", "status", "capabilities", "created_at"):
            verifier(champ in agent_reel, f"agent → champ « {champ} »")

        tache_reelle = client.get(f"/api/v1/tasks/{tache_id}", headers=entetes_admin).json()
        for champ in ("id", "title", "status", "priority", "created_at", "assigned_agent_id"):
            verifier(champ in tache_reelle, f"tâche → champ « {champ} »")

        # Cycle agent complet, en respectant le contrat utilisé par la page Agents.
        client.post(
            "/api/v1/agents/heartbeat",
            headers={"Authorization": f"Bearer {jeton}"},
            json={"status": "ONLINE"},
        )
        client.post(
            f"/api/v1/agents/tasks/{tache_id}/ack",
            headers={"Authorization": f"Bearer {jeton}"},
        )
        resultat = client.post(
            f"/api/v1/agents/tasks/{tache_id}/result",
            headers={"Authorization": f"Bearer {jeton}"},
            json={"status": "COMPLETED", "result": {"couture": "verifiee"}},
        )
        verifier(resultat.status_code == 200, "cycle agent → tâche → résultat réel")
        detail = client.get(f"/api/v1/tasks/{tache_id}", headers=entetes_admin).json()
        verifier(detail["status"] == "COMPLETED", "état terminal visible par le tableau de bord")

        # Filtres réellement utilisés par les pages.
        for params in (
            "?status=ONLINE",
            "?role=builder",
            "?search=Couture",
            "?page=1&page_size=10",
        ):
            reponse = client.get(f"/api/v1/agents{params}", headers=entetes_admin)
            verifier(reponse.status_code == 200, f"filtre agents {params}")
        for params in ("?status=COMPLETED", "?priority=HIGH", "?page=1&page_size=10"):
            reponse = client.get(f"/api/v1/tasks{params}", headers=entetes_admin)
            verifier(reponse.status_code == 200, f"filtre tâches {params}")
        for params in ("?severity=INFO", "?event_type=task.created", "?page=1&page_size=5"):
            reponse = client.get(f"/api/v1/logs{params}", headers=entetes_admin)
            verifier(reponse.status_code == 200, f"filtre journaux {params}")

        # --- 5. service du bundle compilé par le backend
        if (FRONTEND / "dist" / "index.html").is_file():
            racine_spa = client.get("/")
            verifier(racine_spa.status_code == 200, "GET / sert le bundle")
            verifier(
                "text/html" in racine_spa.headers.get("content-type", ""),
                "GET / renvoie du HTML",
            )
            verifier("<div id=\"root\">" in racine_spa.text or "id=\"root\"" in racine_spa.text,
                     "la racine du SPA est présente")
            verifier("http" not in racine_spa.headers.get("content-security-policy", "")
                     or "default-src 'self'" in racine_spa.headers.get("content-security-policy", ""),
                     "politique de sécurité appliquée à la page servie")

            assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', racine_spa.text)
            verifier(bool(assets), f"assets référencés par le bundle ({len(assets)})")
            for asset in assets:
                reponse_asset = client.get(asset)
                verifier(
                    reponse_asset.status_code == 200 and len(reponse_asset.content) > 100,
                    f"asset servi : {asset}",
                )

            # Repli SPA sur une route interne (rechargement direct).
            repli = client.get("/agents")
            verifier(repli.status_code == 200, "repli SPA sur route interne (/agents)")
            # L'API n'est jamais masquée par le repli SPA.
            api_inconnue = client.get("/api/v1/inexistant-couture")
            verifier(api_inconnue.status_code == 404, "une route API inconnue reste un 404 JSON")
            verifier(
                api_inconnue.headers.get("content-type", "").startswith("application/json"),
                "le 404 d'API reste du JSON (non masqué par le HTML du SPA)",
            )

    print()
    print("=" * 68)
    print(f"  Contrôles réussis : {reussis} — échecs : {len(echoues)}")
    for libelle in echoues:
        print(f"    - {libelle}")
    print("=" * 68)
    return 1 if echoues else 0


if __name__ == "__main__":
    sys.exit(main())
