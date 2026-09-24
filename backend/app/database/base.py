"""Base déclarative SQLAlchemy et type d'horodatage partagé.

Référence : ARCHITECTURE.md §5 (module ``database/``), §7 (modèle de données).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import MetaData, String
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from app.utils.datetime_utils import parse_iso, to_iso

# Convention de nommage : rend les contraintes et index nommés de manière
# prévisible, ce qui permet de les cibler dans les migrations.
CONVENTION_NOMMAGE = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class ISODateTime(TypeDecorator):
    """Horodatage stocké sous forme de chaîne ISO 8601 UTC.

    SQLite ne dispose pas de type date natif ; stocker une chaîne à précision fixe
    garantit un ordre lexicographique correct (donc des filtres de date fiables)
    sans dépendre de la couche de conversion de SQLite.
    """

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return to_iso(value)

    def process_result_value(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return parse_iso(value)


class Base(DeclarativeBase):
    """Classe de base de tous les modèles persistants."""

    metadata = MetaData(naming_convention=CONVENTION_NOMMAGE)
