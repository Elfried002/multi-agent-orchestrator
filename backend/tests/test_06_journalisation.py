"""Journalisation et audit : contenu, filtres, expurgation.

Références : SECURITY.md §10, API.md §10, mission §12.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.config import settings
from app.database.connection import session_scope
from app.models.event import Event, EventSeverity, EventType
from app.utils.datetime_utils import to_iso, utcnow
from utils import INSTANCE_A, MOT_DE_PASSE_ADMIN, code_erreur, creer_tache, entetes_agent


def _tous_les_evenements() -> list[Event]:
    with session_scope() as db:
        return db.query(Event).order_by(Event.created_at).all()


# ------------------------------------------------------------ contenu du journal
def test_le_demarrage_du_service_est_journalise(client, session_admin):
    """Le démarrage et l'arrêt du service laissent une trace."""
    reponse = client.get(f"/api/v1/logs?event_type={EventType.SERVICE_STARTED}", headers=session_admin.entetes())
    assert reponse.status_code == 200
    assert reponse.json()["total"] == 1
    evenement = reponse.json()["items"][0]
    assert evenement["actor_type"] == "system"
    assert evenement["success"] is True
    assert evenement["created_at"]


def test_toutes_les_operations_cles_sont_journalisees(client, session_admin, agent):
    """Chaque opération importante produit un événement typé."""
    entetes = entetes_agent(agent["access_token"])
    client.post("/api/v1/agents/heartbeat", headers=entetes, json={"status": "ONLINE"})
    tache = creer_tache(client, session_admin.csrf, titre="Tache journalisee", agent_id=agent["agent_id"])
    client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)
    client.post(
        f"/api/v1/agents/tasks/{tache['id']}/result",
        headers=entetes,
        json={"status": "COMPLETED", "result": {"ok": True}},
    )
    client.post(f"/api/v1/agents/{agent['agent_id']}/disconnect", headers=session_admin.entetes())
    client.post("/api/v1/auth/logout")

    types = {evenement.event_type for evenement in _tous_les_evenements()}
    for attendu in (
        EventType.SERVICE_STARTED,
        EventType.ADMIN_LOGIN_SUCCEEDED,
        EventType.AGENT_ENROLLED,
        EventType.AGENT_HEARTBEAT,
        EventType.TASK_CREATED,
        EventType.TASK_ASSIGNED,
        EventType.TASK_ACKED,
        EventType.TASK_COMPLETED,
        EventType.AGENT_DISCONNECTED,
        EventType.ADMIN_LOGOUT,
    ):
        assert attendu in types, f"événement manquant : {attendu}"


def test_evenements_portent_acteur_objet_et_ip(client, session_admin, agent):
    """Chaque événement porte l'acteur, l'objet concerné et l'adresse observée."""
    entetes = entetes_agent(agent["access_token"])
    client.post("/api/v1/agents/heartbeat", headers=entetes, json={"status": "ONLINE"})
    tache = creer_tache(client, session_admin.csrf, titre="Tache tracee", agent_id=agent["agent_id"])
    client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)

    ack = [
        evenement
        for evenement in _tous_les_evenements()
        if evenement.event_type == EventType.TASK_ACKED
    ]
    assert len(ack) == 1
    evenement = ack[0]
    assert evenement.actor_type == "agent"
    assert evenement.actor_id == agent["agent_id"]
    assert evenement.agent_id == agent["agent_id"]
    assert evenement.object_type == "task"
    assert evenement.object_id == tache["id"]
    assert evenement.success is True
    assert evenement.request_id


