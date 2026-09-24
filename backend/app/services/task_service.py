"""Service des tâches : création, attribution, prise en charge, résultat, délais.

Le backend est la seule autorité sur le cycle de vie (ARCHITECTURE.md §10) :

``PENDING → ASSIGNED → RUNNING → COMPLETED | FAILED | TIMEOUT``
et ``CANCELLED`` depuis tout état non terminal.

Points de sécurité et de fiabilité :

- **appartenance** : un agent ne peut agir que sur ses propres tâches
  (SECURITY.md §6, API.md §11) ;
- **idempotence** : une répétition de requête (acquittement, transmission de
  résultat) ne provoque jamais une double exécution ni un double enregistrement
  (mission §8, AGENT_CONNECTION.md §9) ;
- **résultats non fiables** : le résultat d'un agent est expurgé des secrets, sa
  taille est bornée et il est traité comme une donnée non fiable
  (SECURITY.md §9) ;
- **délais** : les tâches dépassant leur échéance passent à ``TIMEOUT``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit.event_service import generer_identifiant, record_event
from app.core.config import settings
from app.core.errors import ErreurAPI, erreur_conflit, erreur_introuvable
from app.models.agent import Agent, AgentStatus
from app.models.event import EventActorType, EventSeverity, EventType
from app.models.task import Task, TaskPriority, TaskStatus
from app.utils.datetime_utils import humanize_duration, parse_iso, utcnow
from app.utils.validators import (
    ErreurValidation,
    assert_no_secrets,
    deserialiser_json,
    sanitize_text,
    serialiser_json,
    strip_sensitive_keys,
)

logger = logging.getLogger(__name__)


# ============================================================ lecture =======
def lister_taches(
    db: Session,
    *,
    status: str | None = None,
    agent_id: str | None = None,
    priority: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Task], int]:
    """Liste filtrée et paginée des tâches (API.md §8.2)."""
    page = max(1, page)
    page_size = max(1, min(page_size, 200))

    conditions = []
    if status:
        conditions.append(Task.status == status.strip().upper())
    if agent_id:
        conditions.append(Task.assigned_agent_id == agent_id)
    if priority:
        conditions.append(Task.priority == priority.strip().upper())
    if search:
        motif = f"%{search.strip()}%"
        conditions.append(or_(Task.title.ilike(motif), Task.description.ilike(motif)))

    requete = select(Task)
    requete_compte = select(func.count()).select_from(Task)
    if conditions:
        requete = requete.where(*conditions)
        requete_compte = requete_compte.where(*conditions)

    total = int(db.execute(requete_compte).scalar_one())
    lignes = (
        db.execute(
            requete.order_by(Task.created_at.desc(), Task.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(lignes), total


def obtenir_tache(db: Session, task_id: str) -> Task:
    """Retourne une tâche ou lève une erreur 404."""
    tache = db.get(Task, task_id)
    if tache is None:
        raise erreur_introuvable(f"Tâche introuvable : {task_id}")
    return tache


def taches_pour_agent(db: Session, agent: Agent, *, limite: int = 50) -> list[Task]:
    """Tâches attribuées à un agent et prêtes à être traitées (API.md §6.4).

    Sont retournées les tâches ``ASSIGNED`` (à prendre en charge) **et** ``RUNNING``
    (à reprendre après une interruption) : c'est ce qui permet au connecteur de
    reprendre son travail sans doublon après un redémarrage.
    """
    if agent.status == AgentStatus.REVOKED:
        raise ErreurAPI("AGENT_REVOKED", "Agent révoqué : aucune tâche ne peut être remise.", status_code=403)
    return list(
        db.execute(
            select(Task)
            .where(
                Task.assigned_agent_id == agent.id,
                Task.status.in_((TaskStatus.ASSIGNED, TaskStatus.RUNNING)),
            )
            .order_by(Task.priority.desc(), Task.created_at.asc())
            .limit(limite)
        )
        .scalars()
        .all()
    )


def resultat_tache(tache: Task) -> dict[str, Any] | None:
    """Résultat d'une tâche, désérialisé."""
    if not tache.result:
        return None
    charge = deserialiser_json(tache.result, None)
    return charge if isinstance(charge, dict) else {"valeur": charge}


