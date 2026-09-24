"""État ONLINE / OFFLINE : distinction des trois notions et persistance.

Références : ARCHITECTURE.md §8, INSTALLATION.md §7, API.md §9, mission §10.
"""

from __future__ import annotations

from app.core.state import ETAT_EN_LIGNE, ETAT_HORS_LIGNE
from app.database.connection import session_scope
from app.models.event import Event, EventType
from app.models.orchestrator_state import OrchestratorState
from utils import INSTANCE_A, code_erreur, entetes_agent


def _basculer(client, session_admin, etat: str, motif: str | None = None):
    return client.post(
        "/api/v1/settings/orchestrator/state",
        headers=session_admin.entetes(),
        json={"desired_state": etat, "reason": motif},
    )


def _evenements(event_type: str) -> list[Event]:
    with session_scope() as db:
        return db.query(Event).filter(Event.event_type == event_type).all()


def test_etat_initial_a_la_creation_de_la_base(client, session_admin):
    """À la première initialisation, l'orchestrateur démarre EN LIGNE."""
    reponse = client.get("/api/v1/settings/orchestrator", headers=session_admin.entetes())
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["desired_state"] == ETAT_EN_LIGNE
    assert corps["service_status"] == "RUNNING"
    assert corps["health_status"] in ("HEALTHY", "DEGRADED")
    assert corps["database_ok"] is True
    assert corps["version"]


def test_bascule_hors_ligne_ne_stoppe_pas_le_service(client, session_admin):
    """La désactivation logique laisse le processus actif (mission §10)."""
    reponse = _basculer(client, session_admin, ETAT_HORS_LIGNE, "maintenance planifiée")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["desired_state"] == ETAT_HORS_LIGNE
    assert corps["previous_state"] == ETAT_EN_LIGNE
    assert corps["reason"] == "maintenance planifiée"
    # Le service répond toujours : c'est le point central de la distinction.
    assert corps["service_status"] == "RUNNING"
    assert client.get("/health").status_code == 200

    with session_scope() as db:
        etat = db.query(OrchestratorState).one()
        assert etat.desired_state == ETAT_HORS_LIGNE
    assert len(_evenements(EventType.STATE_CHANGED)) == 1


def test_en_etat_hors_ligne_les_enregistrements_sont_refuses(client, session_admin, cle_enregistrement):
    """Hors ligne : aucun nouvel enregistrement d'agent (mission §10)."""
    _basculer(client, session_admin, ETAT_HORS_LIGNE)

    reponse = client.post(
        "/api/v1/agents/enroll",
        headers={"Authorization": f"Bearer {cle_enregistrement}"},
        json={
            "runtime": "hermes",
            "client_instance_id": INSTANCE_A,
            "requested_name": "Agent trop tard",
            "capabilities": [],
        },
    )
    assert reponse.status_code == 503
    assert code_erreur(reponse) == "SERVICE_UNAVAILABLE"


def test_en_etat_hors_ligne_les_taches_ne_sont_pas_distribuees(client, session_admin, agent):
    """Hors ligne : les opérations métier des agents sont refusées.

    ``GET /agents/me`` reste accessible : la consultation de sa propre identité
    n'est pas une opération métier et permet au connecteur de diagnostiquer sa
    situation.
    """
    entetes = entetes_agent(agent["access_token"])
    client.post("/api/v1/agents/heartbeat", headers=entetes, json={"status": "ONLINE"})

    _basculer(client, session_admin, ETAT_HORS_LIGNE)

    refusee = client.get("/api/v1/agents/tasks", headers=entetes)
    assert refusee.status_code == 503
    assert code_erreur(refusee) == "SERVICE_UNAVAILABLE"

    identite = client.get("/api/v1/agents/me", headers=entetes)
    assert identite.status_code == 200
    assert identite.json()["agent_id"] == agent["agent_id"]

    # Les opérations sur une tâche sont refusées de la même manière.
    from utils import creer_tache

    tache = creer_tache(
        client, session_admin.csrf, titre="Tache pendant maintenance", agent_id=agent["agent_id"]
    )
    _basculer(client, session_admin, ETAT_HORS_LIGNE)
    for chemin, corps in (
        (f"/api/v1/agents/tasks/{tache['id']}/ack", None),
        (f"/api/v1/agents/tasks/{tache['id']}/status", {"status": "RUNNING"}),
        (f"/api/v1/agents/tasks/{tache['id']}/result", {"status": "COMPLETED", "result": {}}),
    ):
        reponse = (
            client.post(chemin, headers=entetes, json=corps)
            if corps
            else client.post(chemin, headers=entetes)
        )
        assert reponse.status_code == 503, chemin


