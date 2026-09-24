"""Authentification administrateur, sessions, verrouillage, mot de passe.

Références : API.md §5, SECURITY.md §4 et §12.
"""

from __future__ import annotations

from app.core.config import settings
from app.database.connection import session_scope
from app.models.admin import Admin
from app.models.event import Event, EventType
from app.models.session import AdminSession
from app.services.admin_service import MAX_TENTATIVES, creer_admin
from utils import (
    MOT_DE_PASSE_ADMIN,
    MOT_DE_PASSE_ADMIN_BIS,
    MOT_DE_PASSE_FAIBLE,
    code_erreur,
)


def _evenements(event_type: str) -> list[Event]:
    """Lit les événements d'un type donné (vérification côté base)."""
    with session_scope() as db:
        return (
            db.query(Event)
            .filter(Event.event_type == event_type)
            .order_by(Event.created_at)
            .all()
        )


# ------------------------------------------------------------------ connexion
def test_connexion_reussie_et_cookie_protege(client, session_admin):
    """La connexion crée une session serveur et pose un cookie protégé."""
    assert session_admin.csrf
    with session_scope() as db:
        sessions = db.query(AdminSession).all()
    assert len(sessions) == 1
    assert sessions[0].revoked_at is None
    # Le jeton n'est jamais stocké en clair.
    assert len(sessions[0].token_hash) == 64

    evenements = _evenements(EventType.ADMIN_LOGIN_SUCCEEDED)
    assert len(evenements) == 1
    assert evenements[0].success is True


def test_cookie_session_httponly_et_cookie_inaccessible(client):
    """Le cookie de session est HttpOnly et SameSite (SECURITY.md §4)."""
    with session_scope() as db:
        creer_admin(db, username="admin", password=MOT_DE_PASSE_ADMIN)

    reponse = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": MOT_DE_PASSE_ADMIN},
    )
    assert reponse.status_code == 200
    entete = reponse.headers["set-cookie"].lower()
    assert "httponly" in entete
    assert "samesite" in entete
    assert settings.cookie_name in reponse.headers["set-cookie"]
    # Aucun jeton de session n'est exposé dans le corps de la réponse.
    assert settings.cookie_name not in reponse.text


def test_mot_de_passe_incorrect(client):
    """Un mot de passe incorrect est refusé sans révéler l'existence du compte."""
    with session_scope() as db:
        creer_admin(db, username="admin", password=MOT_DE_PASSE_ADMIN)

    reponse = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "MauvaisMotDePasse-2026!"}
    )
    assert reponse.status_code == 401
    assert code_erreur(reponse) == "INVALID_CREDENTIALS"


def test_utilisateur_inconnu_meme_reponse(client):
    """Compte inexistant et mot de passe erroné produisent la même réponse."""
    with session_scope() as db:
        creer_admin(db, username="admin", password=MOT_DE_PASSE_ADMIN)

    inconnu = client.post(
        "/api/v1/auth/login", json={"username": "intrus", "password": MOT_DE_PASSE_ADMIN}
    )
    faux = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "MauvaisMotDePasse-2026!"}
    )
    assert inconnu.status_code == faux.status_code == 401
    assert inconnu.json()["error"]["code"] == faux.json()["error"]["code"]
    assert inconnu.json()["error"]["message"] == faux.json()["error"]["message"]


def test_verrouillage_apres_echecs_repetes(client):
    """Plusieurs échecs consécutifs verrouillent temporairement le compte."""
    with session_scope() as db:
        creer_admin(db, username="admin", password=MOT_DE_PASSE_ADMIN)

    for _ in range(MAX_TENTATIVES):
        client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "MauvaisMotDePasse-2026!"},
        )

    # Même avec le bon mot de passe, le compte est verrouillé.
    reponse = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN}
    )
    assert reponse.status_code == 401

    with session_scope() as db:
        administrateur = db.query(Admin).filter(Admin.username == "admin").one()
        assert administrateur.locked_until is not None
        assert administrateur.failed_login_count >= MAX_TENTATIVES

    assert len(_evenements(EventType.ADMIN_ACCOUNT_LOCKED)) == 1
    # Les échecs précédant le verrouillage, plus la tentative refusée pendant le
    # verrouillage, sont journalisés comme échecs de connexion.
    assert len(_evenements(EventType.ADMIN_LOGIN_FAILED)) == MAX_TENTATIVES
    for evenement in _evenements(EventType.ADMIN_LOGIN_FAILED):
        assert evenement.severity == "WARNING"
        assert "MauvaisMotDePasse" not in evenement.message


