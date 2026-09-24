"""Tests de sécurité exigés par la mission §16 et SECURITY.md.

Les scénarios sont volontairement offensifs : chacun doit être **refusé**, et le
refus doit être **journalisé**.
"""

from __future__ import annotations

import json
from datetime import timedelta

from app.core.config import settings
from app.database.connection import session_scope
from app.models.event import Event, EventType
from app.models.token import Token, TokenType
from app.services.token_service import rotation_cle_enregistrement
from app.utils.datetime_utils import utcnow
from utils import (
    INSTANCE_A,
    MOT_DE_PASSE_ADMIN,
    code_erreur,
    creer_tache,
    enregistrer_agent,
    entetes_agent,
    entetes_cle,
)


def _evenements(event_type: str) -> list[Event]:
    with session_scope() as db:
        return db.query(Event).filter(Event.event_type == event_type).all()


# ------------------------------------------------------- accès non authentifiés
def test_acces_administrateur_sans_session(client):
    """Toutes les routes d'administration refusent l'anonyme."""
    for chemin in (
        "/api/v1/auth/me",
        "/api/v1/agents",
        "/api/v1/tasks",
        "/api/v1/logs",
        "/api/v1/settings/orchestrator",
        "/api/v1/settings/service",
        "/api/v1/settings/enrollment-key",
        "/api/v1/settings/parameters",
        "/api/v1/settings/security",
        "/docs",
        "/openapi.json",
    ):
        reponse = client.get(chemin)
        assert reponse.status_code == 401, chemin
        assert code_erreur(reponse) == "UNAUTHENTICATED"


def test_operations_modifiantes_sans_session(client):
    """Aucune opération modifiante n'est possible sans session."""
    for chemin, corps in (
        ("/api/v1/tasks", {"title": "Tâche anonyme"}),
        ("/api/v1/settings/orchestrator/state", {"desired_state": "OFFLINE"}),
        ("/api/v1/settings/enrollment-key/rotate", None),
    ):
        reponse = client.post(chemin, json=corps) if corps else client.post(chemin)
        assert reponse.status_code in (401, 403), chemin


def test_cookie_falsifie_refuse(client, session_admin):
    """Un cookie inventé ne donne aucun accès."""
    falsifie = client.get(
        "/api/v1/auth/me", headers={"Cookie": f"{settings.cookie_name}=jeton-invente-abcdef"}
    )
    assert falsifie.status_code == 401

    vide = client.get("/api/v1/auth/me", headers={"Cookie": f"{settings.cookie_name}="})
    assert vide.status_code == 401


# ------------------------------------------------------------------ jetons agents
def test_jeton_agent_invalide(client, agent):
    """Un jeton inconnu est refusé et journalisé."""
    reponse = client.get("/api/v1/agents/me", headers=entetes_agent("jeton-inexistant-000000"))
    assert reponse.status_code == 401
    assert code_erreur(reponse) == "UNAUTHENTICATED"
    assert len(_evenements(EventType.AGENT_AUTH_FAILED)) >= 1


def test_jeton_agent_revoque(client, session_admin, agent):
    """Un jeton révoqué est refusé même s'il était valide."""
    entetes = entetes_agent(agent["access_token"])
    assert client.get("/api/v1/agents/me", headers=entetes).status_code == 200

    client.post(
        f"/api/v1/agents/{agent['agent_id']}/revoke",
        headers=session_admin.entetes(),
        json={"reason": "test de sécurité"},
    )
    reponse = client.get("/api/v1/agents/me", headers=entetes)
    assert reponse.status_code == 401
    assert len(_evenements(EventType.AGENT_TOKEN_REVOKED_USED)) >= 1


def test_jeton_agent_expire(client, agent):
    """Un jeton arrivé à échéance est refusé."""
    with session_scope() as db:
        jeton = (
            db.query(Token)
            .filter(Token.agent_id == agent["agent_id"], Token.token_type == TokenType.AGENT)
            .one()
        )
        jeton.expires_at = utcnow() - timedelta(minutes=1)

    reponse = client.get("/api/v1/agents/me", headers=entetes_agent(agent["access_token"]))
    assert reponse.status_code == 401


def test_jeton_mal_forme(client, agent):
    """Un en-tête Authorization mal formé est refusé proprement."""
    for entete in ("Bearer", "Bearer ", "Basic YWRtaW46YWRtaW4=", "jeton-nu", "Bearer a b c"):
        reponse = client.get("/api/v1/agents/me", headers={"Authorization": entete})
        assert reponse.status_code == 401, entete


