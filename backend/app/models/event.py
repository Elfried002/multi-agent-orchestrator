"""Modèle « Événement » — journal d'audit et de sécurité (ARCHITECTURE.md §7.4).

Chaque événement porte l'identité de l'acteur, l'adresse IP observée, la date, le
type d'événement et l'objet concerné (SECURITY.md §10). Les métadonnées sont
expurgées de tout secret avant persistance.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, ISODateTime
from app.utils.datetime_utils import utcnow


class EventSeverity:
    """Niveaux de gravité."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    TOUS = (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    #: Niveaux considérés comme sensibles (page « Security » du tableau de bord).
    SENSIBLES = (WARNING, ERROR, CRITICAL)


class EventActorType:
    """Nature de l'acteur à l'origine d'un événement."""

    ADMIN = "admin"
    AGENT = "agent"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"

    TOUS = (ADMIN, AGENT, SYSTEM, ANONYMOUS)


class EventType:
    """Types d'événements journalisés (SECURITY.md §10).

    Les valeurs sont stables : elles servent de filtre d'API et de base aux
    alertes de sécurité.
    """

    # Cycle de vie du service
    SERVICE_STARTED = "service.started"
    SERVICE_STOPPED = "service.stopped"
    SERVICE_ERROR = "service.error"

    # État logique de l'orchestrateur
    STATE_CHANGED = "orchestrator.state_changed"

    # Authentification administrateur
    ADMIN_LOGIN_SUCCEEDED = "admin.login_succeeded"
    ADMIN_LOGIN_FAILED = "admin.login_failed"
    ADMIN_LOGOUT = "admin.logout"
    ADMIN_CREATED = "admin.created"
    ADMIN_PASSWORD_CHANGED = "admin.password_changed"
    ADMIN_SESSION_REVOKED = "admin.session_revoked"
    ADMIN_ACCOUNT_LOCKED = "admin.account_locked"

    # Cycle de vie des agents
    AGENT_ENROLLED = "agent.enrolled"
    AGENT_ENROLLMENT_REFUSED = "agent.enrollment_refused"
    AGENT_HEARTBEAT = "agent.heartbeat"
    AGENT_HEARTBEAT_ANOMALY = "agent.heartbeat_anomaly"
    AGENT_WENT_OFFLINE = "agent.went_offline"
    AGENT_RECONNECTED = "agent.reconnected"
    AGENT_DISCONNECTED = "agent.disconnected"
    AGENT_REVOKED = "agent.revoked"
    AGENT_DELETED = "agent.deleted"

    # Authentification des agents
    AGENT_AUTH_FAILED = "agent.auth_failed"
    AGENT_TOKEN_REVOKED_USED = "agent.token_revoked_used"
    AGENT_FORBIDDEN = "agent.forbidden"

    # Tâches
    TASK_CREATED = "task.created"
    TASK_ASSIGNED = "task.assigned"
    TASK_ACKED = "task.acked"
    TASK_STATUS_CHANGED = "task.status_changed"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    TASK_TIMEOUT = "task.timeout"
    TASK_DUPLICATE_IGNORED = "task.duplicate_ignored"

    # Clés et secrets
    ENROLLMENT_KEY_READ = "enrollment_key.read"
    ENROLLMENT_KEY_ROTATED = "enrollment_key.rotated"

    # Sécurité applicative
    RATE_LIMIT_EXCEEDED = "security.rate_limit_exceeded"
    AUTHORIZATION_DENIED = "security.authorization_denied"
    CSRF_REJECTED = "security.csrf_rejected"
    INVALID_INPUT = "security.invalid_input"


class Event(Base):
    """Événement d'audit persisté."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EventSeverity.INFO, index=True
    )
    message: Mapped[str] = mapped_column(String(500), nullable=False)

    agent_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EventActorType.SYSTEM, index=True
    )
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    object_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Résultat de l'opération : True (succès), False (échec), None (neutre).
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        ISODateTime, nullable=False, default=utcnow, index=True
    )

    metadata_json: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)

    __table_args__ = (
        Index("ix_events_type_created", "event_type", "created_at"),
        Index("ix_events_severity_created", "severity", "created_at"),
        Index("ix_events_agent_created", "agent_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - confort de débogage
        return f"<Event id={self.id!r} type={self.event_type!r} gravite={self.severity!r}>"
