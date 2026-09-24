"""Cycle de vie des tâches : transitions, idempotence, délais, cloisonnement.

Références : ARCHITECTURE.md §10, API.md §8, SECURITY.md §6.
"""

from __future__ import annotations

from datetime import timedelta

from app.database.connection import session_scope
from app.models.agent import Agent
from app.models.event import Event, EventType
from app.models.task import TaskStatus
from app.services.task_service import marquer_depassements
from app.utils.datetime_utils import utcnow
from utils import (
    INSTANCE_A,
    INSTANCE_B,
    code_erreur,
    creer_tache,
    enregistrer_agent,
    entetes_agent,
)


def _evenements(event_type: str) -> list[Event]:
    with session_scope() as db:
        return db.query(Event).filter(Event.event_type == event_type).all()


def _taches_annexes(client, cle, instance=INSTANCE_B, nom="Second agent"):
    """Enregistre un second agent (pour les tests de cloisonnement)."""
    corps = enregistrer_agent(client, cle, instance_id=instance, nom=nom)
    client.post(
        "/api/v1/agents/heartbeat",
        headers=entetes_agent(corps["access_token"]),
        json={"status": "ONLINE"},
    )
    return corps


# ------------------------------------------------------------------- création
def test_creation_tache_en_attente(client, session_admin):
    """Sans agent, la tâche reste en attente."""
    tache = creer_tache(client, session_admin.csrf, titre="Tâche à attribuer plus tard")
    assert tache["status"] == TaskStatus.PENDING
    assert tache["assigned_agent_id"] is None
    assert tache["created_by"] == session_admin.admin_id
    assert tache["deadline_at"] is not None, "une échéance est posée à la création"
    assert tache["attempt_count"] == 0
    assert len(_evenements(EventType.TASK_CREATED)) == 1


def test_creation_tache_attribuee(client, session_admin, agent):
    """Avec un agent, la tâche est attribuée immédiatement (ASSIGNED)."""
    tache = creer_tache(
        client, session_admin.csrf, titre="Analyse demandée", agent_id=agent["agent_id"]
    )
    assert tache["status"] == TaskStatus.ASSIGNED
    assert tache["assigned_at"] is not None
    assert len(_evenements(EventType.TASK_ASSIGNED)) == 1


def test_creation_tache_agent_inconnu(client, session_admin):
    """Un agent inexistant est refusé."""
    reponse = client.post(
        "/api/v1/tasks",
        headers=session_admin.entetes(),
        json={"title": "Tâche pour agent fantome", "assigned_agent_id": "agt_inexistant0000000"},
    )
    assert reponse.status_code == 404
    assert code_erreur(reponse) == "NOT_FOUND"


def test_creation_tache_agent_revoque_refusee(client, session_admin, agent):
    """Aucune tâche n'est confiée à un agent révoqué."""
    client.post(
        f"/api/v1/agents/{agent['agent_id']}/revoke",
        headers=session_admin.entetes(),
        json={"reason": "test"},
    )
    reponse = client.post(
        "/api/v1/tasks",
        headers=session_admin.entetes(),
        json={"title": "Tâche pour agent revoque", "assigned_agent_id": agent["agent_id"]},
    )
    assert reponse.status_code == 409
    assert code_erreur(reponse) == "AGENT_REVOKED"


def test_creation_tache_champs_invalides(client, session_admin):
    """Titre trop court et priorité inconnue sont refusés."""
    court = client.post(
        "/api/v1/tasks", headers=session_admin.entetes(), json={"title": "ab"}
    )
    assert court.status_code == 422

    priorite = client.post(
        "/api/v1/tasks",
        headers=session_admin.entetes(),
        json={"title": "Tâche priorité invalide", "priority": "SUPER_URGENT"},
    )
    assert priorite.status_code == 422
    assert code_erreur(priorite) == "VALIDATION_ERROR"