def test_cle_enregistrement_invalide_et_rotatee(client, cle_enregistrement, session_admin):
    """La rotation révoque l'ancienne clé immédiatement."""
    ancienne = cle_enregistrement

    reponse = client.post("/api/v1/settings/enrollment-key/rotate", headers=session_admin.entetes())
    assert reponse.status_code == 200
    nouvelle = reponse.json()["enrollment_key"]
    assert nouvelle != ancienne
    assert ancienne not in reponse.text or nouvelle == ancienne
    assert len(_evenements(EventType.ENROLLMENT_KEY_ROTATED)) == 1

    # L'ancienne clé ne fonctionne plus.
    refus = client.post(
        "/api/v1/agents/enroll",
        headers=entetes_cle(ancienne),
        json={
            "runtime": "hermes",
            "client_instance_id": "instance-apres-rotation-1",
            "requested_name": "Agent clé périmée",
            "capabilities": [],
        },
    )
    assert refus.status_code == 401

    # La nouvelle fonctionne.
    accepte = enregistrer_agent(
        client, nouvelle, instance_id="instance-apres-rotation-1", nom="Agent clé valide"
    )
    assert accepte["agent_id"].startswith("agt_")


def test_rotation_conservee_en_base(client, cle_enregistrement, session_admin):
    """Après rotation, une seule clé active subsiste pour le type « enrollment »."""
    client.post("/api/v1/settings/enrollment-key/rotate", headers=session_admin.entetes())
    with session_scope() as db:
        cles = db.query(Token).filter(Token.token_type == TokenType.ENROLLMENT).all()
    assert len(cles) == 2
    assert sum(1 for cle in cles if cle.revoked_at is None) == 1


# ------------------------------------------------------------------ cloisonnement
def test_jamais_un_jeton_agent_pour_les_routes_administrateur(client, agent):
    """Un jeton d'agent ne peut rien faire d'administratif."""
    entetes = entetes_agent(agent["access_token"])
    assert client.get("/api/v1/agents", headers=entetes).status_code == 401
    assert (
        client.post("/api/v1/tasks", headers=entetes, json={"title": "Tâche par agent"}).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/settings/orchestrator/state",
            headers=entetes,
            json={"desired_state": "OFFLINE"},
        ).status_code
        == 401
    )


def test_acces_a_la_tache_d_un_autre_agent(client, session_admin, agent, cle_enregistrement):
    """Un agent ne peut pas agir sur la tâche d'un autre, et la tentative est journalisée."""
    autre = enregistrer_agent(
        client, cle_enregistrement, instance_id="instance-securite-02", nom="Agent tiers"
    )
    tache = creer_tache(
        client, session_admin.csrf, titre="Tache protegee", agent_id=agent["agent_id"]
    )

    entetes = entetes_agent(autre["access_token"])
    reponse = client.post(f"/api/v1/agents/tasks/{tache['id']}/ack", headers=entetes)
    assert reponse.status_code == 403
    assert code_erreur(reponse) == "TASK_NOT_ASSIGNED_TO_AGENT"
    assert len(_evenements(EventType.AGENT_FORBIDDEN)) >= 1

    # La liste des tâches de l'intrus ne contient rien.
    assert client.get("/api/v1/agents/tasks", headers=entetes).json() == []


def test_agent_ne_peut_pas_se_donner_de_droits(client, cle_enregistrement):
    """Identité, rôle et capacités sont imposés ou bornés par le serveur."""
    corps = enregistrer_agent(
        client,
        cle_enregistrement,
        instance_id="instance-securite-03",
        nom="Agent revendiquant des droits",
        role="admin",
        capacites=["admin", "manage_agents", "revoke"],
    )
    assert corps["role"] != "admin"
    entetes = entetes_agent(corps["access_token"])
    assert client.get("/api/v1/agents", headers=entetes).status_code == 401


