"""Enregistrement, identité, présence, révocation et isolation des agents.

Références : AGENT_CONNECTION.md, API.md §6 et §7, SECURITY.md §5 et §6.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.config import ROLES_AUTORISES, settings
from app.database.connection import session_scope
from app.models.agent import Agent, AgentStatus
from app.models.event import Event, EventType
from app.models.token import Token, TokenType
from app.monitoring.heartbeat import SurveillancePresence
from app.utils.datetime_utils import utcnow
from utils import (
    INSTANCE_A,
    INSTANCE_B,
    code_erreur,
    enregistrer_agent,
    entetes_agent,
)


def _agent(agent_id: str) -> Agent:
    with session_scope() as db:
        return db.get(Agent, agent_id)


def _evenements(event_type: str) -> list[Event]:
    with session_scope() as db:
        return (
            db.query(Event)
            .filter(Event.event_type == event_type)
            .order_by(Event.created_at)
            .all()
        )


# ------------------------------------------------------------ enregistrement
def test_enregistrement_attribue_identite_par_le_serveur(client, cle_enregistrement):
    """Le serveur attribue l'identifiant, le nom, le rôle et le jeton."""
    reponse = client.post(
        "/api/v1/agents/enroll",
        headers={"Authorization": f"Bearer {cle_enregistrement}"},
        json={
            "runtime": "hermes",
            "client_instance_id": INSTANCE_A,
            "requested_name": "Hermes Builder",
            "declared_role": "builder",
            "capabilities": ["code_generation", "code_review"],
            "version": "1.4.2",
            # Champs d'identité que le client ne doit pas pouvoir imposer :
            "agent_id": "agt_falsifie",
            "status": "ONLINE",
        },
    )
    assert reponse.status_code == 201, reponse.text
    corps = reponse.json()

    assert corps["agent_id"].startswith("agt_") and corps["agent_id"] != "agt_falsifie"
    assert corps["status"] == "PENDING", "un agent n'est jamais ONLINE avant son premier signal"
    assert corps["role"] == "builder"
    assert corps["token_type"] == "Bearer"
    assert len(corps["access_token"]) >= 32

    # Le jeton n'est conservé qu'en empreinte.
    with session_scope() as db:
        jetons = db.query(Token).filter(Token.token_type == TokenType.AGENT).all()
        assert len(jetons) == 1
        assert jetons[0].token_hash != corps["access_token"]
        assert len(jetons[0].token_hash) == 64
        assert jetons[0].encrypted_value is None, "un jeton d'agent n'est jamais conservé en clair"

    assert len(_evenements(EventType.AGENT_ENROLLED)) == 1


def test_enregistrement_refuse_avec_cle_invalide(client, cle_enregistrement):
    """Une clé d'enregistrement invalide est refusée et journalisée."""
    reponse = client.post(
        "/api/v1/agents/enroll",
        headers={"Authorization": "Bearer cle-completement-invalide"},
        json={
            "runtime": "hermes",
            "client_instance_id": "instance-intruse-0001",
            "requested_name": "Intrus",
            "capabilities": [],
        },
    )
    assert reponse.status_code == 401
    assert code_erreur(reponse) == "UNAUTHENTICATED"
    assert len(_evenements(EventType.AGENT_ENROLLMENT_REFUSED)) == 1
    with session_scope() as db:
        assert db.query(Agent).count() == 0


def test_enregistrement_sans_cle_refuse(client):
    """Aucune clé : refus explicite."""
    reponse = client.post(
        "/api/v1/agents/enroll",
        json={
            "runtime": "hermes",
            "client_instance_id": "instance-sans-cle-0001",
            "requested_name": "Sans clé",
            "capabilities": [],
        },
    )
    assert reponse.status_code == 401


def test_enregistrement_entree_malformee(client, cle_enregistrement):
    """Une demande incomplète est refusée sans révéler l'implémentation."""
    reponse = client.post(
        "/api/v1/agents/enroll",
        headers={"Authorization": f"Bearer {cle_enregistrement}"},
        json={"runtime": "hermes"},
    )
    assert reponse.status_code == 422
    assert code_erreur(reponse) == "VALIDATION_ERROR"
    assert "Traceback" not in reponse.text


def test_pas_de_doublon_pour_une_meme_instance(client, cle_enregistrement):
    """Une même identité locale ne crée pas un second agent (AGENT_CONNECTION.md §14)."""
    premier = enregistrer_agent(client, cle_enregistrement, instance_id=INSTANCE_A, nom="Agent unique")

    reponse = client.post(
        "/api/v1/agents/enroll",
        headers={"Authorization": f"Bearer {cle_enregistrement}"},
        json={
            "runtime": "hermes",
            "client_instance_id": INSTANCE_A,
            "requested_name": "Agent unique",
            "declared_role": "research",
            "capabilities": [],
        },
    )
    assert reponse.status_code == 409
    assert code_erreur(reponse) == "AGENT_ALREADY_ENROLLED"
    # L'agent existant est rappelé pour permettre au connecteur de reprendre sa session.
    assert premier["agent_id"] in reponse.text

    with session_scope() as db:
        assert db.query(Agent).count() == 1


