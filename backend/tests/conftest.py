"""Fixtures partagées de la suite de tests.

Les variables d'environnement sont définies **avant** l'import de l'application :
la configuration est lue au chargement des modules. Chaque test dispose ensuite de
sa propre base SQLite, d'un environnement isolé et de limites de débit non
contraignantes (sauf pour le test dédié à la limitation de débit).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("ORCHESTRATOR_ENV", "test")
os.environ.setdefault("SECRET_KEY", "cle-de-test-suite-uniquement-" + "0" * 32)
os.environ.setdefault("MONITOR_ENABLED", "false")
os.environ.setdefault("ENABLE_DOCS", "false")
os.environ.setdefault("LOG_DIR", "")
os.environ.setdefault("TRUSTED_PROXIES", "")
os.environ.setdefault("ALLOWED_ORIGINS", "")
os.environ.setdefault("DATABASE_PATH", str(RACINE / "data" / "orchestrator-tests.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.database.connection import configure_database, session_scope  # noqa: E402
from app.database.migrations import run_migrations  # noqa: E402
from app.main import app  # noqa: E402
from app.services.admin_service import creer_admin  # noqa: E402
from app.services.token_service import (  # noqa: E402
    assurer_cle_enregistrement,
    valeur_cle_enregistrement,
)
from utils import (  # noqa: E402
    INSTANCE_A,
    MOT_DE_PASSE_ADMIN,
    enregistrer_agent,
)


@dataclass
class AdminConnecte:
    """Session administrateur active, avec son jeton anti-CSRF."""

    client: TestClient
    csrf: str
    admin_id: str
    username: str

    def entetes(self) -> dict[str, str]:
        """En-têtes à utiliser pour une requête modifiante."""
        return {"X-CSRF-Token": self.csrf}


@pytest.fixture(autouse=True)
def environnement_isole(tmp_path, monkeypatch) -> Path:
    """Base dédiée par test et limites de débit non contraignantes."""
    base = tmp_path / "orchestrator.db"
    monkeypatch.setattr(settings, "database_path", str(base))
    monkeypatch.setattr(settings, "login_rate_limit", "1000/minute")
    monkeypatch.setattr(settings, "enroll_rate_limit", "1000/minute")
    monkeypatch.setattr(settings, "agent_rate_limit", "1000/minute")
    monkeypatch.setattr(settings, "admin_rate_limit", "1000/minute")
    return base


@pytest.fixture()
def client(environnement_isole):  # noqa: ANN001
    """Client HTTP exerçant l'application complète, cycle de vie compris."""
    with TestClient(app) as instance:
        yield instance


@pytest.fixture()
def base_prete(environnement_isole) -> Path:  # noqa: ANN001
    """Prépare la base sans passer par le client HTTP (tests de service)."""
    moteur = configure_database(settings.database_file)
    run_migrations(moteur)
    return environnement_isole


@pytest.fixture()
def session(base_prete):  # noqa: ANN001
    """Session SQLAlchemy directe (tests de service et d'intégrité)."""
    with session_scope() as db:
        yield db


@pytest.fixture()
def session_admin(client) -> AdminConnecte:  # noqa: ANN001
    """Compte administrateur créé puis connecté."""
    with session_scope() as db:
        administrateur, _ = creer_admin(db, username="admin", password=MOT_DE_PASSE_ADMIN)
        identifiant = administrateur.id

    reponse = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": MOT_DE_PASSE_ADMIN},
    )
    assert reponse.status_code == 200, reponse.text
    return AdminConnecte(
        client=client,
        csrf=reponse.json()["csrf_token"],
        admin_id=identifiant,
        username="admin",
    )


@pytest.fixture()
def cle_enregistrement(client) -> str:  # noqa: ANN001
    """Clé d'enregistrement active."""
    with session_scope() as db:
        assurer_cle_enregistrement(db)
        valeur, _ = valeur_cle_enregistrement(db)
    assert valeur
    return valeur


@pytest.fixture()
def agent(client, cle_enregistrement) -> dict:  # noqa: ANN001
    """Agent enregistré et opérationnel."""
    return enregistrer_agent(
        client,
        cle_enregistrement,
        instance_id=INSTANCE_A,
        nom="Agent de test",
    )