# --------------------------------------------------------------- cycle de vie
def test_cycle_complet(client, session_admin, agent):
    """PENDING/ASSIGNED → RUNNING → COMPLETED, avec conservation du résultat."""
    tache = creer_tache(client, session_admin.csrf, titre="Cycle complet", agent_id=agent["agent_id"])
    entetes = entetes_agent(agent["access_token"])

    # Récupération par l'agent.
    recues = client.get("/api/v1/agents/tasks", headers=entetes)
    assert recues.status_code == 200
    assert [t["id"] for t in recues.json()] == [tache["id"]]

    # Prise en charge.
    ack = client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)
    assert ack.status_code == 200
    assert ack.json()["status"] == TaskStatus.RUNNING
    assert ack.json()["deja_prise_en_charge"] is False

    # Une répétition du même état n'est pas une transition autorisée : elle est
    # refusée plutôt que comptée comme une progression.
    repete = client.post(
        f"/api/v1/agents/tasks/{tache['id']}/status",
        headers=entetes,
        json={"status": "RUNNING", "message": "Encore en cours."},
    )
    assert repete.status_code == 409
    assert code_erreur(repete) == "INVALID_TASK_TRANSITION"

    # Résultat.
    resultat = client.post(
        f"/api/v1/agents/tasks/{tache['id']}/result",
        headers=entetes,
        json={"status": "COMPLETED", "result": {"valeur": 42, "note": "terminé"}},
    )
    assert resultat.status_code == 200
    assert resultat.json()["status"] == TaskStatus.COMPLETED

    detail = client.get(f"/api/v1/tasks/{tache['id']}", headers=session_admin.entetes()).json()
    assert detail["result"]["valeur"] == 42
    assert detail["completed_at"] is not None
    assert detail["attempt_count"] == 1
    assert detail["assigned_agent_name"] == agent["name"]
    assert detail["peut_etre_annulee"] is False


def test_prise_en_charge_par_mise_a_jour_d_etat(client, session_admin, agent):
    """La route documentée ``/status`` permet la transition ASSIGNED → RUNNING."""
    tache = creer_tache(
        client, session_admin.csrf, titre="Prise en charge par statut", agent_id=agent["agent_id"]
    )
    entetes = entetes_agent(agent["access_token"])

    reponse = client.post(
        f"/api/v1/agents/tasks/{tache['id']}/status",
        headers=entetes,
        json={"status": "RUNNING", "message": "Exécution démarrée."},
    )
    assert reponse.status_code == 200
    assert reponse.json()["status"] == TaskStatus.RUNNING

    fin = client.post(
        f"/api/v1/agents/tasks/{tache['id']}/result",
        headers=entetes,
        json={"status": "COMPLETED", "result": {"ok": True}},
    )
    assert fin.status_code == 200
    assert fin.json()["status"] == TaskStatus.COMPLETED


def test_acquittement_repete_est_idempotent(client, session_admin, agent):
    """Une relance du connecteur ne déclenche pas une seconde exécution."""
    tache = creer_tache(client, session_admin.csrf, titre="Idempotence", agent_id=agent["agent_id"])
    entetes = entetes_agent(agent["access_token"])

    premier = client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)
    second = client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)
    assert premier.status_code == second.status_code == 200
    assert premier.json()["deja_prise_en_charge"] is False
    assert second.json()["deja_prise_en_charge"] is True

    with session_scope() as db:
        from app.models.task import Task

        assert db.get(Task, tache["id"]).attempt_count == 1
    assert len(_evenements(EventType.TASK_DUPLICATE_IGNORED)) == 1


def test_resultat_repete_est_ignore(client, session_admin, agent):
    """Un second envoi de résultat ne réécrit pas une tâche terminée."""
    tache = creer_tache(client, session_admin.csrf, titre="Resultat unique", agent_id=agent["agent_id"])
    entetes = entetes_agent(agent["access_token"])
    client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)

    premier = client.post(
        f"/api/v1/agents/tasks/{tache['id']}/result",
        headers=entetes,
        json={"status": "COMPLETED", "result": {"valeur": 1}},
    )
    assert premier.status_code == 200

    second = client.post(
        f"/api/v1/agents/tasks/{tache['id']}/result",
        headers=entetes,
        json={"status": "COMPLETED", "result": {"valeur": 2}},
    )
    assert second.status_code == 200

    detail = client.get(f"/api/v1/tasks/{tache['id']}", headers=session_admin.entetes()).json()
    assert detail["result"]["valeur"] == 1, "le premier résultat est conservé"


