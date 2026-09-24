"""Service d'événements : enregistrement et consultation du journal d'audit.

Chaque événement porte l'acteur, l'adresse IP observée, la date, le type et
l'objet concerné (SECURITY.md §10). Les métadonnées sont expurgées des secrets
avant persistance.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit.audit_logger import audit_logger
from app.models.event import Event, EventActorType, EventSeverity
from app.utils.datetime_utils import age_seconds, humanize_duration, iso_now, parse_iso, utcnow
from app.utils.validators import (
    ErreurValidation,
    serialiser_json,
    strip_sensitive_keys,
)

logger = logging.getLogger(__name__)

#: Taille maximale des métadonnées persistées avec un événement.
TAILLE_MAX_METADONNEES = 8192
#: Longueur maximale du message.
LONGUEUR_MAX_MESSAGE = 500


def generer_identifiant(prefixe: str) -> str:
    """Identifiant unique lisible, préfixé par la nature de l'objet."""
    return f"{prefixe}_{uuid4().hex[:16]}"


def record_event(
    db: Session,
    *,
    event_type: str,
    message: str,
    severity: str = EventSeverity.INFO,
    actor_type: str = EventActorType.SYSTEM,
    actor_id: str | None = None,
    agent_id: str | None = None,
    source_ip: str | None = None,
    request_id: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    success: bool | None = None,
    metadata: dict[str, Any] | None = None,
    commit: bool = True,
) -> Event:
    """Enregistre un événement d'audit.

    Le message est tronqué à ``LONGUEUR_MAX_MESSAGE`` et les métadonnées expurgées :
    aucun secret ne peut atteindre la base par ce chemin.
    """
    if severity not in EventSeverity.TOUS:
        severity = EventSeverity.INFO
    if actor_type not in EventActorType.TOUS:
        actor_type = EventActorType.SYSTEM

    texte = (message or "").strip().replace("\n", " ")
    if len(texte) > LONGUEUR_MAX_MESSAGE:
        texte = texte[: LONGUEUR_MAX_MESSAGE - 1] + "…"

    metadonnees: str | None = None
    if metadata:
        propre = strip_sensitive_keys(metadata)
        try:
            metadonnees = serialiser_json(propre, max_bytes=TAILLE_MAX_METADONNEES)
        except ErreurValidation:
            metadonnees = serialiser_json({"note": "métadonnées trop volumineuses, non conservées"})

    evenement = Event(
        id=generer_identifiant("evt"),
        event_type=event_type,
        severity=severity,
        message=texte,
        agent_id=agent_id,
        actor_type=actor_type,
        actor_id=actor_id,
        source_ip=source_ip,
        request_id=request_id,
        object_type=object_type,
        object_id=object_id,
        success=success,
        created_at=utcnow(),
        metadata_json=metadonnees,
    )
    db.add(evenement)

    # Trace immédiate dans le journal d'audit : la valeur des secrets n'y figure pas.
    audit_logger().info(
        "evenement type=%s gravite=%s acteur=%s/%s agent=%s objet=%s/%s succes=%s ip=%s message=%s",
        event_type,
        severity,
        actor_type,
        actor_id or "-",
        agent_id or "-",
        object_type or "-",
        object_id or "-",
        success,
        source_ip or "-",
        texte,
        extra={"request_id": request_id or "-"},
    )

    if commit:
        db.commit()
    return evenement


def get_event(db: Session, event_id: str) -> Event | None:
    """Retourne un événement par son identifiant."""
    return db.get(Event, event_id)


