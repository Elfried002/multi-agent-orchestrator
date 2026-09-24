"""Contrôles de santé du service.

La route publique ``/health`` (API.md §4) ne doit révéler aucune information
sensible : ce module distingue donc deux niveaux.

- ``etat_public()`` : réponse minimale pour la supervision externe et Nginx ;
- ``verifier_sante()`` : diagnostic détaillé, réservé aux administrateurs
  (``/api/v1/settings/orchestrator``).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.state import (
    SANTE_DEGRADEE,
    SANTE_INDISPONIBLE,
    SANTE_OK,
    demarre_a,
    health_status,
    mode_supervision,
    uptime_seconds,
)
from app.database.connection import healthcheck
from app.database.migrations import etat_migrations

logger = logging.getLogger(__name__)


def etat_public() -> dict[str, str]:
    """Réponse minimale de ``GET /health`` (API.md §4).

    Aucun détail d'infrastructure, aucune version de bibliothèque, aucun chemin.
    """
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.version,
    }


def verifier_base() -> bool:
    """La base de données répond-elle ?"""
    return healthcheck()


def verifier_migrations(db: Session) -> dict:
    """État du versionnement du schéma."""
    try:
        return etat_migrations(db.get_bind())
    except Exception:
        logger.warning("Lecture de l'état des migrations impossible", exc_info=True)
        return {"version_courante": 0, "version_cible": 0, "a_jour": False, "erreur": True}


def verifier_sante(db: Session) -> dict:
    """Diagnostic complet, réservé aux administrateurs."""
    base_ok = verifier_base()
    migrations = verifier_migrations(db)
    sante = health_status(db)
    return {
        "status": sante,
        "healthy": sante == SANTE_OK,
        "checks": {
            "database": {"ok": base_ok},
            "migrations": {
                "ok": bool(migrations.get("a_jour")),
                "version_courante": migrations.get("version_courante"),
                "version_cible": migrations.get("version_cible"),
            },
            "orchestrateur": {"ok": sante != SANTE_INDISPONIBLE},
        },
        "version": settings.version,
        "environment": settings.environment,
        "started_at": demarre_a(),
        "uptime_seconds": round(uptime_seconds(), 3),
        "mode_supervision": mode_supervision(),
        "message": _message_sante(sante),
    }


def _message_sante(sante: str) -> str:
    if sante == SANTE_OK:
        return "Service opérationnel."
    if sante == SANTE_DEGRADEE:
        return "Service dégradé : des migrations sont en attente."
    return "Service indisponible : la base de données ne répond pas."


def problemes_bloquants(db: Session) -> list[str]:
    """Problèmes empêchant un démarrage en production.

    Utilisé au démarrage (journalisés en erreur) et par les tests de vérification
    d'installation.
    """
    problemes: list[str] = []
    if not verifier_base():
        problemes.append("La base de données ne répond pas.")
    migrations = verifier_migrations(db)
    if not migrations.get("a_jour"):
        problemes.append(
            "Le schéma n'est pas à jour "
            f"(version {migrations.get('version_courante')} / cible {migrations.get('version_cible')})."
        )
    return problemes


def resume_uptime() -> str:
    """Durée de fonctionnement, formatée pour l'affichage."""
    from app.utils.datetime_utils import humanize_duration

    return humanize_duration(uptime_seconds())