def test_transition_invalide_refusee(client, session_admin, agent):
    """Le backend contrôle les transitions : pas de retour en arrière."""
    tache = creer_tache(client, session_admin.csrf, titre="Transition interdite", agent_id=agent["agent_id"])
    entetes = entetes_agent(agent["access_token"])
    client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)
    client.post(
        f"/api/v1/agents/tasks/{tache['id']}/result",
        headers=entetes,
        json={"status": "COMPLETED", "result": {}},
    )

    # Une tâche terminée ne peut plus changer d'état.
    reponse = client.post(
        f"/api/v1/agents/tasks/{tache['id']}/status",
        headers=entetes,
        json={"status": "RUNNING"},
    )
    assert reponse.status_code == 409
    assert code_erreur(reponse) == "TASK_TERMINAL"


def test_statut_inconnu_refuse(client, session_admin, agent):
    """Un état hors machine à états est refusé."""
    tache = creer_tache(client, session_admin.csrf, titre="Etat inconnu", agent_id=agent["agent_id"])
    entetes = entetes_agent(agent["access_token"])
    client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)

    reponse = client.post(
        f"/api/v1/agents/tasks/{tache['id']}/status",
        headers=entetes,
        json={"status": "DONE"},
    )
    assert reponse.status_code == 422
    assert code_erreur(reponse) == "VALIDATION_ERROR"


def test_echec_avec_message_d_erreur(client, session_admin, agent):
    """Un échec est enregistré avec son motif, sans secret."""
    tache = creer_tache(client, session_admin.csrf, titre="Tache en echec", agent_id=agent["agent_id"])
    entetes = entetes_agent(agent["access_token"])
    client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)

    reponse = client.post(
        f"/api/v1/agents/tasks/{tache['id']}/result",
        headers=entetes,
        json={"status": "FAILED", "error_message": "Dépendance indisponible (HTTP 503)."},
    )
    assert reponse.status_code == 200
    assert reponse.json()["status"] == TaskStatus.FAILED

    detail = client.get(f"/api/v1/tasks/{tache['id']}", headers=session_admin.entetes()).json()
    assert "Dépendance indisponible" in detail["error_message"]
    assert len(_evenements(EventType.TASK_FAILED)) == 1


def test_resultat_trop_volumineux_refuse(client, session_admin, agent):
    """La taille des résultats est bornée (SECURITY.md §12)."""
    from app.core.config import settings

    tache = creer_tache(client, session_admin.csrf, titre="Resultat enorme", agent_id=agent["agent_id"])
    entetes = entetes_agent(agent["access_token"])
    client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)

    enorme = {"charge": "x" * (settings.max_result_bytes + 1024)}
    reponse = client.post(
        f"/api/v1/agents/tasks/{tache['id']}/result",
        headers=entetes,
        json={"status": "COMPLETED", "result": enorme},
    )
    assert reponse.status_code in (413, 422)

    detail = client.get(f"/api/v1/tasks/{tache['id']}", headers=session_admin.entetes()).json()
    assert detail["status"] == TaskStatus.RUNNING, "la tâche n'est pas terminée"


