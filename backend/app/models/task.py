"""Modèle « Tâche » et machine à états du cycle de vie.

Cycle imposé par ARCHITECTURE.md §10 et la mission (§8) :

``PENDING → ASSIGNED → RUNNING → COMPLETED | FAILED | TIMEOUT``
avec annulation possible depuis les états non terminaux.

Toute transition est validée par le backend (voir ``services/task_service.py``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, ISODateTime
from app.utils.datetime_utils import utcnow


class TaskStatus:
    """États d'une tâche."""

    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"

    TOUS = (PENDING, ASSIGNED, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT)
    # États depuis lesquels plus rien ne peut évoluer.
    TERMINAUX = (COMPLETED, FAILED, CANCELLED, TIMEOUT)
    # États dans lesquels la tâche est « vivante ».
    EN_COURS = (PENDING, ASSIGNED, RUNNING)

    #: Transitions autorisées : état actuel → états atteignables.
    TRANSITIONS: dict[str, tuple[str, ...]] = {
        PENDING: (ASSIGNED, RUNNING, CANCELLED, FAILED),
        ASSIGNED: (RUNNING, PENDING, CANCELLED, FAILED, TIMEOUT),
        RUNNING: (COMPLETED, FAILED, CANCELLED, TIMEOUT),
        COMPLETED: (),
        FAILED: (),
        CANCELLED: (),
        TIMEOUT: (),
    }

    @classmethod
    def transition_autorisee(cls, actuel: str, cible: str) -> bool:
        """Indique si le passage de ``actuel`` à ``cible`` est autorisé.

        Une transition vers le même état est refusée : elle est traitée comme une
        répétition de requête (idempotence gérée par les services).
        """
        return cible in cls.TRANSITIONS.get(actuel, ())

    @classmethod
    def est_terminal(cls, statut: str) -> bool:
        return statut in cls.TERMINAUX


class TaskPriority:
    """Niveaux de priorité d'une tâche."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"

    TOUS = (LOW, NORMAL, HIGH, URGENT)


class Task(Base):
    """Tâche créée par un administrateur et exécutée par un agent."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TaskStatus.PENDING, index=True
    )
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TaskPriority.NORMAL, index=True
    )

    assigned_agent_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("admins.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(ISODateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True)

    #: Échéance au-delà de laquelle la tâche est déclarée en dépassement de délai.
    deadline_at: Mapped[datetime | None] = mapped_column(ISODateTime, nullable=True, index=True)

    #: Résultat JSON transmis par l'agent — donnée NON fiable (SECURITY.md §9).
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Nombre de tentatives de prise en charge, utile au diagnostic.
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_tasks_status_created", "status", "created_at"),
        Index("ix_tasks_agent_status", "assigned_agent_id", "status"),
    )

    @property
    def est_terminal(self) -> bool:
        return TaskStatus.est_terminal(self.status)

    @property
    def est_assignable(self) -> bool:
        return self.status in (TaskStatus.PENDING, TaskStatus.ASSIGNED)

    def __repr__(self) -> str:  # pragma: no cover - confort de débogage
        return f"<Task id={self.id!r} etat={self.status!r} agent={self.assigned_agent_id!r}>"
