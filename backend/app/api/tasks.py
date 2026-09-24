"""Routes des tâches côté administrateur (API.md §8).

Toutes les routes exigent une session administrateur ; les opérations modifiantes
exigent en plus un jeton anti-CSRF.
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
)
from app.database.connection import get_db
from app.schemas.common import ListePage
from app.schemas.task import (
    TaskCancelRequest,
    TaskCreateRequest,
    TaskDetailOut,
    TaskOut,
    TaskStateInfo,
)
from app.services import task_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["Tâches"])


@router.post(
    "",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="Création d'une tâche",
    description=(
        "Crée une tâche. Si un agent est fourni, la tâche est immédiatement "
        "attribuée (état ASSIGNED) ; sinon elle reste en attente (PENDING)."
    ),
    responses={404: {"description": "Agent introuvable"}, 409: {"description": "Agent révoqué"}},
)
def creer(
    request: Request,
    demande: TaskCreateRequest,
    admin: ContexteAdmin = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> TaskOut:
    """Crée une tâche."""
    tache = task_service.creer_tache(
        db,
        titre=demande.title,
        description=demande.description,
        priorite=demande.priority,
        agent_id=demande.assigned_agent_id,
        admin_id=admin.admin_id,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )
    return TaskOut.model_validate(tache)


@router.get(
    "",
    response_model=ListePage[TaskOut],
    summary="Liste des tâches",
)
def lister(
    db: Session = Depends(get_db),
    _admin: ContexteAdmin = Depends(require_admin),
    statut: str | None = Query(None, alias="status"),
    agent_id: str | None = Query(None),
    priority: str | None = Query(None),
    search: str | None = Query(None, description="Recherche sur le titre et la description"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> ListePage[TaskOut]:
    """Liste paginée et filtrable des tâches."""
    taches, total = task_service.lister_taches(
        db,
        status=statut,
        agent_id=agent_id,
        priority=priority,
        search=search,
        page=page,
        page_size=page_size,
    )
    return ListePage[TaskOut](
        items=[TaskOut.model_validate(tache) for tache in taches],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/state-machine",
    response_model=TaskStateInfo,
    summary="Machine à états des tâches",
    description=(
        "Expose les états et transitions autorisés, tels qu'appliqués par le "
        "backend : le tableau de bord n'a pas à dupliquer cette logique."
    ),
)
def machine_a_etats(_admin: ContexteAdmin = Depends(require_admin)) -> TaskStateInfo:
    """Retourne les transitions autorisées."""
    return TaskStateInfo.depuis_modele()


@router.get(
    "/{task_id}",
    response_model=TaskDetailOut,
    summary="Détail d'une tâche",
    responses={404: {"description": "Tâche introuvable"}},
)
def detail(
    task_id: str,
    db: Session = Depends(get_db),
    _admin: ContexteAdmin = Depends(require_admin),
) -> TaskDetailOut:
    """Détail d'une tâche : état, résultat conservé, agent assigné."""
    tache = task_service.obtenir_tache(db, task_id)
    sortie = TaskDetailOut.model_validate(tache)
    sortie.result = task_service.resultat_tache(tache)
    sortie.peut_etre_annulee = task_service.peut_etre_annulee(tache)
    if tache.assigned_agent_id:
        from app.models.agent import Agent

        agent = db.get(Agent, tache.assigned_agent_id)
        sortie.assigned_agent_name = agent.name if agent else None
    return sortie


@router.post(
    "/{task_id}/cancel",
    response_model=TaskOut,
    summary="Annulation d'une tâche",
    description="Refusée si la tâche est déjà dans un état terminal.",
    responses={409: {"description": "Tâche déjà terminale"}},
)
def annuler(
    request: Request,
    task_id: str,
    demande: TaskCancelRequest | None = None,
    admin: ContexteAdmin = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> TaskOut:
    """Annule une tâche non terminale."""
    tache = task_service.obtenir_tache(db, task_id)
    tache = task_service.annuler_tache(
        db,
        tache,
        motif=demande.reason if demande else None,
        actor_id=admin.admin_id,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )
    return TaskOut.model_validate(tache)
