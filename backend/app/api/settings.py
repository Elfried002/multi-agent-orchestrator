"""Routes de gestion de l'orchestrateur (API.md §9).

Distinction maintenue à chaque réponse : ``desired_state`` (état logique persistant),
``service_status`` (processus systemd), ``health_status`` (santé du backend).

Note d'exploitation : les paramètres d'exécution sont **lus** depuis la
configuration d'environnement (``/etc/multi-agent-orchestrator/production.env``).
L'API les expose en lecture seule ; leur modification passe par le fichier de
configuration puis un redémarrage du service, conformément au processus
d'installation (INSTALLATION.md §7). Ce choix évite un réglage d'API non persistant
qui donnerait l'illusion d'une configuration durable.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.authentication import (
    ContexteAdmin,
    client_ip,
    require_admin,
    require_admin_csrf,
)
from app.core.config import settings
from app.core.rate_limit import limites_actives
from app.database.connection import get_db
from app.monitoring import alert_service
from app.schemas.settings import (
    EnrollmentKeyOut,
    OrchestratorStateOut,
    RotateEnrollmentKeyResponse,
    ServiceInfoOut,
    SettingsOut,
    StateChangeRequest,
)
from app.services import orchestrator_service, token_service
from app.utils.datetime_utils import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/settings", tags=["Orchestrateur"])


@router.get(
    "/orchestrator",
    response_model=OrchestratorStateOut,
    summary="État de l'orchestrateur",
)
def etat(
    db: Session = Depends(get_db),
    _admin: ContexteAdmin = Depends(require_admin),
) -> OrchestratorStateOut:
    """Retourne l'état logique, l'état du service et l'état de santé."""
    return OrchestratorStateOut.model_validate(orchestrator_service.etat_courant(db))


@router.post(
    "/orchestrator/state",
    response_model=OrchestratorStateOut,
    summary="Changement d'état ONLINE / OFFLINE",
    description=(
        "Le changement est persisté en base et journalisé. Le processus systemd "
        "n'est jamais arrêté : le tableau de bord reste accessible."
    ),
)
def changer_etat(
    request: Request,
    demande: StateChangeRequest,
    admin: ContexteAdmin = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> OrchestratorStateOut:
    """Change l'état logique de l'orchestrateur."""
    orchestrator_service.changer_etat(
        db,
        etat=demande.desired_state,
        admin_id=admin.admin_id,
        motif=demande.reason,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )
    return OrchestratorStateOut.model_validate(orchestrator_service.etat_courant(db))


@router.get(
    "/enrollment-key",
    response_model=EnrollmentKeyOut,
    summary="Consultation de la clé d'enregistrement",
    description=(
        "Réservé à l'administrateur authentifié. La consultation est journalisée ; "
        "la valeur de la clé n'est jamais écrite dans les journaux."
    ),
)
def cle_enregistrement(
    request: Request,
    admin: ContexteAdmin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> EnrollmentKeyOut:
    """Retourne la clé d'enregistrement active."""
    valeur, cle = token_service.cle_pour_affichage(
        db,
        actor_id=admin.admin_id,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )
    return EnrollmentKeyOut(
        enrollment_key=valeur,
        created_at=cle.created_at,
        rotated_at=None,
    )


@router.post(
    "/enrollment-key/rotate",
    response_model=RotateEnrollmentKeyResponse,
    summary="Rotation de la clé d'enregistrement",
    description=(
        "L'ancienne clé est révoquée immédiatement : les agents qui ne disposent que "
        "d'elle ne peuvent plus s'enregistrer. Les agents déjà enregistrés conservent "
        "leur jeton individuel."
    ),
)
def rotation_cle(
    request: Request,
    admin: ContexteAdmin = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> RotateEnrollmentKeyResponse:
    """Révoque la clé active et en génère une nouvelle."""
    cle, valeur = token_service.rotation_cle_enregistrement(
        db,
        actor_id=admin.admin_id,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )
    from app.services.enrollment_service import statistiques_agents

    return RotateEnrollmentKeyResponse(
        enrollment_key=valeur,
        rotated_at=cle.created_at or utcnow(),
        agents_impactes=statistiques_agents(db)["total"],
        message=(
            "Nouvelle clé générée. L'ancienne est révoquée : les agents déjà enregistrés "
            "ne sont pas affectés, seuls les nouveaux enregistrements nécessitent la "
            "nouvelle clé."
        ),
    )


@router.get(
    "/service",
    response_model=ServiceInfoOut,
    summary="Informations de service",
    description="Diagnostic réservé à l'administrateur : version, santé, compteurs.",
)
def informations_service(
    db: Session = Depends(get_db),
    _admin: ContexteAdmin = Depends(require_admin),
) -> ServiceInfoOut:
    """Retourne les informations de service et les compteurs."""
    return ServiceInfoOut.model_validate(orchestrator_service.informations_service(db))


@router.get(
    "/security",
    summary="Synthèse de sécurité et alertes",
    description=(
        "Synthèse dérivée du journal applicatif : échecs d'authentification, "
        "tentatives avec jeton révoqué, rejets CSRF, agents hors ligne, tâches en "
        "échec. Portée applicative — ceci n'est ni un IDS ni une capture réseau."
    ),
)
def synthese_securite(
    db: Session = Depends(get_db),
    _admin: ContexteAdmin = Depends(require_admin),
    fenetre_heures: int = 24,
) -> dict:
    """Retourne la synthèse de sécurité."""
    from fastapi import Query

    return alert_service.resume_securite(db, fenetre_heures=max(1, min(fenetre_heures, 720)))


@router.get(
    "/parameters",
    response_model=SettingsOut,
    summary="Paramètres d'exécution (lecture seule)",
    description=(
        "Paramètres effectifs du service. Modifiables uniquement dans le fichier de "
        "configuration de production, puis appliqués par un redémarrage du service."
    ),
)
def parametres(_admin: ContexteAdmin = Depends(require_admin)) -> SettingsOut:
    """Retourne les paramètres d'exécution effectifs."""
    limites = limites_actives()
    return SettingsOut(
        environment=settings.environment,
        version=settings.version,
        agent_offline_threshold_seconds=settings.agent_offline_threshold_seconds,
        monitor_interval_seconds=settings.monitor_interval_seconds,
        task_default_timeout_seconds=settings.task_default_timeout_seconds,
        admin_session_ttl_hours=settings.admin_session_ttl_hours,
        admin_session_idle_minutes=settings.admin_session_idle_minutes,
        max_request_bytes=settings.max_request_bytes,
        max_result_bytes=settings.max_result_bytes,
        login_rate_limit=limites["login"],
        enroll_rate_limit=limites["enroll"],
        agent_rate_limit=limites["agent"],
        admin_rate_limit=limites["admin"],
        enable_docs=settings.enable_docs,
        trusted_proxies=settings.trusted_proxies,
        allowed_origins=settings.allowed_origins,
    )