def test_secrets_expurges_du_resultat(client, session_admin, agent):
    """Un résultat contenant des secrets est expurgé avant persistance."""
    tache = creer_tache(client, session_admin.csrf, titre="Resultat avec secret", agent_id=agent["agent_id"])
    entetes = entetes_agent(agent["access_token"])
    client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)

    client.post(
        f"/api/v1/agents/tasks/{tache['id']}/result",
        headers=entetes,
        json={
            "status": "COMPLETED",
            "result": {
                "rapport": "ok",
                "password": "SecretEnClair-2026!",
                "authorization": "Bearer jeton-secret-abcdef",
            },
        },
    )

    detail = client.get(f"/api/v1/tasks/{tache['id']}", headers=session_admin.entetes())
    assert "SecretEnClair-2026!" not in detail.text
    assert "jeton-secret-abcdef" not in detail.text
    assert detail.json()["result"]["rapport"] == "ok"


# ------------------------------------------------ annulation, délais, filtres
def test_annulation_par_l_administrateur(client, session_admin):
    """Une tâche non terminale peut être annulée."""
    tache = creer_tache(client, session_admin.csrf, titre="Tache annulable")
    reponse = client.post(
        f"/api/v1/tasks/{tache['id']}/cancel",
        headers=session_admin.entetes(),
        json={"reason": "plus nécessaire"},
    )
    assert reponse.status_code == 200
    assert reponse.json()["status"] == TaskStatus.CANCELLED
    assert reponse.json()["cancelled_at"] is not None
    assert len(_evenements(EventType.TASK_CANCELLED)) == 1


def test_annulation_tache_terminale_refusee(client, session_admin):
    """L'annulation est refusée si la tâche est déjà terminale (API.md §8)."""
    tache = creer_tache(client, session_admin.csrf, titre="Tache deja annulee")
    client.post(f"/api/v1/tasks/{tache['id']}/cancel", headers=session_admin.entetes())

    seconde = client.post(
        f"/api/v1/tasks/{tache['id']}/cancel", headers=session_admin.entetes()
    )
    assert seconde.status_code == 409
    assert code_erreur(seconde) == "TASK_TERMINAL"


def test_depassement_de_delai_marque_en_timeout(client, session_admin, agent):
    """Une tâche dont l'échéance est dépassée passe en TIMEOUT."""
    tache = creer_tache(client, session_admin.csrf, titre="Tache trop lente", agent_id=agent["agent_id"])
    entetes = entetes_agent(agent["access_token"])
    client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)

    with session_scope() as db:
        from app.models.task import Task

        db.get(Task, tache["id"]).deadline_at = utcnow() - timedelta(minutes=5)

    marques = 0
    with session_scope() as db:
        marques = marquer_depassements(db)
    assert marques >= 1
    detail = client.get(f"/api/v1/tasks/{tache['id']}", headers=session_admin.entetes()).json()
    assert detail["status"] == TaskStatus.TIMEOUT
    assert len(_evenements(EventType.TASK_TIMEOUT)) == 1


def test_liste_taches_filtres_et_pagination(client, session_admin, agent):
    """La liste est filtrable et paginée, et ne présente que des données réelles."""
    for index in range(3):
        creer_tache(client, session_admin.csrf, titre=f"Tache filtree {index}", priorite="HIGH")
    creer_tache(
        client, session_admin.csrf, titre="Tache assignee isolee", agent_id=agent["agent_id"]
    )

    toutes = client.get("/api/v1/tasks", headers=session_admin.entetes()).json()
    assert toutes["total"] == 4
    assert toutes["page"] == 1

    haute = client.get("/api/v1/tasks?priority=HIGH", headers=session_admin.entetes()).json()
    assert haute["total"] == 3

    assignees = client.get(
        f"/api/v1/tasks?status=ASSIGNED&agent_id={agent['agent_id']}",
        headers=session_admin.entetes(),
    ).json()
    assert assignees["total"] == 1

    recherche = client.get(
        "/api/v1/tasks?search=isolee", headers=session_admin.entetes()
    ).json()
    assert recherche["total"] == 1

    page = client.get("/api/v1/tasks?page=2&page_size=3", headers=session_admin.entetes()).json()
    assert page["page"] == 2 and len(page["items"]) == 1


