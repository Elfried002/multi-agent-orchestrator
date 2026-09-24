"""Service d'enregistrement des agents (AGENT_CONNECTION.md §5, API.md §6.1).

Règles appliquées :

- **l'identifiant interne est attribué par le serveur** : un agent ne le choisit
  jamais (ARCHITECTURE.md §2.1) ;
- le **nom** et le **rôle** sont déterminés par le serveur ; un rôle hors liste est
  ramené au rôle par défaut, et un nom déjà pris reçoit un suffixe ;
- les **capacités déclarées** sont conservées telles quelles mais n'accordent aucun
  privilège (SECURITY.md §7) ;
- une **identité locale de connecteur** déjà enregistrée n'entraîne pas la création
  d'un doublon : la demande est refusée avec le rappel de l'identifiant existant, et
  le connecteur réutilise son jeton (AGENT_CONNECTION.md §14) ;
- après **révocation**, un nouvel enregistrement crée une identité neuve (§13).
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.event_service import generer_identifiant, record_event
from app.core.config import ROLE_PAR_DEFAUT, ROLES_AUTORISES
from app.core.errors import ErreurAPI, erreur_conflit
from app.core.state import ETAT_EN_LIGNE, get_desired_state
from app.models.agent import Agent, AgentStatus
from app.models.event import EventActorType, EventSeverity, EventType
from app.schemas.agent import EnrollRequest
from app.utils.datetime_utils import utcnow
from app.utils.validators import serialiser_json, validate_role

logger = logging.getLogger(__name__)

#: Taille maximale des métadonnées conservées pour un agent.
TAILLE_MAX_METADONNEES_AGENT = 4096


def nom_unique(db: Session, souhaite: str) -> str:
    """Retourne un nom d'agent libre, en suffixant si nécessaire."""
    base = souhaite.strip()[:80] or "Agent"
    existant = db.execute(select(Agent.name).where(Agent.name == base)).scalar_one_or_none()
    if existant is None:
        return base
    for index in range(2, 1000):
        candidat = f"{base} ({index})"
        if db.execute(select(Agent.name).where(Agent.name == candidat)).scalar_one_or_none() is None:
            return candidat
    # Repli improbable : suffixe par identifiant unique.
    return f"{base} ({generer_identifiant('n')[2:]})"


def agent_existant_pour_instance(
    db: Session, runtime: str, client_instance_id: str
) -> Agent | None:
    """Agent non révoqué déjà enregistré pour cette identité locale."""
    return db.execute(
        select(Agent).where(
            Agent.runtime == runtime,
            Agent.client_instance_id == client_instance_id,
            Agent.status != AgentStatus.REVOKED,
        )
    ).scalar_one_or_none()


def enregistrer_agent(
    db: Session,
    *,
    demande: EnrollRequest,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> tuple[Agent, str]:
    """Enregistre un agent et lui attribue une identité et un jeton individuel.

    Retourne ``(agent, jeton_en_clair)``. Le jeton n'est jamais journalisé et n'est
    plus jamais accessible ensuite.
    """
    from app.services.token_service import creer_jeton_agent

    # Garde-fou : même si la dépendance de route a déjà vérifié l'état, le service
    # reste sûr utilisé seul (tests, scripts, appels internes).
    if get_desired_state(db) != ETAT_EN_LIGNE:
        record_event(
            db,
            event_type=EventType.AGENT_ENROLLMENT_REFUSED,
            message="Enregistrement refusé : orchestrateur logiquement hors ligne.",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.ANONYMOUS,
            source_ip=source_ip,
            request_id=request_id,
            object_type="agent",
            success=False,
            metadata={"runtime": demande.runtime, "instance": demande.client_instance_id},
        )
        raise ErreurAPI(
            "ORCHESTRATOR_OFFLINE",
            "L'orchestrateur est hors ligne : les enregistrements sont refusés.",
            status_code=503,
        )

    existant = agent_existant_pour_instance(db, demande.runtime, demande.client_instance_id)
    if existant is not None:
        record_event(
            db,
            event_type=EventType.AGENT_ENROLLMENT_REFUSED,
            message=(
                "Demande d'enregistrement refusée : identité locale déjà enregistrée "
                f"(agent {existant.id})."
            ),
            severity=EventSeverity.INFO,
            actor_type=EventActorType.ANONYMOUS,
            source_ip=source_ip,
            request_id=request_id,
            agent_id=existant.id,
            object_type="agent",
            object_id=existant.id,
            success=False,
            metadata={"runtime": demande.runtime, "instance": demande.client_instance_id},
        )
        raise erreur_conflit(
            "AGENT_ALREADY_ENROLLED",
            "Cette identité de connecteur est déjà enregistrée sous "
            f"l'identifiant {existant.id} : réutilisez votre jeton existant au lieu de "
            "vous réenregistrer. Un nouvel enregistrement n'est possible qu'après révocation.",
        )

    maintenant = utcnow()
    identifiant = f"agt_{generer_identifiant('a')[2:]}"
    role = validate_role(demande.declared_role, ROLES_AUTORISES, ROLE_PAR_DEFAUT)

    metadonnees = serialiser_json(
        {
            "nom_demande": demande.requested_name,
            "role_declare": demande.declared_role,
            "version": demande.version,
        },
        max_bytes=TAILLE_MAX_METADONNEES_AGENT,
    )

    agent = Agent(
        id=identifiant,
        name=nom_unique(db, demande.requested_name),
        role=role,
        runtime=demande.runtime,
        capabilities=serialiser_json(demande.capabilities or []),
        status=AgentStatus.PENDING,
        source_ip=source_ip,
        client_instance_id=demande.client_instance_id,
        version=demande.version,
        created_at=maintenant,
        metadata_json=metadonnees,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    jeton, valeur_jeton = creer_jeton_agent(db, agent.id, label=f"jeton de {agent.name}")

    agent.credential_id = jeton.id
    db.add(agent)
    db.commit()
    db.refresh(agent)

    record_event(
        db,
        event_type=EventType.AGENT_ENROLLED,
        message=f"Agent « {agent.name} » enregistré (rôle {agent.role}).",
        severity=EventSeverity.INFO,
        actor_type=EventActorType.AGENT,
        actor_id=agent.id,
        agent_id=agent.id,
        source_ip=source_ip,
        request_id=request_id,
        object_type="agent",
        object_id=agent.id,
        success=True,
        # Le jeton n'apparaît jamais ici : seule la nature de la demande est tracée.
        metadata={
            "runtime": agent.runtime,
            "role": agent.role,
            "capacites": demande.capabilities or [],
            "instance": demande.client_instance_id,
        },
    )
    return agent, valeur_jeton


def statistiques_agents(db: Session) -> dict[str, int]:
    """Compteurs d'agents utilisés par le tableau de bord et ``/health``."""
    total = int(db.execute(select(func.count()).select_from(Agent)).scalar_one())
    par_etat = {
        etat: int(
            db.execute(
                select(func.count()).select_from(Agent).where(Agent.status == etat)
            ).scalar_one()
        )
        for etat in AgentStatus.TOUS
    }
    return {
        "total": total,
        "en_ligne": par_etat[AgentStatus.ONLINE],
        "hors_ligne": par_etat[AgentStatus.OFFLINE],
        "en_attente": par_etat[AgentStatus.PENDING],
        "revoques": par_etat[AgentStatus.REVOKED],
    }
