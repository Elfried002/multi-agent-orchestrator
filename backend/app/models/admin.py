"""Modèle « Administrateur » (ARCHITECTURE.md §7.1).

Le mot de passe en clair n'est jamais stocké : seule une empreinte Argon2id est
conservée (SECURITY.md §4).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, ISODateTime
from app.utils.datetime_utils import utcnow

if TYPE_CHECKING:
    from app.models.session import AdminSession


class Admin(Base):
    """Compte administrateur du tableau de bord."""

    __tablename__ = "admins"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(ISODateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)

    # Protection contre les tentatives répétées (SECURITY.md §4, §12).
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    last_failed_login_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)

    sessions: Mapped[list["AdminSession"]] = relationship(
        "AdminSession",
        back_populates="admin",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - confort de débogage
        return f"<Admin id={self.id!r} username={self.username!r} actif={self.is_active}>"