def test_noms_uniques_attribues_par_le_serveur(client, cle_enregistrement):
    """Deux agents homonymes reçoivent des noms distincts."""
    premier = enregistrer_agent(client, cle_enregistrement, instance_id=INSTANCE_A, nom="Analyse")
    second = enregistrer_agent(client, cle_enregistrement, instance_id=INSTANCE_B, nom="Analyse")
    assert premier["name"] != second["name"]
    assert second["name"].startswith("Analyse")


def test_role_inconnu_ramene_au_role_par_defaut(client, cle_enregistrement):
    """Un rôle non prévu n'est jamais accordé tel quel."""
    corps = enregistrer_agent(
        client,
        cle_enregistrement,
        instance_id="instance-role-inconnu-01",
        nom="Agent au rôle fantaisiste",
        role="administrateur_supreme",
    )
    assert corps["role"] not in ("administrateur_supreme", "admin", "owner")
    assert corps["role"] == "general"
    assert corps["role"] in ROLES_AUTORISES


# ------------------------------------------------------- identité et présence
def test_identite_de_l_agent(client, agent):
    """``/agents/me`` renvoie l'identité attribuée, sans secret."""
    reponse = client.get("/api/v1/agents/me", headers=entetes_agent(agent["access_token"]))
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["agent_id"] == agent["agent_id"]
    assert corps["runtime"] == "hermes"
    assert "access_token" not in reponse.text
    assert "token_hash" not in reponse.text


def test_jeton_invalide_refuse(client, agent):
    """Un jeton inconnu est refusé."""
    reponse = client.get("/api/v1/agents/me", headers=entetes_agent("jeton-fabrique-de-toutes-pieces"))
    assert reponse.status_code == 401
    assert code_erreur(reponse) == "UNAUTHENTICATED"


