"""Contrôle de la recette Docker (docker-compose.yml).

Vérifie, sur une recette réellement démarrée, que l'orchestrateur est opérationnel :
backend joignable, frontend React compilé servi par le proxy, API relayée, compte
administrateur créé par le service d'initialisation.

Utilisation (le fichier Compose doit être démarré au préalable) :

    python deploy/tests/test-recette-compose.py

Variables reconnues : ORCHESTRATOR_WEB_PORT (défaut 8080),
ORCHESTRATOR_API_PORT (défaut 8000), ADMIN_USERNAME (défaut admin),
ADMIN_PASSWORD (exigé pour les contrôles de connexion).
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

WEB = f"http://127.0.0.1:{os.environ.get('ORCHESTRATOR_WEB_PORT', '8080')}"
API = f"http://127.0.0.1:{os.environ.get('ORCHESTRATOR_API_PORT', '8000')}"
UTILISATEUR = os.environ.get("ADMIN_USERNAME", "admin")
MOT_DE_PASSE = os.environ.get("ADMIN_PASSWORD", "")

controles: list[tuple[bool, str]] = []


def verifier(condition: bool, etiquette: str, detail: str = "") -> None:
    controles.append((bool(condition), etiquette))
    suffixe = f" — {detail}" if detail and not condition else ""
    print(f"  [{'OK' if condition else 'ECHEC'}] {etiquette}{suffixe}")


def lire(url: str, methode: str = "GET", corps=None, ouvreur=None, entetes=None):
    """Retourne (code, texte). Un code HTTP d'erreur n'est pas une exception."""
    requete = urllib.request.Request(
        url,
        data=json.dumps(corps).encode() if corps is not None else None,
        headers={"Content-Type": "application/json", **(entetes or {})},
        method=methode,
    )
    try:
        ouvrant = ouvreur.open if ouvreur is not None else urllib.request.urlopen
        with ouvrant(requete, timeout=20) as reponse:
            return reponse.status, reponse.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as erreur:
        return erreur.code, erreur.read().decode("utf-8", errors="replace")
    except Exception as erreur:  # réseau, délai, DNS du conteneur…
        if os.environ.get("ORCHESTRATOR_DEBUG"):
            print(f"  [debug] {methode} {url} -> {type(erreur).__name__} : {erreur}", file=sys.stderr)
        return None, str(erreur)


def connexion(methode: str, chemin: str, corps=None, ouvreur=None, essais: int = 4):
    """Appel d'authentification tolérant à la limitation de débit (429).

    La limite est de 5 connexions par minute et par adresse (SECURITY.md) : une
    série de contrôles rapprochés peut l'atteindre. On attend la fin de la fenêtre
    au lieu de conclure à un échec fonctionnel.
    """
    for tentative in range(essais):
        code, texte_reponse = lire(WEB + chemin, methode=methode, corps=corps, ouvreur=ouvreur)
        if code != 429:
            return code, texte_reponse
        if tentative < essais - 1:
            print("  [info] limite de débit atteinte (429) : attente de 20 s")
            time.sleep(20)
    return code, texte_reponse


def attendre(url: str, essais: int = 40, pause: float = 3.0) -> bool:
    for _ in range(essais):
        code, _ = lire(url)
        if code == 200:
            return True
        time.sleep(pause)
    return False


def main() -> int:
    print(f"  interface : {WEB}")
    print(f"  API       : {API}")

    verifier(attendre(API + "/health"), "l'API répond sur /health (port publié)")

    # --- le proxy sert le frontend compilé ---------------------------------
    verifier(attendre(WEB + "/"), "page d'accueil servie par le proxy")
    code, html = lire(WEB + "/")
    verifier(code == 200 and "<!doctype html" in html.lower(), "HTML du frontend servi")
    verifier("Multi-Agent Orchestrator" in html or "<div id=\"root\"" in html,
             "le document est bien l'application frontend")

    bundle = re.search(r'src="(/assets/[^"]+\.js)"', html) or re.search(r'src="([^"]+\.js)"', html)
    if bundle:
        code_js, contenu = lire(WEB + bundle.group(1))
        verifier(code_js == 200 and len(contenu) > 1000, "bundle JavaScript servi par le proxy")
    else:
        verifier(False, "bundle JavaScript référencé par index.html")

    code, _ = lire(WEB + "/assets/inexistant-xyz.png")
    verifier(code == 404, "asset inexistant refusé (404, aucun repli HTML)")

    code, _ = lire(WEB + "/api/v1/tasks")
    verifier(code in (401, 403), "route /api protégée sans session (refus attendu)",
             f"code={code}")

    # --- API relayée par le proxy ------------------------------------------
    code, corps = lire(WEB + "/health")
    verifier(code == 200 and "status" in corps, "santé relayée par le proxy",
             f"code={code}")

    # --- compte administrateur créé par le service d'initialisation ---------
    if not MOT_DE_PASSE:
        verifier(False, "ADMIN_PASSWORD fourni pour les contrôles de connexion")
    else:
        # Refus d'abord : la limitation de débit des connexions est de 5/minute.
        code, _ = connexion(
            "POST",
            "/api/v1/auth/login",
            {"username": UTILISATEUR, "password": "mauvais-mot-de-passe-000"},
        )
        verifier(code == 401, "mot de passe erroné refusé (401)", f"code={code}")

        jarre = http.cookiejar.CookieJar()
        ouvreur = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jarre))
        code, corps = connexion(
            "POST",
            "/api/v1/auth/login",
            {"username": UTILISATEUR, "password": MOT_DE_PASSE},
            ouvreur=ouvreur,
        )
        verifier(code == 200, "connexion administrateur par le proxy", f"code={code}")

        noms = {c.name for c in jarre}
        verifier("orchestrator_session" in noms, "cookie de session posé (HttpOnly)")
        verifier("csrf_token" in noms, "cookie anti-CSRF posé (lisible par le frontend)")

        jeton = next((c.value for c in jarre if c.name == "csrf_token"), "")
        if jeton and code == 200:
            code, _ = lire(WEB + "/api/v1/agents", ouvreur=ouvreur,
                           entetes={"X-CSRF-Token": jeton})
            verifier(code == 200, "lecture authentifiée de la liste des agents",
                     f"code={code}")

    reussis = sum(1 for ok, _ in controles if ok)
    print(f"\n  Contrôles réussis : {reussis} — échecs : {len(controles) - reussis}")
    return 0 if controles and reussis == len(controles) else 1


if __name__ == "__main__":
    sys.exit(main())
