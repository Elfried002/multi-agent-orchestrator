"""Schémas des agents : enregistrement, identité, heartbeat, tâches.

Rappel appliqué dans tout ce module : les capacités déclarées par un agent sont des
**déclarations**, jamais une preuve de confiance, et aucun secret (jeton) n'est
retourné après l'enregistrement initial (SECURITY.md §7).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, Field, field_validator, model_validator

from app.models.agent import AgentStatus
from app.models.task import TaskPriority, TaskStatus
from app.schemas.common import ORMBase
from app.utils.validators import (
    validate_capabilities,
    validate_client_instance_id,
    sanitize_text,
)


def _charger_liste(valeur: Any) -> list[str]:
    """Convertit une colonne JSON texte en liste de capacités."""
    if valeur is None or valeur == "":
        return []
    if isinstance(valeur, str):
        try:
            valeur = json.loads(valeur)
        except (TypeError, ValueError):
            return []
    if isinstance(valeur, (list, tuple)):
        return [str(element) for element in valeur]
    return []


# --------------------------------------------------------------- entrées -----
class EnrollRequest(ORMBase):
    """Demande d'enregistrement d'un agent (API.md §6.1)."""

    runtime: str = Field("hermes", min_length=1, max_length=32)
    client_instance_id: str = Field(..., description="Identifiant local stable du connecteur")
    requested_name: str = Field(..., min_length=1, max_length=80)
    declared_role: str | None = Field(None, max_length=32)
    capabilities: list[str] = Field(default_factory=list)
    version: str | None = Field(None, max_length=32)

    @field_validator("client_instance_id")
    @classmethod
    def _verifier_instance(cls, valeur: str) -> str:
        return validate_client_instance_id(valeur)

    @field_validator("runtime")
    @classmethod
    def _normaliser_runtime(cls, valeur: str) -> str:
        return sanitize_text(valeur, max_length=32).lower() or "hermes"

    @field_validator("requested_name")
    @classmethod
    def _normaliser_nom(cls, valeur: str) -> str:
        nom = sanitize_text(valeur, max_length=80)
        if not nom:
            raise ValueError("Le nom demandé ne peut pas être vide.")
        return nom

    @field_validator("capabilities")
    @classmethod
    def _verifier_capacites(cls, valeur: list[str]) -> list[str]:
        return validate_capabilities(valeur)


class HeartbeatRequest(ORMBase):
    """Signal de présence (API.md §6.3).

    L'horodatage fourni par l'agent est **informative** : le serveur utilise son
    propre horodatage pour déterminer le dernier contact fiable.
    """

    status: str = Field("ONLINE", max_length=16)
    runtime_status: str | None = Field(None, max_length=64)
    timestamp: datetime | None = None

    @field_validator("status")
    @classmethod
    def _verifier_etat(cls, valeur: str) -> str:
        autorises = (AgentStatus.ONLINE, AgentStatus.PENDING)
        etat = (valeur or "").strip().upper()
        if etat not in autorises:
            raise ValueError(
                "L'état déclaré par un agent doit être ONLINE ou PENDING ; "
                "les états OFFLINE et REVOKED sont déterminés par le serveur."
            )
        return etat


class TaskStatusUpdateRequest(ORMBase):
    """Mise à jour d'état d'une tâche par l'agent (API.md §6.6)."""

    status: str = Field(..., max_length=16)
    message: str | None = Field(None, max_length=500)

    @field_validator("status")
    @classmethod
    def _verifier_etat(cls, valeur: str) -> str:
        etat = (valeur or "").strip().upper()
        if etat not in (TaskStatus.RUNNING, TaskStatus.FAILED):
            raise ValueError(
                "Un agent peut uniquement déclarer RUNNING ou FAILED ; "
                "COMPLETED et TIMEOUT passent par la transmission de résultat."
            )
        return etat


class TaskResultRequest(ORMBase):
    """Transmission du résultat d'une tâche (API.md §6.7)."""

    status: str = Field(..., max_length=16)
    result: dict[str, Any] | None = None
    error_message: str | None = Field(None, max_length=2000)

    @field_validator("status")
    @classmethod
    def _verifier_statut(cls, valeur: str) -> str:
        etat = (valeur or "").strip().upper()
        if etat not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            raise ValueError("Le statut final doit être COMPLETED ou FAILED.")
        return etat

    @model_validator(mode="after")
    def _verifier_coherence(self) -> "TaskResultRequest":
        if self.status == TaskStatus.FAILED and not self.error_message:
            # Un échec sans motif n'est pas exploitable par l'exploitant.
            self.error_message = "Échec signalé par l'agent sans message d'erreur."
        return self


# --------------------------------------------------------------- sorties -----
class AgentOut(ORMBase):
    """Agent tel qu'exposé à l'administrateur (API.md §7.1).

    Aucun jeton n'apparaît ici : ni empreinte, ni identifiant de jeton.
    """

    id: str
    name: str
    role: str
    runtime: str
    status: str
    source_ip: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    version: str | None = None
    created_at: datetime | None = None
    last_seen_at: datetime | None = None

    @field_validator("capabilities", mode="before")
    @classmethod
    def _lire_capacites(cls, valeur: Any) -> list[str]:
        return _charger_liste(valeur)


class AgentDetailOut(AgentOut):
    """Détail d'un agent (API.md §7.2)."""

    client_instance_id: str | None = None
    disconnected_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_reason: str | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("metadata_json", "metadata"),
    )

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


class EnrollResponse(ORMBase):
    """Réponse d'enregistrement (API.md §6.1).

    ``access_token`` est le seul moment où le jeton individuel est transmis : il
    n'est jamais journalisé et jamais réaffiché.
    """

    agent_id: str
    name: str
    role: str
    status: str
    access_token: str
    token_type: str = "Bearer"
    created_at: datetime | None = None


class AgentIdentityOut(ORMBase):
    """Identité de l'agent courant (API.md §6.2)."""

    agent_id: str
    name: str
    role: str
    runtime: str
    status: str
    capabilities: list[str] = Field(default_factory=list)
    instance_id: str | None = None
    registered_at: datetime | None = None
    last_seen_at: datetime | None = None

    @field_validator("capabilities", mode="before")
    @classmethod
    def _lire_capacites(cls, valeur: Any) -> list[str]:
        return _charger_liste(valeur)


class HeartbeatResponse(ORMBase):
    """Réponse à un heartbeat (API.md §6.3)."""

    accepted: bool
    agent_status: str
    orchestrator_status: str
    server_time: datetime


class AgentTaskOut(ORMBase):
    """Tâche telle que présentée à l'agent propriétaire."""

    id: str
    title: str
    description: str | None = None
    status: str
    priority: str = TaskPriority.NORMAL
    created_at: datetime | None = None
    deadline_at: datetime | None = None


class AckResponse(ORMBase):
    """Confirmation de prise en charge (API.md §6.5)."""

    task_id: str
    status: str
    acked_at: datetime | None = None
    deja_prise_en_charge: bool = False
    message: str


class AgentActionResult(ORMBase):
    """Résultat d'une action d'administration sur un agent."""

    agent_id: str
    status: str
    message: str
