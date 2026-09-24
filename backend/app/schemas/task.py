"""Schémas des tâches (API.md §8) et des transitions d'état.

Les transitions autorisées sont définies dans ``models/task.py`` et vérifiées par
le service : aucun client ne peut imposer une transition.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from app.models.task import TaskPriority, TaskStatus
from app.schemas.common import ORMBase
from app.utils.validators import sanitize_text


class TaskCreateRequest(ORMBase):
    """Création d'une tâche par l'administrateur (API.md §8.1)."""

    title: str = Field(..., min_length=3, max_length=200)
    description: str | None = Field(None, max_length=8000)
    priority: str = Field(TaskPriority.NORMAL, max_length=16)
    assigned_agent_id: str | None = Field(None, max_length=40)

    @field_validator("title")
    @classmethod
    def _verifier_titre(cls, valeur: str) -> str:
        titre = sanitize_text(valeur, max_length=200)
        if len(titre) < 3:
            raise ValueError("Le titre doit contenir au moins 3 caractères.")
        return titre

    @field_validator("description")
    @classmethod
    def _nettoyer_description(cls, valeur: str | None) -> str | None:
        if valeur is None:
            return None
        return sanitize_text(valeur, max_length=8000)

    @field_validator("priority")
    @classmethod
    def _verifier_priorite(cls, valeur: str) -> str:
        priorite = (valeur or "").strip().upper()
        if priorite not in TaskPriority.TOUS:
            raise ValueError(f"Priorité inconnue. Valeurs acceptées : {', '.join(TaskPriority.TOUS)}.")
        return priorite


class TaskOut(ORMBase):
    """Tâche telle qu'exposée à l'administrateur."""

    id: str
    title: str
    description: str | None = None
    status: str
    priority: str
    assigned_agent_id: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    assigned_at: datetime | None = None
    acked_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    deadline_at: datetime | None = None
    error_message: str | None = None
    attempt_count: int = 0


class TaskDetailOut(TaskOut):
    """Détail d'une tâche, résultat compris (API.md §8.3)."""

    result: dict[str, Any] | None = None
    assigned_agent_name: str | None = None
    peut_etre_annulee: bool = False

    @field_validator("result", mode="before")
    @classmethod
    def _lire_resultat(cls, valeur: Any) -> dict[str, Any] | None:
        if valeur is None or valeur == "":
            return None
        if isinstance(valeur, dict):
            return valeur
        if isinstance(valeur, str):
            try:
                charge = json.loads(valeur)
            except (TypeError, ValueError):
                # Un résultat non JSON est conservé sous forme de texte : il ne faut
                # jamais perdre la donnée transmise par un agent.
                return {"brut": valeur}
            if isinstance(charge, dict):
                return charge
            return {"valeur": charge}
        return {"valeur": valeur}


class TaskCancelRequest(ORMBase):
    """Annulation d'une tâche (API.md §8.4)."""

    reason: str | None = Field(None, max_length=255)

    @field_validator("reason")
    @classmethod
    def _nettoyer_motif(cls, valeur: str | None) -> str | None:
        return sanitize_text(valeur, max_length=255) if valeur else None


class TaskStateInfo(ORMBase):
    """Machine à états exposée aux clients (utile au tableau de bord)."""

    etats: list[str]
    etats_terminaux: list[str]
    transitions: dict[str, list[str]]

    @classmethod
    def depuis_modele(cls) -> "TaskStateInfo":
        return cls(
            etats=list(TaskStatus.TOUS),
            etats_terminaux=list(TaskStatus.TERMINAUX),
            transitions={etat: list(cibles) for etat, cibles in TaskStatus.TRANSITIONS.items()},
        )
