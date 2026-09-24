"""Connexion à la base SQLite, fabrique de sessions et dépendance FastAPI.

La base est un fichier unique, conservé hors du répertoire de code en production
(``/var/lib/multi-agent-orchestrator/orchestrator.db`` — INSTALLATION.md §6).

Points d'attention :

- ``foreign_keys=ON`` : SQLite ne l'active pas par défaut, or les relations du
  modèle de données doivent être appliquées (ARCHITECTURE.md §11).
- ``journal_mode=WAL`` : fiabilité et concurrence en lecture pendant l'écriture.
- Le moteur est **reconfigurable** : les tests installent une base temporaire
  isolée via :func:`configure_database`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)

_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None
_CHEMIN_BASE: str | None = None


def _appliquer_pragmas(dbapi_connexion: Any, _enregistrement: Any) -> None:
    """Active les contraintes d'intégrité et la robustesse en écriture."""
    curseur = dbapi_connexion.cursor()
    try:
        curseur.execute("PRAGMA foreign_keys=ON")
        curseur.execute("PRAGMA busy_timeout=5000")
        # WAL n'a pas de sens pour une base en mémoire.
        curseur.execute("PRAGMA journal_mode=WAL")
        curseur.execute("PRAGMA synchronous=NORMAL")
    except Exception:  # pragma: no cover - dépend du moteur sous-jacent
        logger.debug("Pragmas SQLite non appliqués", exc_info=True)
    finally:
        curseur.close()


def configure_database(database_path: str | Path | None = None, *, echo: bool = False) -> Engine:
    """(Re)configure le moteur et la fabrique de sessions.

    :param database_path: chemin du fichier SQLite, ou ``":memory:"`` pour une base
        en mémoire (utilisée par les tests).
    """
    global _ENGINE, _SESSION_FACTORY, _CHEMIN_BASE

    if _ENGINE is not None:
        _ENGINE.dispose()

    if database_path is None:
        from app.core.config import settings

        chemin = settings.database_file
    else:
        chemin = database_path
    _CHEMIN_BASE = str(chemin)

    if _CHEMIN_BASE == ":memory:":
        url = "sqlite+pysqlite:///:memory:"
        moteur = create_engine(
            url,
            echo=echo,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        fichier = Path(_CHEMIN_BASE)
        fichier.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+pysqlite:///{fichier.as_posix()}"
        moteur = create_engine(
            url,
            echo=echo,
            future=True,
            connect_args={"check_same_thread": False},
        )

    event.listen(moteur, "connect", _appliquer_pragmas)
    _ENGINE = moteur
    _SESSION_FACTORY = sessionmaker(bind=moteur, autoflush=False, expire_on_commit=False, future=True)
    return moteur


def get_engine() -> Engine:
    """Moteur courant, configuré au besoin."""
    if _ENGINE is None:
        configure_database()
    assert _ENGINE is not None
    return _ENGINE


def get_session_factory() -> sessionmaker[Session]:
    """Fabrique de sessions courante."""
    if _SESSION_FACTORY is None:
        configure_database()
    assert _SESSION_FACTORY is not None
    return _SESSION_FACTORY


def current_database_path() -> str | None:
    """Chemin de la base actuellement configurée (diagnostic uniquement)."""
    return _CHEMIN_BASE


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session transactionnelle : commit en cas de succès, rollback sinon."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """Dépendance FastAPI fournissant une session par requête."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def healthcheck() -> bool:
    """Vérifie que la base répond (utilisé par la supervision et ``/health``)."""
    try:
        with get_engine().connect() as connexion:
            connexion.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("Contrôle de santé de la base en échec", exc_info=True)
        return False


def reset_database() -> None:
    """Ferme le moteur courant (utilisé par les tests)."""
    global _ENGINE, _SESSION_FACTORY, _CHEMIN_BASE
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None
    _SESSION_FACTORY = None
    _CHEMIN_BASE = None
