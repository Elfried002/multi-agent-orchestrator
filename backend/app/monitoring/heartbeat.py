"""Surveillance de présence des agents, des délais et des jetons.

Une passe de contrôle :

1. détecte les agents dont le dernier signal dépasse le seuil configuré et les
   passe à ``OFFLINE`` avec un événement d'audit (ARCHITECTURE.md §9) ;
2. déclare en dépassement de délai les tâches dont l'échéance est passée ;
3. révoque les jetons expirés.

La boucle est un tâche asyncio lancée au démarrage du service. Chaque passe
s'exécute dans un fil d'exécution séparé pour ne pas bloquer la boucle d'événements
(SQLite est une base synchrone).

Portée : surveillance **applicative** uniquement — elle ne remplace ni un IDS ni une
capture réseau (SECURITY.md §11).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from app.core.config import settings
from app.database.connection import session_scope
from app.services.agent_service import agents_injoignables, marquer_hors_ligne
from app.services.task_service import marquer_depassements
from app.services.token_service import purge_jetons_expires

logger = logging.getLogger(__name__)

#: Clé de stockage de la tâche de fond dans ``app.state``.
CLE_ETAT_SURVEILLANCE = "surveillance_presence"


class SurveillancePresence:
    """Contrôle périodique de la présence des agents et des échéances."""

    def __init__(self, *, intervalle_secondes: int | None = None, seuil_secondes: int | None = None) -> None:
        self.intervalle_secondes = intervalle_secondes or settings.monitor_interval_seconds
        self.seuil_secondes = seuil_secondes or settings.agent_offline_threshold_seconds
        self._tache: asyncio.Task[None] | None = None
        self._arret = asyncio.Event()

    # ------------------------------------------------------------ une passe
    def controler_une_fois(self) -> dict[str, int]:
        """Exécute une passe complète de contrôle et retourne les compteurs."""
        resultat = {"agents_hors_ligne": 0, "taches_en_depassement": 0, "jetons_revoques": 0}
        with session_scope() as db:
            for agent in agents_injoignables(db, seuil_secondes=self.seuil_secondes):
                marquer_hors_ligne(db, agent, seuil_secondes=self.seuil_secondes)
                resultat["agents_hors_ligne"] += 1
            resultat["taches_en_depassement"] = marquer_depassements(db)
            resultat["jetons_revoques"] = purge_jetons_expires(db)
        if any(resultat.values()):
            logger.info(
                "Passe de surveillance : %s agent(s) hors ligne, %s tâche(s) en dépassement, "
                "%s jeton(s) révoqué(s)",
                resultat["agents_hors_ligne"],
                resultat["taches_en_depassement"],
                resultat["jetons_revoques"],
            )
        return resultat

    # ------------------------------------------------------------- boucle
    async def boucler(self) -> None:
        """Boucle de contrôle jusqu'à demande d'arrêt."""
        logger.info(
            "Surveillance démarrée (intervalle %s s, seuil de présence %s s)",
            self.intervalle_secondes,
            self.seuil_secondes,
        )
        while not self._arret.is_set():
            try:
                await asyncio.wait_for(self._arret.wait(), timeout=self.intervalle_secondes)
                break
            except asyncio.TimeoutError:
                pass
            try:
                await asyncio.to_thread(self.controler_une_fois)
            except Exception:
                # Une passe en échec ne doit jamais interrompre la surveillance.
                logger.exception("Échec d'une passe de surveillance (poursuite de la boucle)")
        logger.info("Surveillance arrêtée.")

    def demarrer(self) -> asyncio.Task[None]:
        """Démarre la boucle et retourne la tâche créée."""
        self._arret.clear()
        self._tache = asyncio.create_task(self.boucler(), name="surveillance-presence")
        return self._tache

    async def arreter(self) -> None:
        """Demande l'arrêt et attend la fin de la boucle."""
        self._arret.set()
        if self._tache is not None:
            try:
                await asyncio.wait_for(self._tache, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._tache.cancel()
            self._tache = None


def demarrer_surveillance(app: FastAPI) -> SurveillancePresence | None:
    """Lance la surveillance si elle est activée par la configuration."""
    if not settings.monitor_enabled:
        logger.info("Surveillance désactivée par configuration (MONITOR_ENABLED=false)")
        return None
    surveillance = SurveillancePresence()
    surveillance.demarrer()
    setattr(app.state, CLE_ETAT_SURVEILLANCE, surveillance)
    return surveillance


async def arreter_surveillance(app: FastAPI) -> None:
    """Arrête proprement la surveillance."""
    surveillance: SurveillancePresence | None = getattr(app.state, CLE_ETAT_SURVEILLANCE, None)
    if surveillance is not None:
        await surveillance.arreter()
        setattr(app.state, CLE_ETAT_SURVEILLANCE, None)
