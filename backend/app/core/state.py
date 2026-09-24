"""État logique persistant de l'orchestrateur.

Trois notions distinctes, jamais confondues (ARCHITECTURE.md §8, INSTALLATION.md §7) :

1. **état du processus** (systemd) — ``service_status`` ;
2. **état logique** (``ONLINE`` / ``OFFLINE``) — ``desired_state``, persisté en base
   et restauré au démarrage ;
3. **état de santé** du backend et de ses dépendances — ``health_status``.

Le passage logique à ``OFFLINE`` **n'arrête pas** le processus : le service reste
actif pour que l'administrateur puisse consulter le tableau de bord et revenir en
ligne, tandis que les opérations métier des agents sont refusées.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy.orm import Session

from app.audit.event_service import record_event
from app.models.event import EventActorType, EventSeverity, EventType
from app.models.orchestrator_state import LIGNE_UNIQUE, OrchestratorState, etat_par_defaut
from app.utils.datetime_utils import to_iso, utcnow

logger = logging.getLogger(__name__)

ETAT_EN_LIGNE = "ONLINE"
ETAT_HORS_LIGNE = "OFFLINE"
ETATS_LOGIQUES = (ETAT_EN_LIGNE, ETAT_HORS_LIGNE)

SERVICE_EN_COURS = "RUNNING"
SANTE_OK = "HEALTHY"
SANTE_DEGRADEE = "DEGRADED"
SANTE_INDISPONIBLE = "UNHEALTHY"

#: Instant de démarrage du processus, fixé par ``main`` (sert au calcul de la durée
#: de fonctionnement affichée sur le tableau de bord).
_DEMARRAGE = utcnow()


def marquer_demarrage() -> None:
    """Fixe l'instant de démarrage du processus."""
    global _DEMARRAGE
    _DEMARRAGE = utcnow()


def demarre_a() -> str:
    return to_iso(_DEMARRAGE) or ""


def uptime_seconds() -> float:
    return (utcnow() - _DEMARRAGE).total_seconds()


def mode_supervision() -> str:
    """Indique si le processus est supervisé par systemd ou lancé manuellement.

    systemd exporte ``INVOCATION_ID`` ; ce champ est informatif et n'intervient
    jamais dans l'autorisation.
    """
    return "systemd" if os.environ.get("INVOCATION_ID") else "manuel"


# ------------------------------------------------------------ état logique ---
def get_state_row(db: Session) -> OrchestratorState:
    """Retourne la ligne d'état, en la créant si la base est neuve."""
    ligne = db.get(OrchestratorState, LIGNE_UNIQUE)
    if ligne is None:
        ligne = etat_par_defaut()
        db.add(ligne)
        db.commit()
        db.refresh(ligne)
    return ligne


def get_desired_state(db: Session) -> str:
    """État logique courant (``ONLINE`` ou ``OFFLINE``)."""
    return get_state_row(db).desired_state


def is_online(db: Session) -> bool:
    """Indique si l'orchestrateur accepte le trafic métier des agents."""
    return get_desired_state(db) == ETAT_EN_LIGNE


def set_desired_state(
    db: Session,
    etat: str,
    *,
    actor_id: str | None = None,
    actor_type: str = EventActorType.ADMIN,
    reason: str | None = None,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> OrchestratorState:
    """Change l'état logique et journalise la transition.

    Un changement vers l'état déjà courant est accepté mais ne produit pas de
    nouvel événement de changement d'état : la répétition est idempotente.
    """
    cible = (etat or "").strip().upper()
    if cible not in ETATS_LOGIQUES:
        raise ValueError(f"État logique inconnu : {etat!r}")

    ligne = get_state_row(db)
    precedent = ligne.desired_state
    if precedent == cible:
        return ligne

    ligne.previous_state = precedent
    ligne.desired_state = cible
    ligne.reason = (reason or "").strip() or None
    ligne.updated_at = utcnow()
    ligne.updated_by = actor_id
    ligne.updated_by_type = actor_type
    db.add(ligne)

    record_event(
        db,
        event_type=EventType.STATE_CHANGED,
        message=f"État logique de l'orchestrateur : {precedent} → {cible}",
        severity=EventSeverity.WARNING if cible == ETAT_HORS_LIGNE else EventSeverity.INFO,
        actor_type=actor_type,
        actor_id=actor_id,
        source_ip=source_ip,
        request_id=request_id,
        object_type="orchestrator_state",
        object_id=str(LIGNE_UNIQUE),
        success=True,
        metadata={"precedent": precedent, "nouveau": cible, "motif": ligne.reason},
        commit=False,
    )
    db.commit()
    db.refresh(ligne)
    logger.warning("État logique de l'orchestrateur : %s → %s", precedent, cible)
    return ligne


# -------------------------------------------------------------- santé -------
def health_status(db: Session) -> str:
    """État de santé du backend : dépend de la base et du schéma.

    - ``HEALTHY``     : base joignable et schéma à jour ;
    - ``DEGRADED``    : base joignable mais migrations en attente ;
    - ``UNHEALTHY``   : base injoignable.
    """
    from app.database.connection import healthcheck
    from app.database.migrations import etat_migrations

    if not healthcheck():
        return SANTE_INDISPONIBLE
    try:
        etat = etat_migrations(db.get_bind())
        return SANTE_OK if etat.get("a_jour") else SANTE_DEGRADEE
    except Exception:
        logger.warning("Vérification des migrations impossible", exc_info=True)
        return SANTE_DEGRADEE


def info_etat(db: Session) -> dict[str, Any]:
    """Synthèse destinée à la route ``GET /api/v1/settings/orchestrator``."""
    ligne = get_state_row(db)
    return {
        "desired_state": ligne.desired_state,
        "service_status": SERVICE_EN_COURS,
        "health_status": health_status(db),
        "previous_state": ligne.previous_state,
        "updated_at": ligne.updated_at,
        "updated_by": ligne.updated_by,
        "reason": ligne.reason,
        "version": _version_application(),
        "uptime_seconds": round(uptime_seconds(), 3),
        "database_ok": health_status(db) != SANTE_INDISPONIBLE,
        "mode_supervision": mode_supervision(),
    }


def _version_application() -> str:
    from app.core.config import settings

    return settings.version
