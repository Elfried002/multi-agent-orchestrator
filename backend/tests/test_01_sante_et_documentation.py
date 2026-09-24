"""Santé publique, protection de la documentation et en-têtes de sécurité.

Références : API.md §4 et §11, SECURITY.md §13.
"""

from __future__ import annotations


def test_sante_publique_sans_information_sensible(client):
    """``/health`` répond sans authentification et ne divulgue rien d'exploitable."""
    reponse = client.get("/health")
    assert reponse.status_code == 200

    corps = reponse.json()
    assert set(corps) == {"status", "service", "version"}, corps
    assert corps["status"] == "ok"

    texte = reponse.text.lower()
    for interdit in ("sqlite", "database", "secret", "token", "c:\\", "/var/", "traceback"):
        assert interdit not in texte, f"« {interdit} » ne doit pas apparaître dans /health"


def test_entetes_de_securite(client):
    """Les en-têtes de protection sont présents sur les réponses de l'application."""
    reponse = client.get("/health")
    assert reponse.headers["X-Content-Type-Options"] == "nosniff"
    assert reponse.headers["X-Frame-Options"] == "DENY"
    assert reponse.headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'self'" in reponse.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in reponse.headers["Content-Security-Policy"]
    assert reponse.headers["Cache-Control"] == "no-store"
    assert reponse.headers["X-Request-ID"]


def test_identifiant_de_requete_propage(client):
    """Un identifiant de requête est renvoyé et peut être fourni par le client."""
    fourni = "req_test_0123456789abcdef"
    reponse = client.get("/health", headers={"X-Request-ID": fourni})
    assert reponse.headers["X-Request-ID"] == fourni


def test_documentation_protegee_sans_session(client):
    """En production, la documentation exige une authentification administrateur."""
    for chemin in ("/docs", "/redoc", "/openapi.json"):
        reponse = client.get(chemin)
        assert reponse.status_code == 401, f"{chemin} devrait exiger une session"
        assert reponse.json()["error"]["code"] == "UNAUTHENTICATED"


def test_documentation_accessible_a_l_administrateur(client, session_admin):
    """L'administrateur authentifié accède à la spécification OpenAPI."""
    reponse = client.get("/openapi.json")
    assert reponse.status_code == 200

    schema = reponse.json()
    assert schema["info"]["title"] == "Multi-Agent Orchestrator"
    chemins = schema["paths"]
    for attendu in (
        "/api/v1/auth/login",
        "/api/v1/agents/enroll",
        "/api/v1/agents/heartbeat",
        "/api/v1/agents/tasks",
        "/api/v1/tasks",
        "/api/v1/logs",
        "/api/v1/settings/orchestrator",
        "/api/v1/settings/enrollment-key",
    ):
        assert attendu in chemins, f"route absente de la spécification : {attendu}"

    # La spécification ne doit contenir aucun secret ni empreinte.
    texte = reponse.text
    assert "password_hash" not in texte
    assert "$argon2" not in texte


def test_route_inconnue_format_normalise(client):
    """Une route inconnue renvoie l'enveloppe d'erreur normalisée."""
    reponse = client.get("/api/v1/route-inexistante")
    assert reponse.status_code == 404
    corps = reponse.json()
    assert corps["error"]["code"] == "NOT_FOUND"
    assert "request_id" in corps["error"]


def test_methode_non_autorisee_format_normalise(client):
    """Une méthode non autorisée renvoie une erreur normalisée, pas une page HTML."""
    reponse = client.delete("/health")
    assert reponse.status_code == 405
    assert "error" in reponse.json()


def test_requete_trop_volumineuse_refusee(client):
    """Le corps de requête est borné (SECURITY.md §12)."""
    from app.core.config import settings

    charge = {"username": "admin", "password": "x" * (settings.max_request_bytes + 10)}
    reponse = client.post("/api/v1/auth/login", json=charge)
    assert reponse.status_code == 413
    assert reponse.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
