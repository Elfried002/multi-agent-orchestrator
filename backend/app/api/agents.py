"""Routes des agents (API.md §6 et §7).

Le fichier regroupe deux familles de routes volontairement distinctes :

- **routes agent** (authentification par jeton individuel) : enregistrement,
  identité courante, heartbeat, récupération et traitement des tâches ;
- **routes administrateur** (session + jeton anti-CSRF) : liste, détail,
  déconnexion logique, révocation, suppression.

Ordre de déclaration : les routes littérales (``/me``, ``/tasks``, ``/heartbeat``,
``/enroll``) sont déclarées **avant** ``/{agent_id}`` afin qu'elles ne soient pas
capturées par le paramètre de chemin.

Aucune route d'administration n'est accessible à un agent et aucune route agent ne
permet d'atteindre les données d'un autre agent (API.md §11).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.authentication import (
    ContexteAdmin,
    client_ip,
    require_admin,
    require_admin_csrf,
    require_agent,
    require_enrollment_key,
    require_orchestrator_online,
)
from app.core.config import settings
from app.core.rate_limit import limiter
from app.core.state import get_desired_state
from app.database.connection import get_db
from app.models.agent import Agent, AgentStatus
from app.models.event import EventActorType, EventSeverity, EventType
from app.schemas.agent import (
    AckResponse,
    AgentActionResult,
    AgentDetailOut,
    AgentIdentityOut,
    AgentOut,
    AgentTaskOut,
    EnrollRequest,
    EnrollResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    TaskResultRequest,
    TaskStatusUpdateRequest,
)
from app.schemas.common import ListePage
from app.services import agent_service, enrollment_service, task_service
from app.utils.datetime_utils import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["Agents"])


# ================================================= routes agent (jeton) =====
@router.post(
    "/enroll",
    response_model=EnrollResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enregistrement d'un agent",
    description=(
        "Authentification par clé d'enregistrement. Le serveur attribue lui-même "
        "l'identifiant interne, le nom et le rôle, puis délivre un jeton individuel. "
        "Refusé si l'orchestrateur est logiquement hors ligne."
    ),
    responses={
        201: {"description": "Agent enregistré"},
        401: {"description": "Clé d'enregistrement invalide"},
        409: {"description": "Identité de connecteur déjà enregistrée"},
        503: {"description": "Orchestrateur logiquement hors ligne"},
    },
)
@limiter.limit(lambda: settings.enroll_rate_limit)
def enregistrer(
    request: Request,
    demande: EnrollRequest,
    db: Session = Depends(get_db),
    _cle=Depends(require_enrollment_key),
    _en_ligne=Depends(require_orchestrator_online),
) -> EnrollResponse:
    """Enregistre un agent et retourne son identité et son jeton individuel."""
    agent, jeton = enrollment_service.enregistrer_agent(
        db,
        demande=demande,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )
    return EnrollResponse(
        agent_id=agent.id,
        name=agent.name,
        role=agent.role,
        status=agent.status,
        access_token=jeton,
        token_type="Bearer",
        created_at=agent.created_at,
    )


@router.get(
    "/me",
    response_model=AgentIdentityOut,
    summary="Identité de l'agent courant",
    responses={401: {"description": "Jeton absent, invalide ou révoqué"}},
)
def identite_courante(
    request: Request,
    agent: Agent = Depends(require_agent),
) -> AgentIdentityOut:
    """Retourne l'identité attribuée par le serveur."""
    return AgentIdentityOut(
        agent_id=agent.id,
        name=agent.name,
        role=agent.role,
        runtime=agent.runtime,
        status=agent.status,
        capabilities=agent_service.capacites_agent(agent),
        instance_id=agent.client_instance_id,
        registered_at=agent.created_at,
        last_seen_at=agent.last_seen_at,
    )


@router.post(
    "/heartbeat",
    response_model=HeartbeatResponse,
    summary="Signal de présence",
    description=(
        "Le serveur utilise son propre horodatage pour déterminer le dernier contact "
        "fiable. Un heartbeat est accepté même lorsque l'orchestrateur est hors ligne : "
        "la réponse indique alors l'état logique, ce qui permet au connecteur de "
        "distinguer une indisponibilité réseau d'un état OFFLINE."
    ),
    responses={401: {"description": "Jeton absent, invalide ou révoqué"}},
)
def heartbeat(
    request: Request,
    donnees: HeartbeatRequest,
    agent: Agent = Depends(require_agent),
    db: Session = Depends(get_db),
) -> HeartbeatResponse:
    """Enregistre un signal de présence horodaté par le serveur."""
    agent = agent_service.traiter_heartbeat(
        db,
        agent,
        statut_declare=donnees.status,
        runtime_status=donnees.runtime_status,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )
    return HeartbeatResponse(
        accepted=True,
        agent_status=agent.status,
        orchestrator_status=get_desired_state(db),
        server_time=utcnow(),
    )