def list_events(
    db: Session,
    *,
    severity: str | None = None,
    severities: list[str] | None = None,
    event_type: str | None = None,
    event_types: list[str] | None = None,
    agent_id: str | None = None,
    actor_type: str | None = None,
    start_date: datetime | str | None = None,
    end_date: datetime | str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Event], int]:
    """Liste filtrée et paginée des événements (API.md §10.1)."""
    page = max(1, page)
    page_size = max(1, min(page_size, 200))

    conditions = []
    if severity:
        conditions.append(Event.severity == severity)
    if severities:
        conditions.append(Event.severity.in_(severities))
    if event_type:
        conditions.append(Event.event_type == event_type)
    if event_types:
        conditions.append(Event.event_type.in_(event_types))
    if agent_id:
        conditions.append(Event.agent_id == agent_id)
    if actor_type:
        conditions.append(Event.actor_type == actor_type)

    debut = parse_iso(start_date) if isinstance(start_date, str) else start_date
    fin = parse_iso(end_date) if isinstance(end_date, str) else end_date
    if debut is not None:
        conditions.append(Event.created_at >= debut)
    if fin is not None:
        conditions.append(Event.created_at <= fin)
    if search:
        motif = f"%{search.strip()}%"
        conditions.append(or_(Event.message.ilike(motif), Event.event_type.ilike(motif)))

    requete = select(Event)
    requete_compte = select(func.count()).select_from(Event)
    if conditions:
        requete = requete.where(*conditions)
        requete_compte = requete_compte.where(*conditions)

    total = int(db.execute(requete_compte).scalar_one())
    lignes = (
        db.execute(
            requete.order_by(Event.created_at.desc(), Event.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(lignes), total


def recent_events(db: Session, *, limit: int = 10, agent_id: str | None = None) -> list[Event]:
    """Derniers événements, éventuellement limités à un agent."""
    requete = select(Event)
    if agent_id:
        requete = requete.where(Event.agent_id == agent_id)
    return list(
        db.execute(requete.order_by(Event.created_at.desc(), Event.id.desc()).limit(limit))
        .scalars()
        .all()
    )


def compter_par_type(
    db: Session,
    *,
    event_types: list[str],
    depuis: datetime | None = None,
    agent_id: str | None = None,
    source_ip: str | None = None,
) -> int:
    """Compte les événements correspondants (base des alertes de sécurité)."""
    requete = select(func.count()).select_from(Event).where(Event.event_type.in_(event_types))
    if depuis is not None:
        requete = requete.where(Event.created_at >= depuis)
    if agent_id:
        requete = requete.where(Event.agent_id == agent_id)
    if source_ip:
        requete = requete.where(Event.source_ip == source_ip)
    return int(db.execute(requete).scalar_one())


def evenements_securite(
    db: Session,
    *,
    limit: int = 50,
    fenetre_heures: int = 24,
    severites: list[str] | None = None,
) -> list[Event]:
    """Événements sensibles récents (page « Security » et alertes)."""
    depuis = utcnow() - timedelta(hours=fenetre_heures)
    requete = select(Event).where(Event.created_at >= depuis)
    if severites:
        requete = requete.where(Event.severity.in_(severites))
    else:
        requete = requete.where(Event.severity.in_(EventSeverity.SENSIBLES))
    return list(
        db.execute(requete.order_by(Event.created_at.desc()).limit(limit)).scalars().all()
    )


def statistiques_journal(db: Session, *, fenetre_heures: int = 24) -> dict[str, Any]:
    """Synthèse du journal, utilisée par le tableau de bord."""
    depuis = utcnow() - timedelta(hours=fenetre_heures)
    total = int(
        db.execute(
            select(func.count()).select_from(Event).where(Event.created_at >= depuis)
        ).scalar_one()
    )
    par_gravite = {
        gravite: int(
            db.execute(
                select(func.count())
                .select_from(Event)
                .where(Event.created_at >= depuis, Event.severity == gravite)
            ).scalar_one()
        )
        for gravite in EventSeverity.TOUS
    }
    dernier = db.execute(select(Event.created_at).order_by(Event.created_at.desc()).limit(1)).scalar_one_or_none()
    return {
        "fenetre_heures": fenetre_heures,
        "total": total,
        "par_gravite": par_gravite,
        "dernier_evenement": dernier,
        "anciennete_dernier": humanize_duration(age_seconds(dernier)) if dernier else None,
    }
