"""Versionnement du schéma de la base (migrations).

ARCHITECTURE.md §11 : « Utiliser des migrations ou un mécanisme de versionnement du
schéma adapté ». Le mécanisme retenu est volontairement simple, explicite et
testable :

- une table ``schema_migrations`` conserve les versions appliquées ;
- chaque migration porte un numéro **strictement croissant**, une description et
  une fonction d'application ;
- les migrations sont appliquées dans l'ordre, chacune dans sa transaction ;
- la version 1 crée le schéma initial à partir des modèles SQLAlchemy ;
  les versions suivantes sont écrites en SQL explicite et jamais modifiées après
  application.

Ajouter une migration : incrémenter le numéro et ajouter une entrée à
:data:`MIGRATIONS`. Ne jamais modifier une migration déjà publiée.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.utils.datetime_utils import iso_now, parse_iso

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    """Une étape de migration."""

    version: int
    description: str
    appliquer: Callable[[Connection], None]


# ------------------------------------------------------------------ étapes ---
def _migration_001_schema_initial(connexion: Connection) -> None:
    """Crée le schéma initial à partir des modèles déclarés."""
    import app.models  # noqa: F401  — enregistre toutes les tables dans les métadonnées
    from app.database.base import Base

    Base.metadata.create_all(bind=connexion)


def _migration_002_index_recherche_evenements(connexion: Connection) -> None:
    """Ajoute un index de recherche sur le message des événements.

    La page « Logs » du tableau de bord permet une recherche textuelle ; sans index
    la requête dégénère en parcours complet dès que le journal grossit.
    """
    connexion.execute(text("CREATE INDEX IF NOT EXISTS ix_events_message ON events (message)"))


def _migration_003_index_taches_echeance(connexion: Connection) -> None:
    """Index sur l'échéance des tâches pour le contrôle des dépassements de délai."""
    connexion.execute(
        text("CREATE INDEX IF NOT EXISTS ix_tasks_deadline ON tasks (deadline_at, status)")
    )


def _migration_004_index_jetons_expiration(connexion: Connection) -> None:
    """Index sur l'expiration des jetons pour la purge périodique."""
    connexion.execute(
        text("CREATE INDEX IF NOT EXISTS ix_tokens_expires ON tokens (expires_at, revoked_at)")
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "schéma initial (administrateurs, agents, tâches, événements, jetons)", _migration_001_schema_initial),
    Migration(2, "index de recherche sur le message des événements", _migration_002_index_recherche_evenements),
    Migration(3, "index d'échéance des tâches", _migration_003_index_taches_echeance),
    Migration(4, "index d'expiration des jetons", _migration_004_index_jetons_expiration),
)

VERSION_CIBLE = max(m.version for m in MIGRATIONS)


# --------------------------------------------------------------- exécution ---
def _creer_table_suivi(moteur: Engine) -> None:
    with moteur.begin() as connexion:
        connexion.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version      INTEGER PRIMARY KEY,
                    description  VARCHAR(255) NOT NULL,
                    applied_at   VARCHAR(32)  NOT NULL
                )
                """
            )
        )


def versions_appliquees(moteur: Engine) -> dict[int, str]:
    """Retourne les versions déjà appliquées et leur horodatage."""
    _creer_table_suivi(moteur)
    with moteur.connect() as connexion:
        lignes = connexion.execute(
            text("SELECT version, applied_at FROM schema_migrations ORDER BY version")
        ).fetchall()
    return {int(ligne[0]): str(ligne[1]) for ligne in lignes}


def version_courante(moteur: Engine) -> int:
    """Dernière version appliquée (0 si le schéma est vide)."""
    versions = versions_appliquees(moteur)
    return max(versions) if versions else 0


def migrations_en_attente(moteur: Engine) -> list[Migration]:
    """Migrations non encore appliquées, dans l'ordre."""
    appliquees = set(versions_appliquees(moteur))
    return [m for m in sorted(MIGRATIONS, key=lambda x: x.version) if m.version not in appliquees]


def run_migrations(moteur: Engine) -> list[int]:
    """Applique les migrations en attente et retourne les versions appliquées."""
    _creer_table_suivi(moteur)
    appliquees: list[int] = []
    for migration in migrations_en_attente(moteur):
        logger.info("Application de la migration %s (%s)", migration.version, migration.description)
        try:
            with moteur.begin() as connexion:
                migration.appliquer(connexion)
                connexion.execute(
                    text(
                        "INSERT INTO schema_migrations (version, description, applied_at) "
                        "VALUES (:v, :d, :t)"
                    ),
                    {"v": migration.version, "d": migration.description, "t": iso_now()},
                )
        except Exception:
            logger.exception("Échec de la migration %s", migration.version)
            raise
        appliquees.append(migration.version)
    if appliquees:
        logger.info("Migrations appliquées : %s", appliquees)
    return appliquees


def etat_migrations(moteur: Engine) -> dict[str, object]:
    """Synthèse exploitable par les vérifications de déploiement et les tests."""
    versions = versions_appliquees(moteur)
    return {
        "version_courante": max(versions) if versions else 0,
        "version_cible": VERSION_CIBLE,
        "a_jour": (max(versions) if versions else 0) >= VERSION_CIBLE,
        "appliquees": [
            {
                "version": version,
                "applied_at": horodatage,
                "description": next(
                    (m.description for m in MIGRATIONS if m.version == version), "inconnue"
                ),
            }
            for version, horodatage in sorted(versions.items())
        ],
        "en_attente": [m.version for m in migrations_en_attente(moteur)],
    }


def horodatage_lisible(valeur: str) -> str:
    """Convertit un horodatage stocké en forme lisible (diagnostic)."""
    analyse = parse_iso(valeur)
    if isinstance(analyse, datetime):
        return analyse.strftime("%Y-%m-%d %H:%M:%S UTC")
    return valeur