@router.get(
    "/tasks",
    response_model=list[AgentTaskOut],
    summary="Tâches attribuées à l'agent",
    description=(
        "Retourne les tâches attribuées à l'agent et prêtes à être traitées : "
        "celles à prendre en charge (ASSIGNED) et celles à reprendre après une "
        "interruption (RUNNING)."
    ),
)
def taches_disponibles(
    agent: Agent = Depends(require_agent),
    db: Session = Depends(get_db),
    _en_ligne=Depends(require_orchestrator_online),
) -> list[AgentTaskOut]:
    """Liste les tâches de l'agent authentifié."""
    taches = task_service.taches_pour_agent(db, agent)
    return [AgentTaskOut.model_validate(tache) for tache in taches]


@router.post(
    "/tasks/{task_id}/ack",
    response_model=AckResponse,
    summary="Confirmation de prise en charge",
    description=(
        "Confirme la prise en charge d'une tâche. Un acquittement répété est "
        "idempotent : il ne redéclenche pas d'exécution."
    ),
    responses={
        403: {"description": "Tâche attribuée à un autre agent"},
        404: {"description": "Tâche introuvable"},
        409: {"description": "Tâche déjà terminée"},
    },
)
def acquitter(
    request: Request,
    task_id: str,
    agent: Agent = Depends(require_agent),
    db: Session = Depends(get_db),
    _en_ligne=Depends(require_orchestrator_online),
) -> AckResponse:
    """Acquitte une tâche attribuée à l'agent."""
    tache = task_service.obtenir_tache(db, task_id)
    tache, deja = task_service.acquitter_tache(
        db, tache, agent, request_id=getattr(request.state, "request_id", None)
    )
    return AckResponse(
        task_id=tache.id,
        status=tache.status,
        acked_at=tache.acked_at,
        deja_prise_en_charge=deja,
        message=(
            "Tâche déjà prise en charge : aucun traitement supplémentaire n'est requis."
            if deja
            else "Prise en charge confirmée."
        ),
    )


@router.post(
    "/tasks/{task_id}/status",
    response_model=AgentTaskOut,
    summary="Mise à jour d'une tâche",
    responses={
        403: {"description": "Tâche attribuée à un autre agent"},
        409: {"description": "Transition d'état refusée"},
    },
)
def maj_statut(
    request: Request,
    task_id: str,
    donnees: TaskStatusUpdateRequest,
    agent: Agent = Depends(require_agent),
    db: Session = Depends(get_db),
    _en_ligne=Depends(require_orchestrator_online),
) -> AgentTaskOut:
    """Déclare le démarrage ou l'échec d'une tâche."""
    tache = task_service.obtenir_tache(db, task_id)
    tache = task_service.maj_statut_tache(
        db,
        tache,
        agent,
        nouveau_statut=donnees.status,
        message=donnees.message,
        request_id=getattr(request.state, "request_id", None),
    )
    return AgentTaskOut.model_validate(tache)


@router.post(
    "/tasks/{task_id}/result",
    response_model=AgentTaskOut,
    summary="Transmission du résultat",
    description=(
        "Transmet le résultat d'une tâche. Le contenu est expurgé des secrets, sa "
        "taille est bornée et il est traité comme une donnée non fiable. Une "
        "retransmission après clôture est ignorée sans écraser le résultat enregistré."
    ),
    responses={
        403: {"description": "Tâche attribuée à un autre agent"},
        422: {"description": "Résultat invalide ou trop volumineux"},
    },
)
def transmettre_resultat(
    request: Request,
    task_id: str,
    donnees: TaskResultRequest,
    agent: Agent = Depends(require_agent),
    db: Session = Depends(get_db),
    _en_ligne=Depends(require_orchestrator_online),
) -> AgentTaskOut:
    """Enregistre le résultat d'une tâche."""
    tache = task_service.obtenir_tache(db, task_id)
    tache, _deja = task_service.soumettre_resultat(
        db,
        tache,
        agent,
        statut=donnees.status,
        resultat=donnees.result,
        message_erreur=donnees.error_message,
        request_id=getattr(request.state, "request_id", None),
    )
    return AgentTaskOut.model_validate(tache)


