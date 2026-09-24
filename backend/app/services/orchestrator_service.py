"""Service d'orchestration : état du service, statistiques, santé.

Ce service ne décide jamais seul : il expose l'état logique géré par
``core/state.py`` et agrège les compteurs des autres services pour le tableau de
bord et la route de santé.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.audit.event_service import statistiques_journal
from app.core.state import (
    ETATS_LOGIQUES,
    info_etat,
    set_desired_state,
)
from app.models.orchestrator_state import OrchestratorState
from app.services.enrollment_service import statistiques_agents
from app.services.task_service import statistiques_taches

logger = logging.getLogger(__name__)


def etat_courant(db: Session) -> dict:
    """État complet de l'orchestrateur (API.md §9.1)."""
    return info_etat(db)


def changer_etat(
    db: Session,
    *,
    etat: str,
    admin_id: str | None,
    motif: str | None = None,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> OrchestratorState:
    """Change l'état logique (API.md §9.2)."""
    return set_desired_state(
        db,
        etat,
        actor_id=admin_id,
        reason=motif,
        source_ip=source_ip,
        request_id=request_id,
    )


def statistiques_globales(db: Session, *, fenetre_heures: int = 24) -> dict:
    """Agrégat destiné au tableau de bord."""
    agents = statistiques_agents(db)
    taches = statistiques_taches(db)
    journal = statistiques_journal(db, fenetre_heures=fenetre_heures)
    return {
        "agents": agents,
        "taches": taches,
        "journal": journal,
    }


def informations_service(db: Session) -> dict:
    """Informations de service, sans aucune donnée sensible."""
    from app.database.connection import current_database_path
    from app.database.migrations import etat_migrations
    from app.core.config import settings
    from app.core.state import demarre_a, health_status, mode_supervision, uptime_seconds

    try:
        migrations = etat_migrations(db.get_bind())
    except Exception:
        logger.warning("Lecture de l'état des migrations impossible", exc_info=True)
        migrations = {"version_courante": 0, "version_cible": 0, "a_jour": False}

    agents = statistiques_agents(db)
    taches = statistiques_taches(db)

    return {
        "version": settings.version,
        "environment": settings.environment,
        "started_at": demarre_a(),
        "uptime_seconds": round(uptime_seconds(), 3),
        "database_ok": health_status(db) != "UNHEALTHY",
        "migrations_a_jour": bool(migrations.get("a_jour")),
        "version_schema": int(migrations.get("version_courante", 0)),
        "version_schema_cible": int(migrations.get("version_cible", 0)),
        "agents_total": agents["total"],
        "agents_en_ligne": agents["en_ligne"],
        "agents_hors_ligne": agents["hors_ligne"],
        "taches_en_attente": taches["en_attente"],
        "taches_en_cours": taches["en_cours"] + taches["assignees"],
        "taches_terminees": taches["terminees"],
        "taches_en_echec": taches["en_echec"],
        "mode_supervision": mode_supervision(),
        # Chemin de la base : utile à l'exploitant, non sensible en soi.
        "base_de_donnees": current_database_path(),
    }


def etats_autorises() -> tuple[str, ...]:
    """États logiques acceptés."""
    return ETATS_LOGIQUES
