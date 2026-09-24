"""Versionnement du schéma : application, idempotence, préservation des données.

Références : ARCHITECTURE.md §11, API.md §9.2, mission §11.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text

from app.core.config import settings
from app.database.connection import configure_database, session_scope
from app.database.migrations import (
    MIGRATIONS,
    run_migrations,
    etat_migrations,
    version_courante,
)
from app.models.event import Event, EventType

TABLES_ATTENDUES = {
    "admins",
    "admin_sessions",
    "agents",
    "tasks",
    "events",
    "tokens",
    "orchestrator_state",
    "schema_migrations",
}


def test_migrations_appliquees_sur_base_vierge(client):
    """Sur une base neuve, toutes les migrations sont appliquées dans l'ordre."""
    with session_scope() as db:
        bind = db.get_bind()
        etat = etat_migrations(bind)

    assert etat["version_courante"] == len(MIGRATIONS)
    assert etat["version_cible"] == len(MIGRATIONS)
    assert etat["a_jour"] is True
    assert etat["en_attente"] == []


def test_tables_du_modele_de_donnees_creees(client):
    """Toutes les tables du modèle sont présentes."""
    with session_scope() as db:
        noms = set(inspect(db.get_bind()).get_table_names())
    manquantes = TABLES_ATTENDUES - noms
    assert not manquantes, f"tables manquantes : {sorted(manquantes)}"


def test_migrations_idempotentes(client):
    """Réappliquer les migrations ne fait rien."""
    moteur = configure_database(settings.database_file)
    assert run_migrations(moteur) == []
    assert version_courante(moteur) == len(MIGRATIONS)

    with session_scope() as db:
        lignes = db.execute(
            text("SELECT version FROM schema_migrations ORDER BY version")
        ).scalars().all()
    assert list(lignes) == [migration.version for migration in MIGRATIONS]


def test_versions_de_migration_uniques_et_croissantes():
    """Les versions sont uniques et strictement croissantes."""
    versions = [migration.version for migration in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
    assert versions[0] == 1
    for migration in MIGRATIONS:
        assert migration.description.strip(), "chaque migration doit être décrite"


def test_donnees_preservees_par_une_remigration(client, session_admin, agent):
    """Une réapplication des migrations ne détruit aucune donnée."""
    from utils import creer_tache

    tache = creer_tache(client, session_admin.csrf, titre="Tache avant migration", agent_id=agent["agent_id"])

    moteur = configure_database(settings.database_file)
    assert run_migrations(moteur) == []

    with session_scope() as db:
        assert db.query(Event).count() > 0
    detail = client.get(f"/api/v1/tasks/{tache['id']}", headers=session_admin.entetes())
    assert detail.status_code == 200
    assert detail.json()["title"] == "Tache avant migration"


def test_etat_des_migrations_expose_au_tableau_de_bord(client, session_admin):
    """Les informations de service indiquent que le schéma est à jour."""
    corps = client.get("/api/v1/settings/service", headers=session_admin.entetes()).json()
    assert corps["migrations_a_jour"] is True
    assert corps["version_schema"] == len(MIGRATIONS)
    assert corps["database_ok"] is True


def test_schema_migrations_contient_les_horodatages(client):
    """Chaque migration appliquée est horodatée (traçabilité des déploiements)."""
    with session_scope() as db:
        lignes = db.execute(
            text("SELECT version, description, applied_at FROM schema_migrations ORDER BY version")
        ).all()
    assert len(lignes) == len(MIGRATIONS)
    for version, description, applique_le in lignes:
        assert isinstance(version, int)
        assert description
        assert applique_le and applique_le.endswith("Z")


def test_documentation_referencee_presente():
    """Les cinq documents de référence sont toujours présents (mission §18)."""
    racine = Path(__file__).resolve().parents[2]
    for nom in (
        "ARCHITECTURE.md",
        "API.md",
        "INSTALLATION.md",
        "SECURITY.md",
        "AGENT_CONNECTION.md",
    ):
        fichier = racine / "docs" / nom
        assert fichier.is_file(), f"document manquant : {nom}"
        assert fichier.stat().st_size > 1000, f"document suspect (trop court) : {nom}"


def test_journal_du_demarrage_contient_letat_restaure(client, session_admin):
    """Le démarrage journalise l'état restauré, pas un état inventé."""
    evenements = client.get(
        f"/api/v1/logs?event_type={EventType.SERVICE_STARTED}", headers=session_admin.entetes()
    ).json()
    assert evenements["total"] == 1
    detail = client.get(
        f"/api/v1/logs/{evenements['items'][0]['id']}", headers=session_admin.entetes()
    ).json()
    assert detail["metadata"]["mode_supervision"] in ("systemd", "manuel")