def peut_etre_annulee(tache: Task) -> bool:
    """Une tâche terminale ne peut plus être annulée (API.md §8.4)."""
    return not TaskStatus.est_terminal(tache.status)


# =========================================================== création ======
def creer_tache(
    db: Session,
    *,
    titre: str,
    description: str | None = None,
    priorite: str = TaskPriority.NORMAL,
    agent_id: str | None = None,
    admin_id: str | None = None,
    delai_secondes: int | None = None,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> Task:
    """Crée une tâche, éventuellement déjà attribuée à un agent (API.md §8.1)."""
    maintenant = utcnow()
    delai = delai_secondes or settings.task_default_timeout_seconds
    tache = Task(
        id=generer_identifiant("tsk"),
        title=sanitize_text(titre, max_length=200),
        description=sanitize_text(description, max_length=8000) if description else None,
        status=TaskStatus.PENDING,
        priority=priorite,
        created_by=admin_id,
        created_at=maintenant,
        updated_at=maintenant,
        deadline_at=maintenant + timedelta(seconds=delai),
    )

    agent: Agent | None = None
    if agent_id:
        agent = db.get(Agent, agent_id)
        if agent is None:
            raise erreur_introuvable(f"Agent introuvable : {agent_id}")
        if agent.status == AgentStatus.REVOKED:
            raise erreur_conflit(
                "AGENT_REVOKED", "Cet agent est révoqué : aucune tâche ne peut lui être attribuée."
            )
        tache.assigned_agent_id = agent.id
        tache.status = TaskStatus.ASSIGNED
        tache.assigned_at = maintenant

    db.add(tache)
    db.commit()
    db.refresh(tache)

    record_event(
        db,
        event_type=EventType.TASK_CREATED,
        message=f"Tâche « {tache.title} » créée (priorité {tache.priority}).",
        severity=EventSeverity.INFO,
        actor_type=EventActorType.ADMIN if admin_id else EventActorType.SYSTEM,
        actor_id=admin_id,
        agent_id=tache.assigned_agent_id,
        source_ip=source_ip,
        request_id=request_id,
        object_type="task",
        object_id=tache.id,
        success=True,
        metadata={"priorite": tache.priority, "echeance": tache.deadline_at},
    )
    if agent is not None:
        record_event(
            db,
            event_type=EventType.TASK_ASSIGNED,
            message=f"Tâche « {tache.title} » attribuée à l'agent « {agent.name} ».",
            severity=EventSeverity.INFO,
            actor_type=EventActorType.ADMIN if admin_id else EventActorType.SYSTEM,
            actor_id=admin_id,
            agent_id=agent.id,
            source_ip=source_ip,
            request_id=request_id,
            object_type="task",
            object_id=tache.id,
            success=True,
        )
    return tache


def assigner_tache(
    db: Session,
    tache: Task,
    agent: Agent,
    *,
    actor_id: str | None = None,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> Task:
    """Attribue une tâche en attente à un agent (API.md §8)."""
    if TaskStatus.est_terminal(tache.status):
        raise erreur_conflit(
            "TASK_TERMINAL", "Cette tâche est terminée : elle ne peut plus être réattribuée."
        )
    if tache.status == TaskStatus.RUNNING:
        raise erreur_conflit(
            "TASK_RUNNING", "Cette tâche est en cours : elle ne peut pas être réattribuée."
        )
    if agent.status == AgentStatus.REVOKED:
        raise erreur_conflit(
            "AGENT_REVOKED", "Cet agent est révoqué : aucune tâche ne peut lui être attribuée."
        )

    maintenant = utcnow()
    tache.assigned_agent_id = agent.id
    tache.status = TaskStatus.ASSIGNED
    tache.assigned_at = maintenant
    tache.updated_at = maintenant
    db.add(tache)
    db.commit()
    db.refresh(tache)

    record_event(
        db,
        event_type=EventType.TASK_ASSIGNED,
        message=f"Tâche « {tache.title} » attribuée à l'agent « {agent.name} ».",
        severity=EventSeverity.INFO,
        actor_type=EventActorType.ADMIN if actor_id else EventActorType.SYSTEM,
        actor_id=actor_id,
        agent_id=agent.id,
        source_ip=source_ip,
        request_id=request_id,
        object_type="task",
        object_id=tache.id,
        success=True,
    )
    return tache


# =================================================== cycle de vie agent =====
def _verifier_appartenance(
    db: Session, tache: Task, agent: Agent, *, request_id: str | None = None
) -> None:
    """Vérifie que la tâche est bien attribuée à cet agent.

    Un refus est une tentative d'accès à une ressource non autorisée : il est
    **journalisé** (SECURITY.md §10) avant d'être refusé, sans quoi une tentative
    inter-agents ne laisserait aucune trace exploitable.
    """
    if tache.assigned_agent_id is not None and tache.assigned_agent_id == agent.id:
        return

    record_event(
        db,
        event_type=EventType.AGENT_FORBIDDEN,
        message=(
            "Tentative d'opération sur une tâche attribuée à un autre agent."
            if tache.assigned_agent_id
            else "Tentative d'opération sur une tâche non attribuée."
        ),
        severity=EventSeverity.WARNING,
        actor_type=EventActorType.AGENT,
        actor_id=agent.id,
        agent_id=agent.id,
        request_id=request_id,
        object_type="task",
        object_id=tache.id,
        success=False,
        metadata={"agent_proprietaire": tache.assigned_agent_id},
    )
    raise ErreurAPI(
        "TASK_NOT_ASSIGNED_TO_AGENT",
        "Cette tâche n'est pas attribuée à cet agent.",
        status_code=403,
    )


def acquitter_tache(db: Session, tache: Task, agent: Agent, *, request_id: str | None = None) -> tuple[Task, bool]:
    """Confirme la prise en charge d'une tâche (API.md §6.5).

    Retourne ``(tache, deja_acquittee)``. Un second acquittement de la même tâche
    par le même agent est **idempotent** : il ne redéclenche aucune exécution.
    """
    _verifier_appartenance(db, tache, agent, request_id=request_id)

    if TaskStatus.est_terminal(tache.status):
        raise erreur_conflit(
            "TASK_TERMINAL", "Cette tâche est déjà terminée : la prise en charge est sans objet."
        )

    if tache.status == TaskStatus.RUNNING:
        record_event(
            db,
            event_type=EventType.TASK_DUPLICATE_IGNORED,
            message=f"Acquittement répété ignoré pour la tâche « {tache.title} ».",
            severity=EventSeverity.INFO,
            actor_type=EventActorType.AGENT,
            actor_id=agent.id,
            agent_id=agent.id,
            request_id=request_id,
            object_type="task",
            object_id=tache.id,
            success=True,
            metadata={"etat": tache.status},
        )
        return tache, True

    maintenant = utcnow()
    tache.status = TaskStatus.RUNNING
    tache.acked_at = maintenant
    tache.started_at = maintenant
    tache.updated_at = maintenant
    tache.attempt_count = (tache.attempt_count or 0) + 1
    db.add(tache)
    db.commit()
    db.refresh(tache)

    record_event(
        db,
        event_type=EventType.TASK_ACKED,
        message=f"Tâche « {tache.title} » prise en charge par l'agent « {agent.name} ».",
        severity=EventSeverity.INFO,
        actor_type=EventActorType.AGENT,
        actor_id=agent.id,
        agent_id=agent.id,
        request_id=request_id,
        object_type="task",
        object_id=tache.id,
        success=True,
        metadata={"tentative": tache.attempt_count},
    )
    return tache, False


def maj_statut_tache(
    db: Session,
    tache: Task,
    agent: Agent,
    *,
    nouveau_statut: str,
    message: str | None = None,
    request_id: str | None = None,
) -> Task:
    """Met à jour l'état d'une tâche depuis un agent (API.md §6.6)."""
    _verifier_appartenance(db, tache, agent, request_id=request_id)

    cible = (nouveau_statut or "").strip().upper()
    if cible not in (TaskStatus.RUNNING, TaskStatus.FAILED):
        raise ErreurAPI(
            "INVALID_TASK_TRANSITION",
            "Un agent peut uniquement déclarer RUNNING ou FAILED.",
            status_code=422,
        )
    # Un état terminal est distingué d'une transition simplement interdite : le
    # client doit savoir que la tâche est close, et non que sa demande est mal
    # formulée.
    if TaskStatus.est_terminal(tache.status):
        raise erreur_conflit(
            "TASK_TERMINAL",
            f"Cette tâche est déjà terminée ({tache.status}) : aucun changement d'état "
            "n'est possible.",
        )
    if not TaskStatus.transition_autorisee(tache.status, cible):
        raise erreur_conflit(
            "INVALID_TASK_TRANSITION",
            f"Transition refusée : {tache.status} → {cible}.",
        )

    maintenant = utcnow()
    tache.status = cible
    tache.updated_at = maintenant
    if cible == TaskStatus.RUNNING and tache.started_at is None:
        tache.started_at = maintenant
    if cible == TaskStatus.FAILED:
        tache.error_message = sanitize_text(message, max_length=2000) or "Échec signalé par l'agent."
        tache.completed_at = maintenant
    db.add(tache)
    db.commit()
    db.refresh(tache)

    record_event(
        db,
        event_type=(EventType.TASK_FAILED if cible == TaskStatus.FAILED else EventType.TASK_STATUS_CHANGED),
        message=(
            f"Tâche « {tache.title} » : état {cible}"
            + (f" — {sanitize_text(message, max_length=200)}" if message else "")
        ),
        severity=EventSeverity.WARNING if cible == TaskStatus.FAILED else EventSeverity.INFO,
        actor_type=EventActorType.AGENT,
        actor_id=agent.id,
        agent_id=agent.id,
        request_id=request_id,
        object_type="task",
        object_id=tache.id,
        success=cible != TaskStatus.FAILED,
        metadata={"etat": cible},
    )
    return tache


def soumettre_resultat(
    db: Session,
    tache: Task,
    agent: Agent,
    *,
    statut: str,
    resultat: dict[str, Any] | None = None,
    message_erreur: str | None = None,
    request_id: str | None = None,
) -> tuple[Task, bool]:
    """Transmet le résultat d'une tâche (API.md §6.7).

    Retourne ``(tache, deja_terminee)``. Une retransmission du résultat d'une tâche
    déjà terminée est idempotente : elle n'écrase pas le résultat enregistré et ne
    crée pas de doublon d'historique.
    """
    _verifier_appartenance(db, tache, agent, request_id=request_id)

    cible = (statut or "").strip().upper()
    if cible not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        raise ErreurAPI(
            "INVALID_TASK_TRANSITION",
            "Le statut final doit être COMPLETED ou FAILED.",
            status_code=422,
        )

    if TaskStatus.est_terminal(tache.status):
        deja = tache.status == cible
        record_event(
            db,
            event_type=EventType.TASK_DUPLICATE_IGNORED,
            message=(
                f"Résultat retransmis ignoré pour la tâche « {tache.title} » "
                f"(état déjà {tache.status})."
            ),
            severity=EventSeverity.INFO,
            actor_type=EventActorType.AGENT,
            actor_id=agent.id,
            agent_id=agent.id,
            request_id=request_id,
            object_type="task",
            object_id=tache.id,
            success=True,
            metadata={"etat": tache.status, "statut_transmis": cible},
        )
        return tache, deja

    if not TaskStatus.transition_autorisee(tache.status, cible):
        raise erreur_conflit(
            "INVALID_TASK_TRANSITION",
            f"Transition refusée : {tache.status} → {cible}.",
        )

    resultat_propre: str | None = None
    if resultat is not None:
        if not isinstance(resultat, dict):
            raise ErreurAPI(
                "INVALID_RESULT", "Le résultat doit être un objet JSON.", status_code=422
            )
        try:
            assert_no_secrets(resultat)
            propre = strip_sensitive_keys(resultat)
            resultat_propre = serialiser_json(propre, max_bytes=settings.max_result_bytes)
        except ErreurValidation as erreur:
            raise ErreurAPI(erreur.code, erreur.message, status_code=422) from erreur

    maintenant = utcnow()
    tache.status = cible
    tache.updated_at = maintenant
    tache.completed_at = maintenant
    if resultat_propre is not None:
        tache.result = resultat_propre
    if cible == TaskStatus.FAILED:
        tache.error_message = (
            sanitize_text(message_erreur, max_length=2000)
            or "Échec signalé par l'agent sans message d'erreur."
        )
    if tache.started_at is None:
        tache.started_at = maintenant
    db.add(tache)
    db.commit()
    db.refresh(tache)

    duree = None
    if tache.started_at:
        duree = (maintenant - tache.started_at).total_seconds()
    record_event(
        db,
        event_type=(EventType.TASK_COMPLETED if cible == TaskStatus.COMPLETED else EventType.TASK_FAILED),
        message=(
            f"Tâche « {tache.title} » terminée ({cible})"
            + (f" en {humanize_duration(duree)}" if duree else "")
            + "."
        ),
        severity=EventSeverity.INFO if cible == TaskStatus.COMPLETED else EventSeverity.WARNING,
        actor_type=EventActorType.AGENT,
        actor_id=agent.id,
        agent_id=agent.id,
        request_id=request_id,
        object_type="task",
        object_id=tache.id,
        success=cible == TaskStatus.COMPLETED,
        # Le contenu du résultat n'est pas recopié dans l'événement : il est déjà
        # conservé sur la tâche, et peut contenir des données volumineuses.
        metadata={"etat": cible, "duree_secondes": duree},
    )
    return tache, False


# ================================================== administration =========
def annuler_tache(
    db: Session,
    tache: Task,
    *,
    motif: str | None = None,
    actor_id: str | None = None,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> Task:
    """Annule une tâche non terminale (API.md §8.4)."""
    if TaskStatus.est_terminal(tache.status):
        raise erreur_conflit(
            "TASK_TERMINAL",
            f"Cette tâche est déjà dans un état terminal ({tache.status}) : annulation refusée.",
        )

    maintenant = utcnow()
    tache.status = TaskStatus.CANCELLED
    tache.cancelled_at = maintenant
    tache.updated_at = maintenant
    tache.completed_at = maintenant
    if motif:
        tache.error_message = sanitize_text(motif, max_length=2000)
    db.add(tache)
    db.commit()
    db.refresh(tache)

    record_event(
        db,
        event_type=EventType.TASK_CANCELLED,
        message=f"Tâche « {tache.title} » annulée" + (f" : {motif}" if motif else "."),
        severity=EventSeverity.WARNING,
        actor_type=EventActorType.ADMIN if actor_id else EventActorType.SYSTEM,
        actor_id=actor_id,
        agent_id=tache.assigned_agent_id,
        source_ip=source_ip,
        request_id=request_id,
        object_type="task",
        object_id=tache.id,
        success=True,
        metadata={"motif": motif},
    )
    return tache


def clore_taches_agent_revoque(db: Session, agent: Agent, *, motif: str) -> int:
    """Clôt les tâches en cours d'un agent révoqué (AGENT_CONNECTION.md §13).

    Politique retenue : les tâches ``ASSIGNED`` ou ``RUNNING`` passent à
    ``CANCELLED`` avec le motif de révocation — elles ne peuvent plus aboutir, et les
    laisser « en cours » fausserait la supervision.
    """
    maintenant = utcnow()
    taches = (
        db.execute(
            select(Task).where(
                Task.assigned_agent_id == agent.id,
                Task.status.in_((TaskStatus.ASSIGNED, TaskStatus.RUNNING)),
            )
        )
        .scalars()
        .all()
    )
    for tache in taches:
        tache.status = TaskStatus.CANCELLED
        tache.cancelled_at = maintenant
        tache.completed_at = maintenant
        tache.updated_at = maintenant
        tache.error_message = f"Tâche close : agent révoqué ({motif})."
        db.add(tache)
        record_event(
            db,
            event_type=EventType.TASK_CANCELLED,
            message=f"Tâche « {tache.title} » annulée : agent révoqué.",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.SYSTEM,
            agent_id=agent.id,
            object_type="task",
            object_id=tache.id,
            success=False,
            metadata={"motif": motif},
            commit=False,
        )
    if taches:
        db.commit()
    return len(taches)


def marquer_depassements(db: Session, *, limite: int = 200) -> int:
    """Passe à ``TIMEOUT`` les tâches dont l'échéance est dépassée.

    Les tâches en attente dont l'échéance est passée sont également concernées :
    une tâche jamais prise en charge ne doit pas rester indéfiniment en suspens.
    """
    maintenant = utcnow()
    taches = (
        db.execute(
            select(Task)
            .where(
                Task.status.in_((TaskStatus.PENDING, TaskStatus.ASSIGNED, TaskStatus.RUNNING)),
                Task.deadline_at.is_not(None),
                Task.deadline_at < maintenant,
            )
            .limit(limite)
        )
        .scalars()
        .all()
    )
    for tache in taches:
        tache.status = TaskStatus.TIMEOUT
        tache.completed_at = maintenant
        tache.updated_at = maintenant
        tache.error_message = "Délai d'exécution dépassé."
        db.add(tache)
        record_event(
            db,
            event_type=EventType.TASK_TIMEOUT,
            message=f"Tâche « {tache.title} » déclarée en dépassement de délai.",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.SYSTEM,
            agent_id=tache.assigned_agent_id,
            object_type="task",
            object_id=tache.id,
            success=False,
            commit=False,
        )
    if taches:
        db.commit()
        logger.info("%s tâche(s) marquée(s) en dépassement de délai", len(taches))
    return len(taches)


def statistiques_taches(db: Session) -> dict[str, int]:
    """Compteurs de tâches par état, pour le tableau de bord."""
    par_etat = {
        etat: int(
            db.execute(
                select(func.count()).select_from(Task).where(Task.status == etat)
            ).scalar_one()
        )
        for etat in TaskStatus.TOUS
    }
    return {
        "total": sum(par_etat.values()),
        "en_attente": par_etat[TaskStatus.PENDING],
        "assignees": par_etat[TaskStatus.ASSIGNED],
        "en_cours": par_etat[TaskStatus.RUNNING],
        "terminees": par_etat[TaskStatus.COMPLETED],
        "en_echec": par_etat[TaskStatus.FAILED],
        "annulees": par_etat[TaskStatus.CANCELLED],
        "delai_depasse": par_etat[TaskStatus.TIMEOUT],
        "par_etat": par_etat,
    }


def echeance_depassee(tache: Task) -> bool:
    """Indique si l'échéance d'une tâche non terminale est dépassée."""
    if tache.deadline_at is None or TaskStatus.est_terminal(tache.status):
        return False
    echeance = parse_iso(tache.deadline_at)
    return bool(echeance and echeance <= utcnow())
