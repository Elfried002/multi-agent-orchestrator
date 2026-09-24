#!/usr/bin/env python3
"""Bout-en-bout minimal : prouve que la chaîne complète fonctionne réellement.

Parcours : santé → refus sans session → création d'administrateur → connexion →
session → clé d'enregistrement → enregistrement d'un agent → création de tâche →
récupération par l'agent → acquittement → résultat → consultation administrateur.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent
sys.path.insert(0, str(RACINE))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.database.connection import session_scope  # noqa: E402
from app.database.migrations import run_migrations  # noqa: E402
from app.services.admin_service import creer_admin  # noqa: E402
from app.services.token_service import assurer_cle_enregistrement, valeur_cle_enregistrement  # noqa: E402

MOT_DE_PASSE = "MotDePasse-Test-2026!"
echecs: list[str] = []


def verifier(condition: bool, etiquette: str, detail: str = "") -> None:
    marque = "OK  " if condition else "ECHEC"
    print(f"  [{marque}] {etiquette}{(' — ' + detail) if detail else ''}")
    if not condition:
        echecs.append(etiquette)


with TestClient(app) as client:
    # 1. Santé publique
    r = client.get("/health")
    verifier(r.status_code == 200 and r.json().get("status") == "ok", "GET /health", str(r.json()))
    verifier(
        set(r.json().keys()) == {"status", "service", "version"},
        "aucune information sensible dans /health",
        str(sorted(r.json().keys())),
    )

    # 2. Route protégée sans session
    r = client.get("/api/v1/auth/me")
    verifier(r.status_code == 401, "GET /auth/me sans session renvoie 401", str(r.status_code))
    verifier("error" in r.json() and r.json()["error"]["code"] == "UNAUTHENTICATED",
             "format d'erreur normalisé", r.text[:120])

    # 3. Création du compte administrateur (comme le ferait setup.sh)
    with session_scope() as db:
        admin, cree = creer_admin(db, username="admin", password=MOT_DE_PASSE)
        assurer_cle_enregistrement(db)
        cle, _ = valeur_cle_enregistrement(db)
    verifier(cree and admin.username == "admin", "création du compte administrateur")

    # 4. Connexion
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE})
    verifier(r.status_code == 200, "POST /auth/login", r.text[:200])
    corps = r.json()
    csrf = corps.get("csrf_token", "")
    verifier(bool(csrf), "jeton anti-CSRF délivré")
    verifier(
        MOT_DE_PASSE not in r.text
        and "argon2" not in r.text.lower()
        and "password_hash" not in r.text,
        "aucun mot de passe ni empreinte dans la réponse de connexion",
    )
    verifier(settings.cookie_name in r.cookies or settings.cookie_name in client.cookies,
             "cookie de session posé")

    # 5. Session courante
    r = client.get("/api/v1/auth/me")
    verifier(r.status_code == 200 and r.json()["username"] == "admin", "GET /auth/me avec session")

    # 6. Enregistrement d'un agent
    r = client.post(
        "/api/v1/agents/enroll",
        headers={"Authorization": f"Bearer {cle}"},
        json={
            "runtime": "hermes",
            "client_instance_id": "instance-smoke-test-0001",
            "requested_name": "Smoke Agent",
            "declared_role": "research",
            "capabilities": ["research", "summarization"],
            "version": "1.0.0",
        },
    )
    verifier(r.status_code == 201, "POST /agents/enroll", r.text[:200])
    agent = r.json()
    jeton = agent.get("access_token", "")
    verifier(bool(jeton) and agent["status"] == "PENDING", "identité et jeton attribués par le serveur",
             f"id={agent.get('agent_id')} role={agent.get('role')}")

    # 7. Refus avec clé invalide
    r = client.post(
        "/api/v1/agents/enroll",
        headers={"Authorization": "Bearer cle-invalide-xyz"},
        json={"runtime": "hermes", "client_instance_id": "instance-smoke-test-0002",
              "requested_name": "Intrus", "capabilities": []},
    )
    verifier(r.status_code == 401, "enregistrement refusé avec clé invalide", str(r.status_code))

    # 8. Heartbeat
    r = client.post("/api/v1/agents/heartbeat", headers={"Authorization": f"Bearer {jeton}"},
                    json={"status": "ONLINE", "runtime_status": "ready"})
    verifier(r.status_code == 200 and r.json()["agent_status"] == "ONLINE", "POST /agents/heartbeat")
    verifier(r.json()["orchestrator_status"] == "ONLINE", "état de l'orchestrateur dans la réponse")

    # 9. Création de tâche par l'administrateur (avec CSRF)
    r = client.post("/api/v1/tasks",
                    headers={"X-CSRF-Token": csrf},
                    json={"title": "Analyse de données", "description": "Analyser les données fournies.",
                          "priority": "HIGH", "assigned_agent_id": agent["agent_id"]})
    verifier(r.status_code == 201, "POST /tasks (administrateur)", r.text[:200])
    tache = r.json()
    verifier(tache["status"] == "ASSIGNED", "tâche attribuée immédiatement", tache["status"])

    # 10. Sans jeton anti-CSRF, la création est refusée
    r = client.post("/api/v1/tasks", json={"title": "Tâche sans CSRF"})
    verifier(r.status_code == 403 and r.json()["error"]["code"] == "CSRF_TOKEN_INVALID",
             "création refusée sans jeton anti-CSRF", str(r.status_code))

    # 11. L'agent récupère ses tâches
    r = client.get("/api/v1/agents/tasks", headers={"Authorization": f"Bearer {jeton}"})
    verifier(r.status_code == 200 and len(r.json()) == 1 and r.json()[0]["id"] == tache["id"],
             "GET /agents/tasks retourne la tâche attribuée")

    # 12. Prise en charge
    r = client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers={"Authorization": f"Bearer {jeton}"})
    verifier(r.status_code == 200 and r.json()["status"] == "RUNNING", "POST .../ack")
    r = client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers={"Authorization": f"Bearer {jeton}"})
    verifier(r.status_code == 200 and r.json()["deja_prise_en_charge"] is True,
             "acquittement répété idempotent")

    # 13. Résultat
    r = client.post(f"/api/v1/agents/tasks/{tache['id']}/result",
                    headers={"Authorization": f"Bearer {jeton}"},
                    json={"status": "COMPLETED", "result": {"summary": "Analyse terminée."}})
    verifier(r.status_code == 200 and r.json()["status"] == "COMPLETED", "POST .../result")

    # 14. Consultation administrateur du détail
    r = client.get(f"/api/v1/tasks/{tache['id']}")
    verifier(r.status_code == 200 and r.json()["result"]["summary"] == "Analyse terminée.",
             "résultat conservé et consultable")

    # 15. Liste des agents et journal
    r = client.get("/api/v1/agents")
    verifier(r.status_code == 200 and r.json()["total"] == 1, "GET /agents", r.text[:150])
    verifier("access_token" not in r.text and "token_hash" not in r.text,
             "aucun jeton exposé dans la liste des agents")
    r = client.get("/api/v1/logs?page_size=5")
    verifier(r.status_code == 200 and r.json()["total"] > 0, "GET /logs", f"total={r.json().get('total')}")
    r = client.get("/api/v1/logs?severity=INCONNUE")
    verifier(r.status_code == 422, "gravité invalide refusée", str(r.status_code))

    # 16. Déconnexion : la session est invalidée côté serveur
    r = client.post("/api/v1/auth/logout")
    verifier(r.status_code == 204, "POST /auth/logout", str(r.status_code))
    r = client.get("/api/v1/auth/me")
    verifier(r.status_code == 401, "session réellement invalidée après déconnexion", str(r.status_code))

print()
if echecs:
    print(f"  {len(echecs)} vérification(s) en échec : {echecs}")
    raise SystemExit(1)
print("  Toutes les vérifications de bout en bout ont réussi.")