# --------------------------------------------------------------------- filtres
def test_filtres_du_journal(client, session_admin):
    """Les filtres documentés fonctionnent réellement."""
    for titre in ("Tache alpha", "Tache beta", "Tache gamma"):
        creer_tache(client, session_admin.csrf, titre=titre)

    par_type = client.get(
        f"/api/v1/logs?event_type={EventType.TASK_CREATED}", headers=session_admin.entetes()
    ).json()
    assert par_type["total"] == 3

    par_gravite = client.get("/api/v1/logs?severity=INFO", headers=session_admin.entetes()).json()
    assert par_gravite["total"] >= 3
    assert all(item["severity"] == "INFO" for item in par_gravite["items"])

    par_acteur = client.get("/api/v1/logs?actor_type=admin", headers=session_admin.entetes()).json()
    assert par_acteur["total"] >= 3
    assert all(item["actor_type"] == "admin" for item in par_acteur["items"])

    recherche = client.get("/api/v1/logs?search=beta", headers=session_admin.entetes()).json()
    assert recherche["total"] == 1
    assert "beta" in recherche["items"][0]["message"]

    futur = to_iso(utcnow() + timedelta(days=1))
    apres_demain = to_iso(utcnow() + timedelta(days=2))
    vide = client.get(
        f"/api/v1/logs?start_date={futur}&end_date={apres_demain}", headers=session_admin.entetes()
    ).json()
    assert vide["total"] == 0, "aucun événement ne doit exister dans le futur"

    recent = client.get(
        f"/api/v1/logs?start_date={to_iso(utcnow() - timedelta(hours=1))}",
        headers=session_admin.entetes(),
    ).json()
    assert recent["total"] >= 3

    page = client.get("/api/v1/logs?page_size=2&page=2", headers=session_admin.entetes()).json()
    assert page["page"] == 2 and len(page["items"]) <= 2


def test_journal_trie_du_plus_recent_au_plus_ancien(client, session_admin):
    """L'ordre est décroissant : le plus récent en premier."""
    creer_tache(client, session_admin.csrf, titre="Tache premiere")
    creer_tache(client, session_admin.csrf, titre="Tache seconde")

    items = client.get(
        f"/api/v1/logs?event_type={EventType.TASK_CREATED}", headers=session_admin.entetes()
    ).json()["items"]
    assert items[0]["created_at"] >= items[1]["created_at"]
    assert "seconde" in items[0]["message"]


def test_gravite_inconnue_refusee(client, session_admin):
    """Une gravité inconnue n'est pas ignorée silencieusement."""
    reponse = client.get("/api/v1/logs?severity=INEXISTANTE", headers=session_admin.entetes())
    assert reponse.status_code == 422
    assert code_erreur(reponse) == "INVALID_SEVERITY"


def test_valeurs_de_reference_du_journal(client, session_admin):
    """Les valeurs de référence sont exposées pour construire les filtres."""
    corps = client.get("/api/v1/logs/reference", headers=session_admin.entetes()).json()
    assert set(corps["severites"]) == set(EventSeverity.TOUS)
    assert "task.created" in corps["types_evenement"]
    assert set(corps["types_acteur"]) == {"admin", "agent", "system", "anonymous"}
    assert corps["severites_sensibles"]


def test_detail_evenement_et_404(client, session_admin):
    """Le détail est consultable ; un identifiant inconnu renvoie 404."""
    tache = creer_tache(client, session_admin.csrf, titre="Tache pour detail")
    items = client.get(
        f"/api/v1/logs?event_type={EventType.TASK_CREATED}", headers=session_admin.entetes()
    ).json()["items"]
    identifiant = items[0]["id"]

    detail = client.get(f"/api/v1/logs/{identifiant}", headers=session_admin.entetes())
    assert detail.status_code == 200
    assert detail.json()["metadata"]["priorite"] == "NORMAL"

    absent = client.get("/api/v1/logs/evt_inexistant000000", headers=session_admin.entetes())
    assert absent.status_code == 404
    assert code_erreur(absent) == "NOT_FOUND"
    assert tache["id"]