# ------------------------------------------------------------------- entrées
def test_entrees_malformees(client, session_admin, cle_enregistrement):
    """Les entrées hostiles sont refusées sans fuite ni erreur serveur."""
    cas = [
        ("/api/v1/agents/enroll", "post", entetes_cle(cle_enregistrement), {"runtime": 123}),
        ("/api/v1/agents/enroll", "post", entetes_cle(cle_enregistrement), {"capabilities": "texte"}),
        (
            "/api/v1/agents/enroll",
            "post",
            entetes_cle(cle_enregistrement),
            {"client_instance_id": "x", "requested_name": "a" * 5000, "runtime": "hermes"},
        ),
        ("/api/v1/tasks", "post", session_admin.entetes(), {"title": None}),
        ("/api/v1/tasks", "post", session_admin.entetes(), {"title": ["liste"]}),
        ("/api/v1/auth/login", "post", {}, {"username": {"a": 1}, "password": "x"}),
    ]
    for chemin, methode, entetes, corps in cas:
        reponse = getattr(client, methode)(chemin, headers=entetes, json=corps)
        assert reponse.status_code in (401, 403, 413, 422), f"{chemin} → {reponse.status_code}"
        assert "Traceback" not in reponse.text
        assert "sqlalchemy" not in reponse.text.lower()

    # Injection SQL dans un filtre de recherche : traitée comme du texte.
    tache = creer_tache(client, session_admin.csrf, titre="Tache pour injection")
    reponse = client.get(
        "/api/v1/tasks",
        params={"search": "' OR 1=1; DROP TABLE tasks; --"},
        headers=session_admin.entetes(),
    )
    assert reponse.status_code == 200
    assert reponse.json()["total"] == 0
    # La table existe toujours.
    assert client.get(f"/api/v1/tasks/{tache['id']}", headers=session_admin.entetes()).status_code == 200


def test_json_invalide_refuse(client, session_admin):
    """Un corps non conforme produit une erreur normalisée."""
    reponse = client.post(
        "/api/v1/tasks",
        headers={**session_admin.entetes(), "Content-Type": "application/json"},
        content=b"{ceci n'est pas du json",
    )
    assert reponse.status_code == 422
    assert "error" in reponse.json()


def test_chemin_traversant_refuse(client, session_admin):
    """Aucune remontée de répertoire n'est possible via les paramètres."""
    reponse = client.get(
        "/api/v1/logs", params={"search": "../../../../etc/passwd"}, headers=session_admin.entetes()
    )
    assert reponse.status_code == 200
    assert "root:" not in reponse.text


# --------------------------------------------------------------- tentatives
def test_tentatives_repetees_limitees_et_journalisees(client, monkeypatch):
    """Les tentatives de connexion répétées sont limitées puis journalisées."""
    from app.services.admin_service import creer_admin

    with session_scope() as db:
        creer_admin(db, username="admin", password=MOT_DE_PASSE_ADMIN)
    monkeypatch.setattr(settings, "login_rate_limit", "3/minute")
    # Le compteur de limitation est partagé par le processus : il est remis à zéro
    # pour que ce test mesure bien sa propre séquence de tentatives.
    from app.core.rate_limit import limiter

    limiter.reset()

    codes = [
        client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Mauvais-2026!x"}
        ).status_code
        for _ in range(4)
    ]
    assert 429 in codes, codes
    assert len(_evenements(EventType.ADMIN_LOGIN_FAILED)) >= 1


def test_tls_non_desactive_dans_le_code():
    """Aucune désactivation de la vérification TLS dans le projet (mission §13)."""
    from pathlib import Path

    racine = Path(__file__).resolve().parents[1] / "app"
    interdits = ("verify=False", "verify = False", "ssl._create_unverified_context", "CERT_NONE")
    for fichier in racine.rglob("*.py"):
        contenu = fichier.read_text(encoding="utf-8")
        for motif in interdits:
            assert motif not in contenu, f"{motif} trouvé dans {fichier.name}"


def test_aucun_secret_en_dur_dans_le_code():
    """Aucun secret réel n'est écrit dans le code source."""
    from pathlib import Path

    # Seuls les sources applicatifs sont audités : le présent fichier contient
    # les motifs recherchés et se signalerait lui-même.
    racine = Path(__file__).resolve().parents[1] / "app"
    suspects = []
    for fichier in list(racine.rglob("*.py")):
        contenu = fichier.read_text(encoding="utf-8", errors="ignore")
        for motif in ("sk-", "BEGIN RSA PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY", "AKIA"):
            if motif in contenu:
                suspects.append(f"{fichier.name}:{motif}")
    assert not suspects, suspects


def test_sauvegarde_de_la_base_non_exposee(client, session_admin, environ_isole=None):
    """La base SQLite n'est jamais servie et son chemin n'est pas public."""
    assert "orchestrator.db" not in client.get("/health").text
    assert ".db" not in client.get("/api/v1/settings/parameters", headers=session_admin.entetes()).text