# --------------------------------------------------------------------- session
def test_acces_refuse_sans_session(client):
    """Les routes d'administration exigent une session."""
    for chemin in ("/api/v1/auth/me", "/api/v1/agents", "/api/v1/tasks", "/api/v1/logs"):
        reponse = client.get(chemin)
        assert reponse.status_code == 401, chemin
        assert code_erreur(reponse) == "UNAUTHENTICATED"


def test_session_expiree_refusee(client, session_admin):
    """Une session arrivée à échéance est refusée, puis révoquée."""
    from app.utils.datetime_utils import utcnow
    from datetime import timedelta

    with session_scope() as db:
        session = db.query(AdminSession).one()
        session.expires_at = utcnow() - timedelta(minutes=1)

    reponse = client.get("/api/v1/auth/me")
    assert reponse.status_code == 401

    with session_scope() as db:
        session = db.query(AdminSession).one()
        assert session.revoked_at is not None


def test_deconnexion_invalide_la_session_cote_serveur(client, session_admin):
    """La déconnexion révoque la session : le cookie rejoué ne vaut plus rien."""
    assert client.get("/api/v1/auth/me").status_code == 200

    reponse = client.post("/api/v1/auth/logout")
    assert reponse.status_code == 204

    with session_scope() as db:
        session = db.query(AdminSession).one()
        assert session.revoked_at is not None

    assert client.get("/api/v1/auth/me").status_code == 401
    # La déconnexion est journalisée.
    assert len(_evenements(EventType.ADMIN_LOGOUT)) == 1


def test_deconnexion_idempotente(client, session_admin):
    """Une seconde déconnexion ne produit pas d'erreur serveur."""
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.post("/api/v1/auth/logout").status_code == 204


# ------------------------------------------------------- protection anti-CSRF
def test_csrf_requis_pour_les_operations_modifiantes(client, session_admin):
    """Une requête modifiante sans jeton anti-CSRF est refusée."""
    sans_jeton = client.post("/api/v1/tasks", json={"title": "Tâche sans CSRF"})
    assert sans_jeton.status_code == 403
    assert code_erreur(sans_jeton) == "CSRF_TOKEN_INVALID"

    mauvais = client.post(
        "/api/v1/tasks",
        headers={"X-CSRF-Token": "jeton-falsifie"},
        json={"title": "Tâche avec faux CSRF"},
    )
    assert mauvais.status_code == 403
    assert code_erreur(mauvais) == "CSRF_TOKEN_INVALID"

    assert len(_evenements(EventType.CSRF_REJECTED)) == 2

    # Avec le bon jeton, l'opération passe.
    valide = client.post(
        "/api/v1/tasks",
        headers=session_admin.entetes(),
        json={"title": "Tâche légitime"},
    )
    assert valide.status_code == 201


def test_origine_non_autorisee_refusee(client, session_admin):
    """Une origine étrangère sur une requête modifiante est refusée (SECURITY.md §4)."""
    reponse = client.post(
        "/api/v1/tasks",
        headers={**session_admin.entetes(), "Origin": "https://attaquant.example"},
        json={"title": "Tâche depuis une origine hostile"},
    )
    assert reponse.status_code == 403
    assert code_erreur(reponse) == "FORBIDDEN"


