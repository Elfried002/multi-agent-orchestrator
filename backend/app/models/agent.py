"""Modèle « Agent » (ARCHITECTURE.md §7.2).

Rappels de sécurité appliqués par ce modèle :

- l'identifiant interne est **attribué par le serveur** (§2.1) ;
- les capacités déclarées sont des données non fiables qui n'accordent aucun
  privilège (SECURITY.md §7) ;
- aucun secret (jeton) n'est stocké sur l'agent : seule la référence du jeton
  l'est, via ``credential_id``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, ISODateTime
from app.utils.datetime_utils import utcnow


class AgentStatus:
    """États de connexion d'un agent (ARCHITECTURE.md §9)."""

    PENDING = "PENDING"
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    REVOKED = "REVOKED"

    TOUS = (PENDING, ONLINE, OFFLINE, REVOKED)
    # États pour lesquels l'agent peut encore agir.
    ACTIFS = (PENDING, ONLINE, OFFLINE)


class Agent(Base):
    """Agent enregistré auprès de l'orchestrateur."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    runtime: Mapped[str] = mapped_column(String(32), nullable=False, default="hermes", index=True)

    # Liste JSON des capacités déclarées — déclaratif, jamais une preuve.
    capabilities: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AgentStatus.PENDING, index=True
    )

    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    client_instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(ISODateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)

    credential_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("tokens.id", ondelete="SET NULL"), nullable=True
    )

    # Colonne nommée « metadata » côté base (ARCHITECTURE.md §7.2) ; l'attribut
    # Python porte un autre nom car « metadata » est réservé par SQLAlchemy.
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)

    __table_args__ = (
        # Empêche la création de doublons pour une même identité locale de
        # connecteur, sans bloquer un nouvel enregistrement après révocation
        # (AGENT_CONNECTION.md §13 et §14).
        Index(
            "uq_agents_instance_active",
            "runtime",
            "client_instance_id",
            unique=True,
            sqlite_where=text("status != 'REVOKED' AND client_instance_id IS NOT NULL"),
        ),
        Index("ix_agents_status_last_seen", "status", "last_seen_at"),
    )

    @property
    def est_revoque(self) -> bool:
        return self.status == AgentStatus.REVOKED

    @property
    def est_actif(self) -> bool:
        return self.status in AgentStatus.ACTIFS

    def __repr__(self) -> str:  # pragma: no cover - confort de débogage
        return f"<Agent id={self.id!r} nom={self.name!r} role={self.role!r} etat={self.status}>"