def test_en_etat_hors_ligne_le_heartbeat_reste_accepte_et_signale_l_etat(client, session_admin, agent):
    """Le signal de présence est accepté et renvoie explicitement l'état OFFLINE.

    Décision documentée : le connecteur doit pouvoir apprendre que l'orchestrateur
    est hors ligne sans être coupé du serveur.
    """
    entetes = entetes_agent(agent["access_token"])
    client.post("/api/v1/agents/heartbeat", headers=entetes, json={"status": "ONLINE"})
    _basculer(client, session_admin, ETAT_HORS_LIGNE)

    reponse = client.post("/api/v1/agents/heartbeat", headers=entetes, json={"status": "ONLINE"})
    assert reponse.status_code == 200
    assert reponse.json()["orchestrator_status"] == ETAT_HORS_LIGNE
    assert reponse.json()["agent_status"] == "ONLINE"


def test_en_etat_hors_ligne_le_tableau_de_bord_reste_accessible(client, session_admin, agent):
    """Hors ligne : l'administrateur conserve l'accès complet au tableau de bord."""
    _basculer(client, session_admin, ETAT_HORS_LIGNE)

    for chemin in (
        "/api/v1/agents",
        "/api/v1/tasks",
        "/api/v1/logs",
        "/api/v1/settings/orchestrator",
        "/api/v1/settings/service",
        "/api/v1/settings/security",
        "/api/v1/settings/enrollment-key",
        "/api/v1/settings/parameters",
    ):
        reponse = client.get(chemin, headers=session_admin.entetes())
        assert reponse.status_code == 200, chemin


def test_retour_en_ligne_restaure_les_operations(client, session_admin, agent, cle_enregistrement):
    """Le retour en ligne rétablit les opérations immédiatement."""
    _basculer(client, session_admin, ETAT_HORS_LIGNE)
    entetes = entetes_agent(agent["access_token"])
    assert client.get("/api/v1/agents/tasks", headers=entetes).status_code == 503

    reponse = _basculer(client, session_admin, ETAT_EN_LIGNE, "fin de maintenance")
    assert reponse.status_code == 200
    assert reponse.json()["desired_state"] == ETAT_EN_LIGNE
    assert client.get("/api/v1/agents/tasks", headers=entetes).status_code == 200

    # Un nouvel enregistrement redevient possible.
    from utils import enregistrer_agent

    corps = enregistrer_agent(
        client, cle_enregistrement, instance_id="instance-apres-maintenance", nom="Agent post-maintenance"
    )
    assert corps["status"] == "PENDING"


def test_etat_invalide_refuse(client, session_admin):
    """Un état logique inconnu est refusé, sans modification."""
    reponse = _basculer(client, session_admin, "PAUSED")
    assert reponse.status_code == 422
    assert code_erreur(reponse) == "VALIDATION_ERROR"

    etat = client.get("/api/v1/settings/orchestrator", headers=session_admin.entetes()).json()
    assert etat["desired_state"] == ETAT_EN_LIGNE


def test_changement_d_etat_exige_csrf(client, session_admin):
    """Le changement d'état exige le jeton anti-CSRF."""
    reponse = client.post(
        "/api/v1/settings/orchestrator/state",
        json={"desired_state": ETAT_HORS_LIGNE},
    )
    assert reponse.status_code == 403
    assert code_erreur(reponse) == "CSRF_TOKEN_INVALID"


def test_informations_de_service(client, session_admin, agent):
    """Les informations de service reflètent l'état réel, sans secret."""
    corps = client.get("/api/v1/settings/service", headers=session_admin.entetes()).json()
    assert corps["version"]
    assert corps["database_ok"] is True
    assert corps["migrations_a_jour"] is True
    assert corps["version_schema"] >= 1
    assert corps["agents_total"] == 1
    assert corps["agents_hors_ligne"] + corps["agents_en_ligne"] <= 1
    assert "secret" not in str(corps).lower()


def test_parametres_en_lecture_seule(client, session_admin):
    """Les paramètres effectifs sont exposés sans secret."""
    reponse = client.get("/api/v1/settings/parameters", headers=session_admin.entetes())
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["environment"] == "test"
    assert corps["agent_offline_threshold_seconds"] > 0
    assert corps["max_result_bytes"] > 0
    assert "secret_key" not in reponse.text
    assert "enrollment_key" not in reponse.text
