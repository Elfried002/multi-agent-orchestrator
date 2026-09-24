"""Service des agents : présence, déconnexion, révocation, consultation.

Le service est la seule autorité sur l'état d'un agent :

- un heartbeat est horodaté par le **serveur**, jamais par l'agent
  (AGENT_CONNECTION.md §8) ;
- les états ``OFFLINE`` et ``REVOKED`` sont déterminés par le serveur, jamais
  déclarés par l'agent ;
- une déconnexion logique n'est **pas** une révocation : le jeton reste valide
  (API.md §7.3) ;
- la révocation invalide immédiatement les jetons et clôt les tâches en cours
  (API.md §7.4, AGENT_CONNECTION.md §13).
"""

from __future__ import annotations

import logging

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit.event_service import record_event
from app.core.errors import erreur_conflit, erreur_introuvable
from app.models.agent import Agent, AgentStatus
from app.models.event import Event, EventActorType, EventSeverity, EventType
from app.models.task import Task, TaskStatus
from app.utils.datetime_utils import age_seconds, humanize_duration, utcnow
from app.utils.validators import deserialiser_json

logger = logging.getLogger(__name__)


# ============================================================ lecture =======
def lister_agents(
    db: Session,
    *,
    status: str | None = None,
    role: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Agent], int]:
    """Liste filtrée et paginée des agents (API.md §7.1)."""
    page = max(1, page)
    page_size = max(1, min(page_size, 200))

    conditions = []
    if status:
        conditions.append(Agent.status == status.strip().upper())
    if role:
        conditions.append(Agent.role == role.strip().lower())
    if search:
        motif = f"%{search.strip()}%"
        conditions.append(
            or_(
                Agent.name.ilike(motif),
                Agent.id.ilike(motif),
                Agent.runtime.ilike(motif),
                Agent.source_ip.ilike(motif),
            )
        )

    requete = select(Agent)
    requete_compte = select(func.count()).select_from(Agent)
    if conditions:
        requete = requete.where(*conditions)
        requete_compte = requete_compte.where(*conditions)

    total = int(db.execute(requete_compte).scalar_one())
    lignes = (
        db.execute(
            requete.order_by(Agent.created_at.desc(), Agent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(lignes), total


def obtenir_agent(db: Session, agent_id: str) -> Agent:
    """Retourne un agent ou lève une erreur 404."""
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise erreur_introuvable(f"Agent introuvable : {agent_id}")
    return agent


def capacites_agent(agent: Agent) -> list[str]:
    """Capacités déclarées par l'agent (données non fiables, informatives)."""
    charge = deserialiser_json(agent.capabilities, [])
    return [str(element) for element in charge] if isinstance(charge, list) else []


def metadonnees_agent(agent: Agent) -> dict:
    charge = deserialiser_json(agent.metadata_json, {})
    return charge if isinstance(charge, dict) else {}


def historique_agent(db: Session, agent_id: str, *, limit: int = 50) -> list[Event]:
    """Historique d'activité d'un agent, du plus récent au plus ancien."""
    return list(
        db.execute(
            select(Event)
            .where(Event.agent_id == agent_id)
            .order_by(Event.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


# ========================================================== présence =======
def traiter_heartbeat(
    db: Session,
    agent: Agent,
    *,
    statut_declare: str = AgentStatus.ONLINE,
    runtime_status: str | None = None,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> Agent:
    """Enregistre un signal de présence.

    L'horodatage retenu est celui du serveur. Un retour d'état ``OFFLINE`` vers
    ``ONLINE`` est journalisé ; les heartbeats ordinaires ne le sont pas (sinon le
    journal serait saturé).
    """
    maintenant = utcnow()
    precedent = agent.status
    etat_cible = AgentStatus.ONLINE if statut_declare == AgentStatus.ONLINE else agent.status

    agent.last_seen_at = maintenant
    agent.status = etat_cible
    agent.source_ip = source_ip or agent.source_ip
    agent.updated_at = maintenant
    db.add(agent)
    db.commit()
    db.refresh(agent)

    if precedent == AgentStatus.OFFLINE:
        record_event(
            db,
            event_type=EventType.AGENT_RECONNECTED,
            message=(
                f"Agent « {agent.name} » de nouveau en ligne après "
                f"{humanize_duration(age_seconds(agent.updated_at, maintenant))}."
            ),
            severity=EventSeverity.INFO,
            actor_type=EventActorType.AGENT,
            actor_id=agent.id,
            agent_id=agent.id,
            source_ip=source_ip,
            request_id=request_id,
            object_type="agent",
            object_id=agent.id,
            success=True,
            metadata={"runtime_status": runtime_status},
        )
    elif precedent == AgentStatus.PENDING:
        record_event(
            db,
            event_type=EventType.AGENT_HEARTBEAT,
            message=f"Premier signal de présence de l'agent « {agent.name} » : connexion confirmée.",
            severity=EventSeverity.INFO,
            actor_type=EventActorType.AGENT,
            actor_id=agent.id,
            agent_id=agent.id,
            source_ip=source_ip,
            request_id=request_id,
            object_type="agent",
            object_id=agent.id,
            success=True,
            metadata={"runtime_status": runtime_status},
        )

    return agent


def agents_injoignables(db: Session, *, seuil_secondes: int, limite: int = 500) -> list[Agent]:
    """Agents actifs dont le dernier contact dépasse le seuil autorisé."""
    maintenant = utcnow()
    seuil = maintenant.timestamp() - seuil_secondes
    candidats = (
        db.execute(
            select(Agent).where(Agent.status.in_((AgentStatus.ONLINE, AgentStatus.PENDING)))
        )
        .scalars()
        .all()
    )
    resultat = []
    for agent in candidats:
        reference = agent.last_seen_at or agent.created_at
        if reference is None:
            continue
        if reference.timestamp() < seuil:
            resultat.append(agent)
        if len(resultat) >= limite:
            break
    return resultat


def marquer_hors_ligne(
    db: Session, agent: Agent, *, seuil_secondes: int, request_id: str | None = None
) -> Agent:
    """Passe un agent à l'état ``OFFLINE`` et journalise l'événement."""
    maintenant = utcnow()
    precedent = agent.status
    agent.status = AgentStatus.OFFLINE
    agent.updated_at = maintenant
    db.add(agent)
    db.commit()
    db.refresh(agent)

    anciennete = age_seconds(agent.last_seen_at or agent.created_at, maintenant)
    record_event(
        db,
        event_type=EventType.AGENT_WENT_OFFLINE,
        message=(
            f"Agent « {agent.name} » déclaré hors ligne : aucun signal de présence depuis "
            f"{humanize_duration(anciennete)} (seuil {seuil_secondes} s)."
        ),
        severity=EventSeverity.WARNING,
        actor_type=EventActorType.SYSTEM,
        actor_id=agent.id,
        agent_id=agent.id,
        object_type="agent",
        object_id=agent.id,
        success=True,
        request_id=request_id,
        metadata={"etat_precedent": precedent, "seuil_secondes": seuil_secondes},
    )
    return agent


# ======================================================= administration ====
def deconnecter_agent(
    db: Session,
    agent: Agent,
    *,
    motif: str = "déconnexion demandée par un administrateur",
    actor_id: str | None = None,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> Agent:
    """Déconnexion logique : la session est close, l'enregistrement est conservé.

    Le jeton individuel **n'est pas** révoqué (API.md §7.3) : l'agent pourra se
    reconnecter avec le même jeton.
    """
    if agent.status == AgentStatus.REVOKED:
        raise erreur_conflit(
            "AGENT_REVOKED", "Cet agent est révoqué : aucune session active à fermer."
        )
    if agent.status == AgentStatus.OFFLINE:
        return agent

    maintenant = utcnow()
    agent.status = AgentStatus.OFFLINE
    agent.disconnected_at = maintenant
    agent.updated_at = maintenant
    db.add(agent)
    db.commit()
    db.refresh(agent)

    record_event(
        db,
        event_type=EventType.AGENT_DISCONNECTED,
        message=f"Agent « {agent.name} » déconnecté logiquement : {motif}.",
        severity=EventSeverity.WARNING,
        actor_type=EventActorType.ADMIN if actor_id else EventActorType.SYSTEM,
        actor_id=actor_id,
        agent_id=agent.id,
        source_ip=source_ip,
        request_id=request_id,
        object_type="agent",
        object_id=agent.id,
        success=True,
        metadata={"motif": motif, "jeton_conserve": True},
    )
    return agent


def revoquer_agent(
    db: Session,
    agent: Agent,
    *,
    motif: str = "révocation demandée par un administrateur",
    actor_id: str | None = None,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> tuple[Agent, int, int]:
    """Révocation définitive : jetons invalidés et tâches en cours closés.

    Retourne ``(agent, jetons_revoques, taches_closes)``.
    """
    from app.services.task_service import clore_taches_agent_revoque
    from app.services.token_service import revoquer_jetons_agent

    maintenant = utcnow()
    jetons_revoques = revoquer_jetons_agent(db, agent.id, motif=motif)
    taches_closes = clore_taches_agent_revoque(db, agent, motif=motif)

    agent.status = AgentStatus.REVOKED
    agent.revoked_at = maintenant
    agent.revoked_reason = motif[:160]
    agent.updated_at = maintenant
    agent.credential_id = None
    db.add(agent)
    db.commit()
    db.refresh(agent)

    record_event(
        db,
        event_type=EventType.AGENT_REVOKED,
        message=(
            f"Agent « {agent.name} » révoqué : {jetons_revoques} jeton(s) invalidé(s), "
            f"{taches_closes} tâche(s) close(s)."
        ),
        severity=EventSeverity.ERROR,
        actor_type=EventActorType.ADMIN if actor_id else EventActorType.SYSTEM,
        actor_id=actor_id,
        agent_id=agent.id,
        source_ip=source_ip,
        request_id=request_id,
        object_type="agent",
        object_id=agent.id,
        success=True,
        metadata={"motif": motif, "jetons_revoques": jetons_revoques, "taches_closes": taches_closes},
    )
    return agent, jetons_revoques, taches_closes


def supprimer_agent(
    db: Session,
    agent: Agent,
    *,
    actor_id: str | None = None,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> None:
    """Suppression définitive d'un agent (API.md §7.5).

    La révocation est exigée au préalable : la suppression d'un agent encore actif
    laisserait des jetons valides orphelins. L'historique d'audit est conservé
    (les événements gardent leur trace, le lien vers l'agent devient nul).
    """
    if agent.status != AgentStatus.REVOKED:
        raise erreur_conflit(
            "AGENT_NOT_REVOKED",
            "La révocation de l'agent doit être effectuée avant toute suppression définitive.",
        )

    from app.services.token_service import revoquer_jetons_agent

    revoquer_jetons_agent(db, agent.id, motif="suppression de l'agent")
    nom = agent.name
    identifiant = agent.id
    db.delete(agent)
    db.commit()

    record_event(
        db,
        event_type=EventType.AGENT_DELETED,
        message=f"Agent « {nom} » supprimé définitivement (historique conservé).",
        severity=EventSeverity.WARNING,
        actor_type=EventActorType.ADMIN if actor_id else EventActorType.SYSTEM,
        actor_id=actor_id,
        source_ip=source_ip,
        request_id=request_id,
        object_type="agent",
        object_id=identifiant,
        success=True,
    )


def taches_agent(db: Session, agent_id: str, *, limite: int = 100) -> list[Task]:
    """Tâches attribuées à un agent, les plus récentes d'abord."""
    return list(
        db.execute(
            select(Task)
            .where(Task.assigned_agent_id == agent_id)
            .order_by(Task.created_at.desc())
            .limit(limite)
        )
        .scalars()
        .all()
    )
