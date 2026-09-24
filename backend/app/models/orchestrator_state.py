"""Modèle « État de l'orchestrateur » — état logique persistant.

ARCHITECTURE.md §7.6 et §8 : l'état logique (``ONLINE`` / ``OFFLINE``) doit
survivre aux redémarrages et ne jamais être confondu avec l'état du processus
systemd ni avec l'état de santé du backend.

La table ne contient qu'une seule ligne (``id = 1``), ce qui rend l'état unique
par construction.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, ISODateTime
from app.utils.datetime_utils import utcnow

#: Identifiant de la ligne unique.
LIGNE_UNIQUE = 1


class OrchestratorState(Base):
    """État logique désiré de l'orchestrateur."""

    __tablename__ = "orchestrator_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=LIGNE_UNIQUE)
    desired_state: Mapped[str] = mapped_column(String(16), nullable=False, default="ONLINE")

    previous_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    updated_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by_type: Mapped[str | None] = mapped_column(String(16), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - confort de débogage
        return f"<OrchestratorState etat={self.desired_state!r} maj={self.updated_at}>"


def etat_par_defaut() -> OrchestratorState:
    """Ligne d'état initiale : l'orchestrateur démarre en ligne."""
    return OrchestratorState(
        id=LIGNE_UNIQUE,
        desired_state="ONLINE",
        updated_at=utcnow(),
        updated_by="system",
        updated_by_type="system",
    )