def test_historique_par_agent(client, session_admin, agent):
    """L'historique d'un agent est consultable, et 404 si l'agent n'existe pas."""
    entetes = entetes_agent(agent["access_token"])
    client.post("/api/v1/agents/heartbeat", headers=entetes, json={"status": "ONLINE"})

    reponse = client.get(
        f"/api/v1/logs/agent/{agent['agent_id']}/historique", headers=session_admin.entetes()
    )
    assert reponse.status_code == 200
    assert reponse.json()["total"] >= 2
    assert all(item["agent_id"] == agent["agent_id"] for item in reponse.json()["items"])

    absent = client.get(
        "/api/v1/logs/agent/agt_inexistant000000/historique", headers=session_admin.entetes()
    )
    assert absent.status_code == 404


# ----------------------------------------------------------------- expurgation
def test_aucun_secret_dans_le_journal(client, session_admin, agent):
    """Aucun mot de passe, jeton ou clé n'apparaît dans le journal (SECURITY.md §10)."""
    entetes = entetes_agent(agent["access_token"])
    client.post("/api/v1/agents/heartbeat", headers=entetes, json={"status": "ONLINE"})

    # Une opération qui transporte un secret dans les données de la tâche.
    tache = creer_tache(
        client,
        session_admin.csrf,
        titre="Tache avec secret",
        description="Jeton interne : jeton-secret-abcdef et password=SecretEnClair-2026!",
    )
    client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)
    client.post(
        f"/api/v1/agents/tasks/{tache['id']}/result",
        headers=entetes,
        json={
            "status": "COMPLETED",
            "result": {"password": "SecretEnClair-2026!", "authorization": "Bearer jeton-secret-abcdef"},
        },
    )

    with session_scope() as db:
        charge = " ".join(
            f"{e.message} {e.metadata_json or ''} {e.source_ip or ''} {e.request_id or ''}"
            for e in db.query(Event).all()
        )

    for interdit in (
        MOT_DE_PASSE_ADMIN,
        agent["access_token"],
        "SecretEnClair-2026!",
        "jeton-secret-abcdef",
        "$argon2",
        "Bearer ",
    ):
        assert interdit not in charge, f"« {interdit} » ne doit jamais être journalisé"

    # L'API non plus ne les expose pas.
    for chemin in ("/api/v1/logs", f"/api/v1/logs/agent/{agent['agent_id']}/historique"):
        corps = client.get(chemin, headers=session_admin.entetes()).text
        for interdit in (MOT_DE_PASSE_ADMIN, agent["access_token"], "SecretEnClair-2026!"):
            assert interdit not in corps, chemin


def test_cle_enregistrement_jamais_journalisee(client, session_admin, cle_enregistrement):
    """La consultation de la clé est journalisée sans sa valeur (mission §9)."""
    avant = len(
        [e for e in _tous_les_evenements() if e.event_type == EventType.ENROLLMENT_KEY_READ]
    )

    reponse = client.get("/api/v1/settings/enrollment-key", headers=session_admin.entetes())
    assert reponse.status_code == 200
    assert reponse.json()["enrollment_key"] == cle_enregistrement

    with session_scope() as db:
        charge = " ".join(f"{e.message} {e.metadata_json or ''}" for e in db.query(Event).all())
    assert cle_enregistrement not in charge

    lu = [
        evenement
        for evenement in _tous_les_evenements()
        if evenement.event_type == EventType.ENROLLMENT_KEY_READ
    ]
    # La fixture de clé d'enregistrement lit déjà la clé une fois : c'est le delta
    # de cette consultation par l'API qui est vérifié ici.
    assert len(lu) == avant + 1
    assert lu[-1].actor_type == "admin"


def test_journal_inaccessible_sans_session(client):
    """Le journal est réservé à l'administrateur."""
    for chemin in ("/api/v1/logs", "/api/v1/logs/reference", "/api/v1/logs/evt_x"):
        assert client.get(chemin).status_code == 401, chemin


def test_taille_de_page_bornee(client, session_admin):
    """La pagination est bornée pour éviter un déni de service."""
    assert client.get("/api/v1/logs?page_size=5000", headers=session_admin.entetes()).status_code == 422
    assert client.get("/api/v1/logs?page=0", headers=session_admin.entetes()).status_code == 422
