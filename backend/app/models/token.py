"""Modèle « Jeton » — jetons individuels des agents et clé d'enregistrement.

Deux natures de secrets, avec des contraintes différentes (SECURITY.md §5) :

- **Jeton d'agent** : jamais réaffiché après sa création. Seule son empreinte
  SHA-256 est conservée.
- **Clé d'enregistrement** : elle doit rester *consultable* par un administrateur
  authentifié (API.md §9.3). Elle est donc chiffrée au repos (AES-256-GCM, clé
  dérivée de ``SECRET_KEY``) et jamais journalisée.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, ISODateTime
from app.utils.datetime_utils import utcnow


class TokenType:
    """Types de jetons gérés par le service."""

    AGENT = "agent"
    ENROLLMENT = "enrollment"

    TOUS = (AGENT, ENROLLMENT)


class Token(Base):
    """Jeton révocable, associé à un agent ou à l'enregistrement."""

    __tablename__ = "tokens"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    token_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    agent_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True
    )

    label: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(ISODateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)

    # Uniquement pour la clé d'enregistrement : forme chiffrée permettant de la
    # réafficher à un administrateur autorisé.
    encrypted_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_tokens_agent_actifs", "agent_id", "revoked_at", "expires_at"),)

    @property
    def est_revoque(self) -> bool:
        return self.revoked_at is not None

    def __repr__(self) -> str:  # pragma: no cover - confort de débogage
        return f"<Token id={self.id!r} type={self.token_type!r} revoque={self.est_revoque}>"