def test_heartbeat_et_passage_en_ligne(client, agent):
    """Le premier signal place l'agent en ligne et l'horodate côté serveur."""
    reponse = client.post(
        "/api/v1/agents/heartbeat",
        headers=entetes_agent(agent["access_token"]),
        json={"status": "ONLINE", "runtime_status": "ready"},
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["accepted"] is True
    assert corps["agent_status"] == "ONLINE"
    assert corps["orchestrator_status"] == "ONLINE"

    enregistre = _agent(agent["agent_id"])
    assert enregistre.status == AgentStatus.ONLINE
    assert enregistre.last_seen_at is not None
    assert len(_evenements(EventType.AGENT_HEARTBEAT)) == 1


def test_detection_agent_hors_ligne(client, agent):
    """Un agent silencieux au-delà du seuil est basculé en OFFLINE."""
    client.post(
        "/api/v1/agents/heartbeat",
        headers=entetes_agent(agent["access_token"]),
        json={"status": "ONLINE"},
    )

    with session_scope() as db:
        enregistre = db.get(Agent, agent["agent_id"])
        enregistre.last_seen_at = utcnow() - timedelta(
            seconds=settings.agent_offline_threshold_seconds + 60
        )

    rapport = SurveillancePresence().controler_une_fois()
    assert rapport.get("agents_hors_ligne", 0) >= 1
    assert _agent(agent["agent_id"]).status == AgentStatus.OFFLINE
    assert len(_evenements(EventType.AGENT_WENT_OFFLINE)) == 1


def test_reconnexion_apres_interruption(client, agent):
    """Après une coupure, le signal suivant rétablit l'agent et l'annonce."""
    client.post(
        "/api/v1/agents/heartbeat",
        headers=entetes_agent(agent["access_token"]),
        json={"status": "ONLINE"},
    )
    with session_scope() as db:
        enregistre = db.get(Agent, agent["agent_id"])
        enregistre.last_seen_at = utcnow() - timedelta(
            seconds=settings.agent_offline_threshold_seconds + 60
        )
    SurveillancePresence().controler_une_fois()
    assert _agent(agent["agent_id"]).status == AgentStatus.OFFLINE

    reponse = client.post(
        "/api/v1/agents/heartbeat",
        headers=entetes_agent(agent["access_token"]),
        json={"status": "ONLINE"},
    )
    assert reponse.status_code == 200
    assert _agent(agent["agent_id"]).status == AgentStatus.ONLINE
    assert len(_evenements(EventType.AGENT_RECONNECTED)) == 1


# ----------------------------------------------------------- administration
def test_consultation_par_l_administrateur(client, agent, session_admin):
    """L'administrateur consulte les agents sans jamais voir de secret."""
    reponse = client.get("/api/v1/agents", headers=session_admin.entetes())
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["total"] == 1

    detail = client.get(f"/api/v1/agents/{agent['agent_id']}", headers=session_admin.entetes())
    assert detail.status_code == 200
    assert detail.json()["client_instance_id"] == INSTANCE_A

    for charge in (reponse.text, detail.text):
        assert "token_hash" not in charge
        assert "access_token" not in charge
        assert agent["access_token"] not in charge


def test_deconnexion_logique_conserve_le_jeton(client, agent, session_admin):
    """La déconnexion logique coupe la présence sans révoquer l'accès."""
    client.post(
        "/api/v1/agents/heartbeat",
        headers=entetes_agent(agent["access_token"]),
        json={"status": "ONLINE"},
    )
    reponse = client.post(
        f"/api/v1/agents/{agent['agent_id']}/disconnect",
        headers=session_admin.entetes(),
        json={"reason": "mise en maintenance"},
    )
    assert reponse.status_code == 200
    assert reponse.json()["status"] == "OFFLINE"
    assert len(_evenements(EventType.AGENT_DISCONNECTED)) == 1

    # Le jeton individuel reste valide : l'agent peut revenir sans se réenregistrer.
    retour = client.post(
        "/api/v1/agents/heartbeat",
        headers=entetes_agent(agent["access_token"]),
        json={"status": "ONLINE"},
    )
    assert retour.status_code == 200


def test_revocation_definitive(client, agent, session_admin, cle_enregistrement):
    """La révocation coupe l'accès immédiatement et annule les tâches en cours."""
    client.post(
        "/api/v1/agents/heartbeat",
        headers=entetes_agent(agent["access_token"]),
        json={"status": "ONLINE"},
    )

    reponse = client.post(
        f"/api/v1/agents/{agent['agent_id']}/revoke",
        headers=session_admin.entetes(),
        json={"reason": "fin de mission"},
    )
    assert reponse.status_code == 200
    assert reponse.json()["status"] == "REVOKED"

    with session_scope() as db:
        jetons = (
            db.query(Token)
            .filter(Token.agent_id == agent["agent_id"], Token.token_type == TokenType.AGENT)
            .all()
        )
        assert jetons and all(jeton.revoked_at is not None for jeton in jetons)

    # Le jeton révoqué est refusé.
    refuse = client.post(
        "/api/v1/agents/heartbeat",
        headers=entetes_agent(agent["access_token"]),
        json={"status": "ONLINE"},
    )
    assert refuse.status_code == 401
    assert len(_evenements(EventType.AGENT_TOKEN_REVOKED_USED)) == 1

    # Un nouvel enregistrement est possible : c'est une nouvelle identité.
    nouveau = enregistrer_agent(
        client, cle_enregistrement, instance_id=INSTANCE_A, nom="Agent re-enregistre"
    )
    assert nouveau["agent_id"] != agent["agent_id"]


def test_suppression_exige_la_revocation(client, agent, session_admin):
    """La suppression définitive n'est pas possible sans révocation préalable."""
    refus = client.delete(
        f"/api/v1/agents/{agent['agent_id']}", headers=session_admin.entetes()
    )
    assert refus.status_code == 409
    assert code_erreur(refus) == "AGENT_NOT_REVOKED"

    client.post(
        f"/api/v1/agents/{agent['agent_id']}/revoke",
        headers=session_admin.entetes(),
        json={"reason": "nettoyage"},
    )
    supprime = client.delete(
        f"/api/v1/agents/{agent['agent_id']}", headers=session_admin.entetes()
    )
    assert supprime.status_code == 204
    assert client.get(
        f"/api/v1/agents/{agent['agent_id']}", headers=session_admin.entetes()
    ).status_code == 404
    assert len(_evenements(EventType.AGENT_DELETED)) == 1


# --------------------------------------------------------------- cloisonnement
def test_un_agent_n_accede_pas_aux_routes_administrateur(client, agent):
    """Un jeton d'agent n'ouvre aucune route d'administration (SECURITY.md §6)."""
    entetes = entetes_agent(agent["access_token"])
    for chemin, methode in (
        ("/api/v1/agents", "get"),
        ("/api/v1/tasks", "get"),
        ("/api/v1/logs", "get"),
        ("/api/v1/settings/orchestrator", "get"),
        ("/api/v1/settings/enrollment-key", "get"),
    ):
        reponse = getattr(client, methode)(chemin, headers=entetes)
        assert reponse.status_code == 401, f"{methode.upper()} {chemin} devrait être refusé"


def test_les_capacites_declarees_n_accordent_aucun_droit(client, cle_enregistrement):
    """Les capacités déclarées sont des données, jamais une autorisation."""
    corps = enregistrer_agent(
        client,
        cle_enregistrement,
        instance_id="instance-capacites-01",
        nom="Agent tres capable",
        capacites=["admin", "superuser", "reveal_secrets"],
    )
    entetes = entetes_agent(corps["access_token"])

    # Les capacités sont conservées telles que déclarées (donc non fiables)...
    import json

    declarees = json.loads(_agent(corps["agent_id"]).capabilities)
    assert "admin" in declarees and "reveal_secrets" in declarees

    # ...et n'ouvrent aucun droit supplémentaire : aucune route d'administration,
    # aucun accès aux secrets.
    assert client.get("/api/v1/agents", headers=entetes).status_code == 401
    assert client.get("/api/v1/settings/enrollment-key", headers=entetes).status_code == 401
    assert client.get("/api/v1/settings/parameters", headers=entetes).status_code == 401
    assert client.get("/docs", headers=entetes).status_code == 401
