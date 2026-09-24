"""Schémas des paramètres de l'orchestrateur (API.md §9).

Distinction maintenue partout entre les trois notions :

- ``desired_state`` : état **logique** persistant (ONLINE / OFFLINE) ;
- ``service_status`` : état du **processus** (géré par systemd) ;
- ``health_status`` : état de **santé** du backend et de ses dépendances.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import ORMBase
from app.utils.validators import sanitize_text

#: États logiques autorisés (ARCHITECTURE.md §7.6 et §8).
ETATS_LOGIQUES = ("ONLINE", "OFFLINE")


class OrchestratorStateOut(ORMBase):
    """État courant de l'orchestrateur (API.md §9.1)."""

    desired_state: str = Field(..., description="État logique persistant")
    service_status: str = Field(..., description="État du processus systemd")
    health_status: str = Field(..., description="État de santé du backend")
    previous_state: str | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None
    reason: str | None = None
    version: str | None = None
    uptime_seconds: float | None = None
    database_ok: bool = True


class StateChangeRequest(ORMBase):
    """Changement d'état logique (API.md §9.2)."""

    desired_state: str = Field(..., max_length=16)
    reason: str | None = Field(None, max_length=255)

    @field_validator("desired_state")
    @classmethod
    def _verifier_etat(cls, valeur: str) -> str:
        etat = (valeur or "").strip().upper()
        if etat not in ETATS_LOGIQUES:
            raise ValueError(
                "État inconnu. Valeurs autorisées : " + ", ".join(ETATS_LOGIQUES) + "."
            )
        return etat

    @field_validator("reason")
    @classmethod
    def _nettoyer_motif(cls, valeur: str | None) -> str | None:
        return sanitize_text(valeur, max_length=255) if valeur else None


class EnrollmentKeyOut(ORMBase):
    """Clé d'enregistrement consultable par un administrateur autorisé (API.md §9.3).

    La consultation est journalisée, mais la valeur de la clé ne l'est jamais.
    """

    enrollment_key: str = Field(..., description="Clé d'enregistrement active")
    created_at: datetime | None = None
    rotated_at: datetime | None = None
    warning: str = "Cette clé est un secret : ne la partagez que par un canal sécurisé."


class RotateEnrollmentKeyResponse(ORMBase):
    """Réponse à une rotation de clé (API.md §9.4)."""

    enrollment_key: str
    rotated_at: datetime
    agents_impactes: int = Field(
        0,
        description="Agents déjà enregistrés : leur jeton reste valide, seule la clé change.",
    )
    message: str


class ServiceInfoOut(ORMBase):
    """Informations de service affichables sur le tableau de bord."""

    version: str
    environment: str
    started_at: datetime | None = None
    uptime_seconds: float | None = None
    database_ok: bool
    migrations_a_jour: bool
    version_schema: int
    agents_total: int = 0
    agents_en_ligne: int = 0
    agents_hors_ligne: int = 0
    taches_en_attente: int = 0
    taches_en_cours: int = 0
    taches_terminees: int = 0
    taches_en_echec: int = 0


class SettingOut(ORMBase):
    """Paramètre modifiable exposé par l'API."""

    cle: str
    valeur: str | int | bool | float | None = None
    description: str
    modifiable: bool = True


class SettingsOut(ORMBase):
    """Paramètres autorisés (API.md §9 — consultation)."""

    environment: str
    version: str
    agent_offline_threshold_seconds: int
    monitor_interval_seconds: int
    task_default_timeout_seconds: int
    admin_session_ttl_hours: int
    admin_session_idle_minutes: int
    max_request_bytes: int
    max_result_bytes: int
    login_rate_limit: str
    enroll_rate_limit: str
    agent_rate_limit: str
    admin_rate_limit: str
    enable_docs: bool
    trusted_proxies: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)


class SettingUpdateRequest(ORMBase):
    """Modification d'un paramètre autorisé (API.md §9).

    Seuls des paramètres d'exploitation sans impact sur les secrets sont
    modifiables ; ``SECRET_KEY``, ``ENROLLMENT_KEY`` et les chemins de la base ne
    le sont pas depuis l'API.
    """

    agent_offline_threshold_seconds: int | None = Field(None, ge=10, le=86400)
    monitor_interval_seconds: int | None = Field(None, ge=5, le=3600)
    task_default_timeout_seconds: int | None = Field(None, ge=60, le=604800)
    admin_session_ttl_hours: int | None = Field(None, ge=1, le=720)
    admin_session_idle_minutes: int | None = Field(None, ge=5, le=10080)

    def modifications(self) -> dict[str, int]:
        """Paramètres effectivement fournis."""
        return {
            cle: valeur
            for cle, valeur in self.model_dump(exclude_none=True).items()
        }