# ------------------------------------------------------------ mot de passe
def test_changement_de_mot_de_passe(client, session_admin):
    """Le changement de mot de passe révoque les autres sessions.

    Le jeton anti-CSRF est lié à la session : après une seconde connexion, c'est le
    jeton de cette session courante qui doit être présenté (comportement voulu).
    """
    second = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN}
    )
    assert second.status_code == 200
    with session_scope() as db:
        assert db.query(AdminSession).count() == 2

    reponse = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": second.json()["csrf_token"]},
        json={"current_password": MOT_DE_PASSE_ADMIN, "new_password": MOT_DE_PASSE_ADMIN_BIS},
    )
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["sessions_revoquees"] >= 1
    assert MOT_DE_PASSE_ADMIN_BIS not in reponse.text

    with session_scope() as db:
        administrateur = db.query(Admin).filter(Admin.username == "admin").one()
        assert administrateur.password_hash != MOT_DE_PASSE_ADMIN_BIS
        assert administrateur.password_hash.startswith("$argon2")
        # Les autres sessions sont révoquées, la session courante reste valide.
        revoquees = [s for s in db.query(AdminSession).all() if s.revoked_at is not None]
        assert len(revoquees) == 1

    assert client.get("/api/v1/auth/me").status_code == 200

    with session_scope() as db:
        autres = [
            s for s in db.query(AdminSession).all() if s.revoked_at is not None
        ]
        assert autres and autres[0].revoked_reason

    # L'ancien mot de passe ne fonctionne plus, le nouveau oui.
    assert (
        client.post("/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN})
    ).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN_BIS}
        )
    ).status_code == 200

    assert len(_evenements(EventType.ADMIN_PASSWORD_CHANGED)) == 1
    assert len(_evenements(EventType.ADMIN_CREATED)) == 1


def test_changement_de_mot_de_passe_motif_actuel_incorrect(client, session_admin):
    """Le mot de passe actuel est exigé."""
    reponse = client.post(
        "/api/v1/auth/change-password",
        headers=session_admin.entetes(),
        json={"current_password": "MauvaisMotDePasse-2026!", "new_password": MOT_DE_PASSE_ADMIN_BIS},
    )
    assert reponse.status_code == 401
    assert code_erreur(reponse) == "INVALID_CREDENTIALS"


def test_changement_de_mot_de_passe_trop_faible_refuse(client, session_admin):
    """La politique de robustesse est appliquée côté serveur."""
    reponse = client.post(
        "/api/v1/auth/change-password",
        headers=session_admin.entetes(),
        json={"current_password": MOT_DE_PASSE_ADMIN, "new_password": MOT_DE_PASSE_FAIBLE},
    )
    assert reponse.status_code == 422
    assert code_erreur(reponse) == "VALIDATION_ERROR"

    # Le mot de passe n'a pas été modifié et la réponse ne le reprend pas.
    assert MOT_DE_PASSE_FAIBLE not in reponse.text
    assert (
        client.post("/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN})
    ).status_code == 200


def test_changement_de_mot_de_passe_identique_refuse(client, session_admin):
    """Réutiliser le même mot de passe est refusé."""
    reponse = client.post(
        "/api/v1/auth/change-password",
        headers=session_admin.entetes(),
        json={"current_password": MOT_DE_PASSE_ADMIN, "new_password": MOT_DE_PASSE_ADMIN},
    )
    assert reponse.status_code >= 400
    assert code_erreur(reponse) == "PASSWORD_UNCHANGED"


def test_changement_de_mot_de_passe_exige_csrf(client, session_admin):
    """Le changement de mot de passe exige le jeton anti-CSRF."""
    reponse = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": MOT_DE_PASSE_ADMIN, "new_password": MOT_DE_PASSE_ADMIN_BIS},
    )
    assert reponse.status_code == 403
    assert code_erreur(reponse) == "CSRF_TOKEN_INVALID"


