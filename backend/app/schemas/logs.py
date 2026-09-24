"""Schémas des journaux et événements d'audit (API.md §10).

Les charges utiles sont expurgées des secrets avant d'être persistées : ce que
l'API retourne ne peut donc pas contenir de mot de passe, de jeton ni de clé.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, Field, field_validator

from app.models.event import EventActorType, EventSeverity, EventType
from app.schemas.common import ORMBase


class EventOut(ORMBase):
    """Événement d'audit tel qu'exposé à l'administrateur."""

    id: str
    event_type: str
    severity: str
    message: str
    agent_id: str | None = None
    actor_type: str
    actor_id: str | None = None
    source_ip: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    success: bool | None = None
    created_at: datetime | None = None


class EventDetailOut(EventOut):
    """Détail d'un événement, métadonnées expurgées comprises."""

    # ``metadata`` est un attribut réservé du modèle déclaratif SQLAlchemy : la
    # lecture doit viser explicitement la colonne ``metadata_json``, tout en
    # conservant le nom ``metadata`` dans le contrat d'API.
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("metadata_json", "metadata"),
    )
    request_id: str | None = None

    @field_validator("metadata", mode="before")
    @classmethod
    def _lire_metadonnees(cls, valeur: Any) -> dict[str, Any]:
        if isinstance(valeur, dict):
            return valeur
        if isinstance(valeur, str) and valeur:
            try:
                charge = json.loads(valeur)
                return charge if isinstance(charge, dict) else {}
            except (TypeError, ValueError):
                return {}
        return {}


class JournalReference(ORMBase):
    """Valeurs de référence pour construire les filtres du tableau de bord."""

    severites: list[str] = Field(default_factory=lambda: list(EventSeverity.TOUS))
    types_evenement: list[str] = Field(default_factory=lambda: sorted(
        valeur
        for nom, valeur in vars(EventType).items()
        if not nom.startswith("_") and isinstance(valeur, str)
    ))
    types_acteur: list[str] = Field(default_factory=lambda: list(EventActorType.TOUS))
    severites_sensibles: list[str] = Field(default_factory=lambda: list(EventSeverity.SENSIBLES))
