"""Utilitaires partagés par la suite de tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

#: Mot de passe de test conforme à la politique (≥ 12 caractères, classes variées).
MOT_DE_PASSE_ADMIN = "MotDePasse-Test-2026!"
MOT_DE_PASSE_ADMIN_BIS = "MotDePasse-Test-2027!"
MOT_DE_PASSE_FAIBLE = "court"

#: Identifiants d'instances locales utilisés par les tests.
INSTANCE_A = "instance-locale-test-0001"
INSTANCE_B = "instance-locale-test-0002"


def entetes_agent(jeton: str) -> dict[str, str]:
    """En-têtes d'authentification d'un agent."""
    return {"Authorization": f"Bearer {jeton}"}


def entetes_cle(cle: str) -> dict[str, str]:
    """En-têtes d'enregistrement (clé partagée du protocole)."""
    return {"Authorization": f"Bearer {cle}"}


def enregistrer_agent(
    client: TestClient,
    cle: str,
    *,
    instance_id: str = INSTANCE_A,
    nom: str = "Agent de test",
    role: str = "research",
    runtime: str = "hermes",
    capacites: list[str] | None = None,
    version: str = "1.0.0",
    attendu: int = 201,
) -> dict[str, Any]:
    """Enregistre un agent par l'API et retourne la réponse décodée."""
    reponse = client.post(
        "/api/v1/agents/enroll",
        headers=entetes_cle(cle),
        json={
            "runtime": runtime,
            "client_instance_id": instance_id,
            "requested_name": nom,
            "declared_role": role,
            "capabilities": capacites if capacites is not None else ["research"],
            "version": version,
        },
    )
    assert reponse.status_code == attendu, reponse.text
    return reponse.json()


def creer_tache(
    client: TestClient,
    csrf: str,
    *,
    titre: str = "Tâche de test",
    description: str = "Description de la tâche de test.",
    priorite: str = "NORMAL",
    agent_id: str | None = None,
    attendu: int = 201,
) -> dict[str, Any]:
    """Crée une tâche par l'API (session administrateur) et retourne la réponse."""
    reponse = client.post(
        "/api/v1/tasks",
        headers={"X-CSRF-Token": csrf},
        json={
            "title": titre,
            "description": description,
            "priority": priorite,
            "assigned_agent_id": agent_id,
        },
    )
    assert reponse.status_code == attendu, reponse.text
    return reponse.json()


def code_erreur(reponse) -> str:
    """Extrait le code d'erreur normalisé d'une réponse."""
    corps = reponse.json()
    assert "error" in corps, f"Réponse sans enveloppe d'erreur : {reponse.text}"
    return corps["error"]["code"]
