"""Persistance après redémarrage et parcours d'intégration complets.

Références : mission §10 et §16, ARCHITECTURE.md §8, AGENT_CONNECTION.md.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.state import ETAT_HORS_LIGNE
from app.database.connection import session_scope
from app.main import app
from app.models.agent import Agent
from app.models.event import Event, EventType
from app.models.task import Task, TaskStatus
from utils import (
    INSTANCE_A,
    MOT_DE_PASSE_ADMIN,
    code_erreur,
    creer_tache,
    enregistrer_agent,
    entetes_agent,
)


def _basculer_avec(client, entetes: dict, etat: str):
    """Bascule l'état logique en utilisant directement les en-têtes d'administration."""
    return client.post(
        "/api/v1/settings/orchestrator/state",
        headers=entetes,
        json={"desired_state": etat},
    )


def _basculer(client, session_admin, etat):
    return client.post(
        "/api/v1/settings/orchestrator/state",
        headers=session_admin.entetes(),
        json={"desired_state": etat},
    )


# ===================================================== persistance après arrêt
def test_persistance_complete_apres_redemarrage(environnement_isole):
    """Toutes les données survivent à un redémarrage réel du service.

    Le test ouvre deux cycles de vie successifs sur le **même fichier de base** :
    c'est exactement ce que fait un redémarrage systemd.
    """
    # ---------------- premier cycle de vie ----------------
    with TestClient(app) as client:
        reponse = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN}
        )
        assert reponse.status_code == 401  # aucun compte n'existe encore
        from app.services.admin_service import creer_admin

        with session_scope() as db:
            creer_admin(db, username="admin", password=MOT_DE_PASSE_ADMIN)

        connexion = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN}
        )
        assert connexion.status_code == 200
        csrf = connexion.json()["csrf_token"]
        entetes_admin = {"X-CSRF-Token": csrf}

        from app.services.token_service import assurer_cle_enregistrement, valeur_cle_enregistrement

        with session_scope() as db:
            assurer_cle_enregistrement(db)
            cle, _ = valeur_cle_enregistrement(db)

        agent = enregistrer_agent(client, cle, instance_id=INSTANCE_A, nom="Agent persistant")
        entetes = entetes_agent(agent["access_token"])
        client.post("/api/v1/agents/heartbeat", headers=entetes, json={"status": "ONLINE"})

        tache = creer_tache(
            client, csrf, titre="Tache persistante", agent_id=agent["agent_id"]
        )
        client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)
        client.post(
            f"/api/v1/agents/tasks/{tache['id']}/result",
            headers=entetes,
            json={"status": "COMPLETED", "result": {"persiste": True}},
        )

        # Un second agent révoqué : la révocation doit survivre au redémarrage.
        revoque = enregistrer_agent(
            client, cle, instance_id="instance-revoquee-0001", nom="Agent revoque persistant"
        )
        client.post(
            f"/api/v1/agents/{revoque['agent_id']}/revoke",
            headers=entetes_admin,
            json={"reason": "révocation avant redémarrage"},
        )

        # État logique OFFLINE : il doit être restauré, pas réinitialisé.
        assert _basculer_avec(client, entetes_admin, ETAT_HORS_LIGNE).status_code == 200

        evenements_avant = None
        with session_scope() as db:
            evenements_avant = db.query(Event).count()
        assert evenements_avant > 0

    # ---------------- second cycle de vie (redémarrage) ----------------
    with TestClient(app) as client:
        connexion = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN}
        )
        assert connexion.status_code == 200
        entetes_admin = {"X-CSRF-Token": connexion.json()["csrf_token"]}

        # État logique restauré.
        etat = client.get("/api/v1/settings/orchestrator", headers=entetes_admin).json()
        assert etat["desired_state"] == "OFFLINE", "l'état logique doit être restauré"
        assert etat["service_status"] == "RUNNING"

        # Agents conservés.
        agents = client.get("/api/v1/agents", headers=entetes_admin).json()
        assert agents["total"] == 2
        par_etat = {a["id"]: a["status"] for a in agents["items"]}
        assert par_etat[agent["agent_id"]] in ("ONLINE", "OFFLINE")
        assert par_etat[revoque["agent_id"]] == "REVOKED"

        # Tâche et résultat conservés.
        detail = client.get(f"/api/v1/tasks/{tache['id']}", headers=entetes_admin).json()
        assert detail["status"] == TaskStatus.COMPLETED
        assert detail["result"]["persiste"] is True

        # Révocation persistante : le jeton reste refusé après redémarrage.
        refuse = client.get(
            "/api/v1/agents/me", headers=entetes_agent(revoque["access_token"])
        )
        assert refuse.status_code == 401

        # Journal conservé et enrichi (service démarré deux fois).
        demarrages = client.get(
            f"/api/v1/logs?event_type={EventType.SERVICE_STARTED}", headers=entetes_admin
        ).json()
        assert demarrages["total"] == 2

        with session_scope() as db:
            assert db.query(Event).count() > evenements_avant


def test_le_retour_en_ligne_est_persistant(environnement_isole):
    """Un retour ONLINE est lui aussi persistant."""
    with TestClient(app) as client:
        from app.services.admin_service import creer_admin

        with session_scope() as db:
            creer_admin(db, username="admin", password=MOT_DE_PASSE_ADMIN)
        connexion = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN}
        )
        entetes = {"X-CSRF-Token": connexion.json()["csrf_token"]}
        _basculer_avec(client, entetes, ETAT_HORS_LIGNE)

    with TestClient(app) as client:
        connexion = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN}
        )
        entetes = {"X-CSRF-Token": connexion.json()["csrf_token"]}
        _basculer_avec(client, entetes, "ONLINE")

    with TestClient(app) as client:
        connexion = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN}
        )
        entetes = {"X-CSRF-Token": connexion.json()["csrf_token"]}
        etat = client.get("/api/v1/settings/orchestrator", headers=entetes).json()
        assert etat["desired_state"] == "ONLINE"


# ===================================================== parcours d'intégration
def test_parcours_complet_agent_de_bout_en_bout(client, session_admin, cle_enregistrement):
    """Enregistrement → présence → tâche → résultat → déconnexion → reconnexion."""
    # 1. Enregistrement
    agent = enregistrer_agent(client, cle_enregistrement, instance_id=INSTANCE_A, nom="Agent complet")
    entetes = entetes_agent(agent["access_token"])

    # 2. Authentification par jeton individuel
    identite = client.get("/api/v1/agents/me", headers=entetes)
    assert identite.status_code == 200
    assert identite.json()["agent_id"] == agent["agent_id"]

    # 3. Présence
    assert client.post("/api/v1/agents/heartbeat", headers=entetes, json={"status": "ONLINE"}).status_code == 200

    # 4. Tâche attribuée puis récupérée
    tache = creer_tache(client, session_admin.csrf, titre="Parcours complet", agent_id=agent["agent_id"])
    assert [t["id"] for t in client.get("/api/v1/agents/tasks", headers=entetes).json()] == [tache["id"]]

    # 5. Prise en charge et résultat
    assert client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes).status_code == 200
    resultat = client.post(
        f"/api/v1/agents/tasks/{tache['id']}/result",
        headers=entetes,
        json={"status": "COMPLETED", "result": {"livrable": "rapport.md"}},
    )
    assert resultat.status_code == 200

    # 6. Déconnexion logique : le jeton reste utilisable
    assert (
        client.post(
            f"/api/v1/agents/{agent['agent_id']}/disconnect",
            headers=session_admin.entetes(),
            json={"reason": "fin de journée"},
        ).status_code
        == 200
    )
    assert client.post("/api/v1/agents/heartbeat", headers=entetes, json={"status": "ONLINE"}).status_code == 200

    # 7. Révocation : plus aucun accès
    assert (
        client.post(
            f"/api/v1/agents/{agent['agent_id']}/revoke",
            headers=session_admin.entetes(),
            json={"reason": "fin de mission"},
        ).status_code
        == 200
    )
    assert client.get("/api/v1/agents/me", headers=entetes).status_code == 401

    # 8. Nouvel enregistrement possible avec la même identité locale
    nouveau = enregistrer_agent(
        client, cle_enregistrement, instance_id=INSTANCE_A, nom="Agent remplacant"
    )
    assert nouveau["agent_id"] != agent["agent_id"]
    assert nouveau["status"] == "PENDING"


def test_protection_contre_les_doublons_de_connexion(client, cle_enregistrement):
    """Un connecteur qui redémarre ne crée pas un second agent (mission §8)."""
    premier = enregistrer_agent(client, cle_enregistrement, instance_id=INSTANCE_A, nom="Agent unique")

    for _ in range(3):
        reponse = client.post(
            "/api/v1/agents/enroll",
            headers={"Authorization": f"Bearer {cle_enregistrement}"},
            json={
                "runtime": "hermes",
                "client_instance_id": INSTANCE_A,
                "requested_name": "Agent unique",
                "capabilities": [],
            },
        )
        assert reponse.status_code == 409
        assert code_erreur(reponse) == "AGENT_ALREADY_ENROLLED"

    with session_scope() as db:
        assert db.query(Agent).count() == 1
    assert premier["agent_id"]


def test_comportement_hors_ligne_de_bout_en_bout(client, session_admin, agent, cle_enregistrement):
    """Le cycle OFFLINE bloque les agents sans couper l'administrateur."""
    entetes = entetes_agent(agent["access_token"])
    client.post("/api/v1/agents/heartbeat", headers=entetes, json={"status": "ONLINE"})

    # Bascule hors ligne par l'administrateur.
    _basculer(client, session_admin, ETAT_HORS_LIGNE)

    # Les agents sont bloqués...
    assert client.get("/api/v1/agents/tasks", headers=entetes).status_code == 503
    assert (
        client.post(
            "/api/v1/agents/enroll",
            headers={"Authorization": f"Bearer {cle_enregistrement}"},
            json={
                "runtime": "hermes",
                "client_instance_id": "instance-hors-ligne-0001",
                "requested_name": "Agent hors ligne",
                "capabilities": [],
            },
        ).status_code
        == 503
    )

    # ...mais l'administrateur conserve tout, et peut remettre en ligne.
    assert client.get("/api/v1/agents", headers=session_admin.entetes()).status_code == 200
    assert client.get("/api/v1/tasks", headers=session_admin.entetes()).status_code == 200
    assert _basculer(client, session_admin, "ONLINE").status_code == 200
    assert client.get("/api/v1/agents/tasks", headers=entetes).status_code == 200


def test_suppression_d_un_agent_conserve_l_historique(client, session_admin, agent):
    """La suppression définitive ne doit pas effacer les tâches ni le journal."""
    tache = creer_tache(
        client, session_admin.csrf, titre="Tache orpheline", agent_id=agent["agent_id"]
    )
    client.post(
        f"/api/v1/agents/{agent['agent_id']}/revoke",
        headers=session_admin.entetes(),
        json={"reason": "suppression"},
    )
    assert client.delete(
        f"/api/v1/agents/{agent['agent_id']}", headers=session_admin.entetes()
    ).status_code == 204

    # La tâche subsiste, sans agent assigné.
    detail = client.get(f"/api/v1/tasks/{tache['id']}", headers=session_admin.entetes())
    assert detail.status_code == 200
    assert detail.json()["assigned_agent_id"] is None

    # L'historique est conservé.
    journal = client.get(
        f"/api/v1/logs?event_type={EventType.AGENT_REVOKED}", headers=session_admin.entetes()
    ).json()
    assert journal["total"] == 1

    with session_scope() as db:
        assert db.get(Task, tache["id"]) is not None
