"""Schémas partagés : enveloppe d'erreur, pagination, base ORM.

Le format d'erreur est imposé par API.md §2.3 et doit être identique sur toutes
les routes :

```json
{"error": {"code": "...", "message": "...", "request_id": "..."}}
```
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMBase(BaseModel):
    """Base des schémas construits depuis des objets SQLAlchemy."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ErreurDetail(BaseModel):
    """Détail d'une erreur applicative."""

    code: str = Field(..., description="Code stable et exploitable par le client")
    message: str = Field(..., description="Message lisible, sans secret")
    request_id: str | None = Field(None, description="Identifiant de la requête, pour l'audit")


class EnveloppeErreur(BaseModel):
    """Format unique des réponses d'erreur (API.md §2.3)."""

    error: ErreurDetail

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {
                    "code": "INVALID_CREDENTIALS",
                    "message": "Authentification invalide.",
                    "request_id": "req_2f1c9d",
                }
            }
        }
    )


class ListePage(BaseModel, Generic[T]):
    """Réponse paginée — forme imposée par API.md §7.1."""

    items: list[T]
    total: int = Field(..., ge=0, description="Nombre total d'éléments correspondants")
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1)


class MessageResponse(BaseModel):
    """Réponse générique d'accusé de traitement."""

    message: str
    success: bool = True


class Pagination(BaseModel):
    """Paramètres de pagination communs aux listes."""

    page: int = Field(1, ge=1, le=10_000)
    page_size: int = Field(20, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def reponse_liste(items: list[Any], total: int, page: int, page_size: int) -> dict[str, Any]:
    """Construit le corps d'une réponse paginée."""
    return {"items": items, "total": total, "page": page, "page_size": page_size}
