"""Schémas d'authentification administrateur (API.md §5).

Le mot de passe n'apparaît **jamais** dans une réponse : ni en clair, ni haché.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import ORMBase
from app.utils.validators import validate_password, validate_username


class LoginRequest(ORMBase):
    """Demande de connexion (API.md §5.1)."""

    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)

    model_config = ORMBase.model_config | {
        "json_schema_extra": {"example": {"username": "admin", "password": "mot_de_passe"}}
    }

    @field_validator("username")
    @classmethod
    def _verifier_nom(cls, valeur: str) -> str:
        return validate_username(valeur)


class AdminOut(ORMBase):
    """Informations publiques du compte administrateur (API.md §5.2)."""

    id: str
    username: str
    is_active: bool
    created_at: datetime | None = None
    last_login_at: datetime | None = None
    password_changed_at: datetime | None = None


class SessionInfo(ORMBase):
    """Caractéristiques de la session créée."""

    created_at: datetime
    expires_at: datetime
    idle_expires_at: datetime
    source_ip: str | None = None


class LoginResponse(ORMBase):
    """Réponse de connexion.

    ``csrf_token`` est un jeton anti-CSRF signé, à renvoyer dans l'en-tête
    ``X-CSRF-Token`` pour toute requête modifiant l'état (SECURITY.md §4).
    Il n'est pas un secret d'authentification et n'est pas persisté côté serveur.
    """

    admin: AdminOut
    session: SessionInfo
    csrf_token: str


class ChangePasswordRequest(ORMBase):
    """Modification du mot de passe (API.md §5.4)."""

    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=1, max_length=256)

    @field_validator("new_password")
    @classmethod
    def _verifier_robustesse(cls, valeur: str) -> str:
        return validate_password(valeur)


class ChangePasswordResponse(ORMBase):
    """Confirmation de changement de mot de passe.

    Le changement révoque les autres sessions actives : l'exploitant sait ainsi
    quelles sessions ont été fermées.
    """

    message: str
    sessions_revoquees: int