def test_taches_recues_par_l_agent_limitees_a_son_perimetre(client, session_admin, agent, cle_enregistrement):
    """L'agent ne voit que ses propres tâches."""
    second = _taches_annexes(client, cle_enregistrement)

    sienne = creer_tache(
        client, session_admin.csrf, titre="Tache pour le premier agent", agent_id=agent["agent_id"]
    )
    autre = creer_tache(
        client, session_admin.csrf, titre="Tache pour le second agent", agent_id=second["agent_id"]
    )

    premier = client.get("/api/v1/agents/tasks", headers=entetes_agent(agent["access_token"])).json()
    assert [t["id"] for t in premier] == [sienne["id"]]
    assert autre["id"] not in [t["id"] for t in premier]


def test_un_agent_ne_peut_pas_traiter_la_tache_d_un_autre(client, session_admin, agent, cle_enregistrement):
    """Toute opération sur la tâche d'un autre agent est refusée (SECURITY.md §6)."""
    second = _taches_annexes(client, cle_enregistrement)
    tache = creer_tache(
        client, session_admin.csrf, titre="Tache du premier agent", agent_id=agent["agent_id"]
    )
    entetes = entetes_agent(second["access_token"])

    for chemin, corps in (
        (f"/api/v1/agents/tasks/{tache['id']}/ack", None),
        (f"/api/v1/agents/tasks/{tache['id']}/status", {"status": "RUNNING"}),
        (f"/api/v1/agents/tasks/{tache['id']}/result", {"status": "COMPLETED", "result": {}}),
    ):
        reponse = client.post(chemin, headers=entetes, json=corps) if corps else client.post(
            chemin, headers=entetes
        )
        assert reponse.status_code == 403, chemin
        assert code_erreur(reponse) in ("FORBIDDEN", "TASK_NOT_ASSIGNED_TO_AGENT")

    # La tâche n'a subi aucune modification.
    detail = client.get(f"/api/v1/tasks/{tache['id']}", headers=session_admin.entetes()).json()
    assert detail["status"] == TaskStatus.ASSIGNED
    assert len(_evenements(EventType.AGENT_FORBIDDEN)) >= 3


def test_machine_a_etats_exposee(client, session_admin):
    """La machine à états appliquée est exposée pour le tableau de bord."""
    reponse = client.get("/api/v1/tasks/state-machine", headers=session_admin.entetes())
    assert reponse.status_code == 200
    corps = reponse.json()
    for etat in ("PENDING", "ASSIGNED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"):
        assert etat in corps["etats"]
    assert set(corps["etats_terminaux"]) == {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"}
    # Cohérence avec la table appliquée par le service (l'ordre n'importe pas ici).
    attendu = {etat: sorted(cibles) for etat, cibles in TaskStatus.TRANSITIONS.items()}
    obtenu = {etat: sorted(cibles) for etat, cibles in corps["transitions"].items()}
    assert obtenu == attendu


def test_tache_introuvable(client, session_admin):
    """Une tâche inexistante renvoie 404 dans le format normalisé."""
    reponse = client.get("/api/v1/tasks/tsk_inexistante000000", headers=session_admin.entetes())
    assert reponse.status_code == 404
    assert code_erreur(reponse) == "NOT_FOUND"


def test_agent_inexistant_renvoie_404(client, session_admin):
    """Un agent inexistant renvoie 404."""
    reponse = client.get("/api/v1/agents/agt_inexistant000000", headers=session_admin.entetes())
    assert reponse.status_code == 404
    assert code_erreur(reponse) == "NOT_FOUND"


def test_agent_revoque_conserve_presence_en_base(client, session_admin, agent):
    """La révocation est persistante : l'état de l'agent est bien REVOKED en base."""
    client.post(
        f"/api/v1/agents/{agent['agent_id']}/revoke",
        headers=session_admin.entetes(),
        json={"reason": "test de persistance"},
    )
    with session_scope() as db:
        enregistre = db.get(Agent, agent["agent_id"])
        assert enregistre.status == "REVOKED"
        assert enregistre.revoked_at is not None
