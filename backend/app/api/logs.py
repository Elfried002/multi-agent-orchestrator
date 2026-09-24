"""Routes des journaux d'audit (API.md §10).

Réservées à l'administrateur. Les charges utiles retournées sont expurgées des
secrets : aucune valeur de mot de passe, de jeton ou de clé n'a été persistée.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.authentication import ContexteAdmin, require_admin
from app.core.errors import erreur_introuvable
from app.database.connection import get_db
from app.models.event import EventSeverity
from app.schemas.common import ListePage
from app.schemas.logs import EventDetailOut, EventOut, JournalReference
from app.services import agent_service
from app.audit import event_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/logs", tags=["Journaux et événements"])


@router.get(
    "",
    response_model=ListePage[EventOut],
    summary="Liste des événements",
    description=(
        "Filtres : gravité, type d'événement, agent, plage de dates, recherche "
        "textuelle. Les dates sont au format ISO 8601 (UTC)."
    ),
)
def lister(
    db: Session = Depends(get_db),
    _admin: ContexteAdmin = Depends(require_admin),
    severity: str | None = Query(None, description="DEBUG, INFO, WARNING, ERROR, CRITICAL"),
    event_type: str | None = Query(None),
    agent_id: str | None = Query(None),
    actor_type: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> ListePage[EventOut]:
    """Liste paginée et filtrable des événements d'audit."""
    if severity and severity not in EventSeverity.TOUS:
        # Une gravité inconnue ne doit pas être silencieusement ignorée : elle
        # renverrait un résultat trompeur.
        from app.core.errors import erreur_invalide

        raise erreur_invalide(
            "INVALID_SEVERITY",
            "Gravité inconnue. Valeurs acceptées : " + ", ".join(EventSeverity.TOUS) + ".",
        )

    evenements, total = event_service.list_events(
        db,
        severity=severity,
        event_type=event_type,
        agent_id=agent_id,
        actor_type=actor_type,
        start_date=start_date,
        end_date=end_date,
        search=search,
        page=page,
        page_size=page_size,
    )
    return ListePage[EventOut](
        items=[EventOut.model_validate(evenement) for evenement in evenements],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/reference",
    response_model=JournalReference,
    summary="Valeurs de référence des filtres",
    description=(
        "Liste les gravités, types d'événements et types d'acteurs disponibles : "
        "le tableau de bord construit ses filtres à partir de ces valeurs plutôt que "
        "de les dupliquer."
    ),
)
def reference(_admin: ContexteAdmin = Depends(require_admin)) -> JournalReference:
    """Retourne les valeurs de référence des filtres."""
    return JournalReference()


@router.get(
    "/{event_id}",
    response_model=EventDetailOut,
    summary="Détail d'un événement",
    responses={404: {"description": "Événement introuvable"}},
)
def detail(
    event_id: str,
    db: Session = Depends(get_db),
    _admin: ContexteAdmin = Depends(require_admin),
) -> EventDetailOut:
    """Retourne un événement et ses métadonnées expurgées."""
    evenement = event_service.get_event(db, event_id)
    if evenement is None:
        raise erreur_introuvable(f"Événement introuvable : {event_id}")
    return EventDetailOut.model_validate(evenement)


@router.get(
    "/agent/{agent_id}/historique",
    response_model=ListePage[EventOut],
    summary="Historique d'activité d'un agent",
    description="Historique complet des événements liés à un agent (consultation depuis la page Agents).",
)
def historique_agent(
    agent_id: str,
    db: Session = Depends(get_db),
    _admin: ContexteAdmin = Depends(require_admin),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> ListePage[EventOut]:
    """Retourne l'historique d'activité d'un agent."""
    # Vérifie l'existence de l'agent : une erreur 404 explicite vaut mieux qu'une
    # liste vide ambiguë.
    agent_service.obtenir_agent(db, agent_id)
    evenements, total = event_service.list_events(
        db, agent_id=agent_id, page=page, page_size=page_size
    )
    return ListePage[EventOut](
        items=[EventOut.model_validate(evenement) for evenement in evenements],
        total=total,
        page=page,
        page_size=page_size,
    )