# ------------------------------------------------------------ limitation de débit
def test_limitation_de_debit_sur_la_connexion(client, monkeypatch):
    """Les tentatives de connexion sont limitées (SECURITY.md §12)."""
    monkeypatch.setattr(settings, "login_rate_limit", "3/minute")
    with session_scope() as db:
        creer_admin(db, username="admin", password=MOT_DE_PASSE_ADMIN)

    codes = []
    for _ in range(5):
        reponse = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "MauvaisMotDePasse-2026!"}
        )
        codes.append(reponse.status_code)

    assert 429 in codes, f"Aucune limitation atteinte : {codes}"
    limitee = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "MauvaisMotDePasse-2026!"}
    )
    assert limitee.status_code == 429
    assert code_erreur(limitee) == "RATE_LIMIT_EXCEEDED"
    assert "Retry-After" in limitee.headers

# ------------------------------------------------- jeton anti-CSRF (cookie) --
def test_cookie_csrf_lisible_par_le_frontend(client):
    """Le jeton anti-CSRF est posé en cookie lisible, en plus de la session.

    Le contrôle de double soumission exige que le client puisse relire le jeton
    (SECURITY.md §4). S'il n'était transmis que dans le corps de la réponse de
    connexion, l'interface perdrait toute capacité d'écriture dès le premier
    rechargement de page.
    """
    with session_scope() as db:
        creer_admin(db, username="admin", password=MOT_DE_PASSE_ADMIN)

    reponse = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN}
    )
    assert reponse.status_code == 200

    entetes = reponse.headers.get_list("set-cookie")
    session_cookie = next(e for e in entetes if e.startswith(settings.cookie_name + "="))
    csrf_cookie = next(e for e in entetes if e.startswith("csrf_token="))

    # la session reste inaccessible au JavaScript…
    assert "httponly" in session_cookie.lower()
    # …alors que le jeton anti-CSRF doit être relisible par le client.
    assert "httponly" not in csrf_cookie.lower()
    assert "samesite" in csrf_cookie.lower()
    # le cookie porte exactement le jeton renvoyé dans le corps
    assert reponse.json()["csrf_token"] in csrf_cookie


def test_ecriture_administrateur_apres_rechargement_de_page(client):
    """Scénario d'un onglet rechargé : seuls les cookies subsistent.

    ``GET /auth/me`` est appelé à chaque initialisation du frontend (§13) et doit
    réémettre un jeton utilisable, sinon toutes les actions d'écriture du tableau
    de bord échoueraient en 403 après un simple rafraîchissement.
    """
    with session_scope() as db:
        creer_admin(db, username="admin", password=MOT_DE_PASSE_ADMIN)

    client.post("/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN})
    client.cookies.delete("csrf_token")

    reprise = client.get("/api/v1/auth/me")
    assert reprise.status_code == 200
    jeton = client.cookies.get("csrf_token")
    assert jeton, "le jeton anti-CSRF doit être réémis par /auth/me"

    creation = client.post(
        "/api/v1/tasks",
        json={"title": "Tâche créée après rechargement", "priority": "normal"},
        headers={"X-CSRF-Token": jeton},
    )
    assert creation.status_code in (200, 201), creation.text

    # le contrôle reste actif : sans jeton valide, l'écriture est refusée
    refus = client.post(
        "/api/v1/tasks",
        json={"title": "Tâche refusée faute de jeton"},
        headers={"X-CSRF-Token": "jeton-falsifie"},
    )
    assert refus.status_code == 403


def test_deconnexion_efface_les_deux_cookies(client):
    """La déconnexion invalide la session et efface aussi le jeton anti-CSRF."""
    with session_scope() as db:
        creer_admin(db, username="admin", password=MOT_DE_PASSE_ADMIN)

    client.post("/api/v1/auth/login", json={"username": "admin", "password": MOT_DE_PASSE_ADMIN})
    jeton = client.cookies.get("csrf_token")

    sortie = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": jeton})
    assert sortie.status_code == 204
    entetes = [e.lower() for e in sortie.headers.get_list("set-cookie")]
    assert any(e.startswith("csrf_token=") for e in entetes), entetes
    assert client.get("/api/v1/auth/me").status_code == 401
