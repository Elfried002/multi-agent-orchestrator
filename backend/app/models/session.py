"""Modèle « Session administrateur ».

Le document ARCHITECTURE.md §7 ne détaille pas de table de sessions, mais
API.md §3.1 et §5.3 exigent une session **invalidable côté serveur** (« La
déconnexion doit invalider la session côté serveur »). La session est donc
persistée, et non simplement portée par un cookie autoportant : c'est ce qui
permet la révocation immédiate d'une session volée.

Seule l'empreinte du jeton de session est stockée (SECURITY.md §5.2).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, ISODateTime
from app.utils.datetime_utils import utcnow


class AdminSession(Base):
    """Session authentifiée d'un administrateur."""

    __tablename__ = "admin_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    admin_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("admins.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Empreinte SHA-256 du jeton de session : la valeur en clair n'existe que
    # dans le cookie du navigateur.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(ISODateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(ISODateTime, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)

    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    admin: Mapped["Admin"] = relationship("Admin", back_populates="sessions")  # noqa: F821

    __table_args__ = (
        Index("ix_admin_sessions_admin_actives", "admin_id", "revoked_at", "expires_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - confort de débogage
        return f"<AdminSession id={self.id!r} admin={self.admin_id!r} revoquee={self.revoked_at is not None}>"