# ============================================ routes administrateur ========
@router.get(
    "",
    response_model=ListePage[AgentOut],
    summary="Liste des agents",
    description="Réservé à l'administrateur. Aucun secret n'y figure.",
)
@limiter.limit(lambda: settings.admin_rate_limit)
def lister(
    request: Request,
    db: Session = Depends(get_db),
    _admin: ContexteAdmin = Depends(require_admin),
    statut: str | None = Query(None, alias="status", description="PENDING, ONLINE, OFFLINE, REVOKED"),
    role: str | None = Query(None),
    search: str | None = Query(None, description="Recherche sur le nom, l'identifiant, le runtime, l'IP"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> ListePage[AgentOut]:
    """Liste paginée et filtrable des agents."""
    agents, total = agent_service.lister_agents(
        db, status=statut, role=role, search=search, page=page, page_size=page_size
    )
    return ListePage[AgentOut](
        items=[AgentOut.model_validate(agent) for agent in agents],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{agent_id}",
    response_model=AgentDetailOut,
    summary="Détail d'un agent",
    responses={404: {"description": "Agent introuvable"}},
)
def detail(
    agent_id: str,
    db: Session = Depends(get_db),
    _admin: ContexteAdmin = Depends(require_admin),
) -> AgentDetailOut:
    """Détail d'un agent : identité, capacités déclarées, métadonnées assainies."""
    agent = agent_service.obtenir_agent(db, agent_id)
    detail_out = AgentDetailOut.model_validate(agent)
    detail_out.metadata = agent_service.metadonnees_agent(agent)
    detail_out.capabilities = agent_service.capacites_agent(agent)
    return detail_out


@router.post(
    "/{agent_id}/disconnect",
    response_model=AgentActionResult,
    summary="Déconnexion logique d'un agent",
    description=(
        "Ferme la session de l'agent sans révoquer son jeton : il pourra se "
        "reconnecter avec le même jeton (API.md §7.3)."
    ),
)
def deconnecter(
    request: Request,
    agent_id: str,
    admin: ContexteAdmin = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> AgentActionResult:
    """Déconnecte logiquement un agent."""
    agent = agent_service.obtenir_agent(db, agent_id)
    agent = agent_service.deconnecter_agent(
        db,
        agent,
        actor_id=admin.admin_id,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )
    return AgentActionResult(
        agent_id=agent.id,
        status=agent.status,
        message="Agent déconnecté logiquement ; son jeton reste valide.",
    )


@router.post(
    "/{agent_id}/revoke",
    response_model=AgentActionResult,
    summary="Révocation définitive d'un agent",
    description=(
        "Invalide immédiatement les jetons de l'agent et clôt ses tâches en cours. "
        "Un nouvel enregistrement est nécessaire pour rétablir l'accès (API.md §7.4)."
    ),
)
def revoquer(
    request: Request,
    agent_id: str,
    admin: ContexteAdmin = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> AgentActionResult:
    """Révoque définitivement un agent."""
    agent = agent_service.obtenir_agent(db, agent_id)
    agent, jetons, taches = agent_service.revoquer_agent(
        db,
        agent,
        actor_id=admin.admin_id,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )
    return AgentActionResult(
        agent_id=agent.id,
        status=agent.status,
        message=(
            f"Agent révoqué : {jetons} jeton(s) invalidé(s), {taches} tâche(s) close(s). "
            "Un nouvel enregistrement est requis pour rétablir l'accès."
        ),
    )


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Suppression d'un agent",
    description=(
        "Suppression définitive, autorisée uniquement après révocation. "
        "L'historique d'audit est conservé (API.md §7.5)."
    ),
    responses={409: {"description": "Agent non révoqué"}},
)
def supprimer(
    request: Request,
    agent_id: str,
    admin: ContexteAdmin = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
):
    """Supprime un agent révoqué."""
    agent = agent_service.obtenir_agent(db, agent_id)
    agent_service.supprimer_agent(
        db,
        agent,
        actor_id=admin.admin_id,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )
    return None
