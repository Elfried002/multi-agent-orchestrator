"""Route publique de santé (API.md §4).

Cette route n'exige aucune authentification et ne doit révéler aucune information
sensible : elle ne renvoie que l'état, le nom du service et la version applicative.
Aucun chemin, aucune version de bibliothèque, aucun détail d'infrastructure.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.monitoring.health_monitor import etat_public

router = APIRouter(tags=["Santé"])


@router.get(
    "/health",
    summary="État de santé du service",
    description=(
        "Contrôle de disponibilité destiné à la supervision et au reverse proxy. "
        "Réponse volontairement minimale : aucune information sensible."
    ),
    responses={200: {"description": "Service disponible"}},
)
def sante() -> dict[str, str]:
    """Retourne l'état minimal du service."""
    return etat_public()
