"""Intégration réelle entre l'orchestrateur et le runtime Hermes.

Ce test n'est pas une simulation : il démarre un vrai serveur, enrôle un agent avec
le connecteur, crée une tâche dans l'orchestrateur, laisse la **CLI Hermes locale**
traiter l'énoncé de cette tâche, puis vérifie que le résultat est bien enregistré
côté serveur.

Il est ignoré proprement si la commande ``hermes`` est absente, afin de rester
utilisable en intégration continue sans runtime Hermes installé.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import secrets
import shutil
import socket
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
BACKEND = RACINE / "backend"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)
CONNECTEUR = RACINE / "connector" / "hermes_connector.py"
SECRET_KEY = "cle-de-test-integration-hermes-0123456789abcdef"
UTILISATEUR = "admin"
MOT_DE_PASSE = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
ENONCE_ATTENDU = "RESULTAT-HERMES-OK"

controles: list[tuple[bool, str]] = []


def verifier(condition: bool, etiquette: str) -> None:
    controles.append((bool(condition), etiquette))
    print(f"  [{'OK' if condition else 'ECHEC'}] {etiquette}")


def port_libre() -> int:
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        return int(prise.getsockname()[1])


def main() -> int:
    hermes = shutil.which("hermes")
    if hermes is None:
        print("CLI Hermes absente : test d'intégration ignoré (aucun échec).")
        return 0

    travail = Path(os.environ.get("LOCALAPPDATA", "/tmp")) / "Temp" / "mo-hermes"
    shutil.rmtree(travail, ignore_errors=True)
    travail.mkdir(parents=True, exist_ok=True)
    port = port_libre()
    base = f"http://127.0.0.1:{port}"
    etat_connecteur = travail / "agent-state.json"
    print(f"  serveur de test   : {base}")
    print(f"  CLI Hermes        : {hermes}")

    environnement = {
        **os.environ,
        "SECRET_KEY": SECRET_KEY,
        "ORCHESTRATOR_ENV": "development",
        "MONITOR_ENABLED": "true",
        "LOG_LEVEL": "WARNING",
        "LOGIN_RATE_LIMIT": "1000/minute",
        "ENROLLMENT_KEY": "cle-enregistrement-test-hermes-0123456789",
        "DATABASE_PATH": str(travail / "test.db"),
        "LOG_DIR": str(travail / "logs"),
    }

    serveur = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND),
        env=environnement,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        for _ in range(45):
            try:
                with urllib.request.urlopen(base + "/health", timeout=2) as reponse:
                    if reponse.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            verifier(False, "le service répond sur /health")
            return 1
        verifier(True, "le service répond sur /health")

        # --- compte administrateur -------------------------------------------
        creation = subprocess.run(
            [
                str(PYTHON),
                str(BACKEND / "scripts" / "create_admin.py"),
                "--username",
                UTILISATEUR,
                "--password-stdin",
                "--database",
                str(travail / "test.db"),
            ],
            input=MOT_DE_PASSE + "\n",
            text=True,
            capture_output=True,
            env=environnement,
        )
        verifier(creation.returncode == 0, "compte administrateur créé")

        jarre = http.cookiejar.CookieJar()
        ouvreur = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jarre))

        def admin(methode: str, chemin: str, corps=None, attendu=(200, 201)):
            requete = urllib.request.Request(
                base + chemin,
                data=json.dumps(corps).encode() if corps is not None else None,
                headers={"Content-Type": "application/json"},
                method=methode,
            )
            try:
                with ouvreur.open(requete, timeout=20) as reponse:
                    brut = reponse.read().decode()
                    return reponse.status, (json.loads(brut) if brut else None)
            except urllib.error.HTTPError as erreur:
                brut = erreur.read().decode()
                return erreur.code, (json.loads(brut) if brut else None)

        statut, _ = admin(
            "POST",
            "/api/v1/auth/login",
            {"username": UTILISATEUR, "password": MOT_DE_PASSE},
            attendu=(200,),
        )
        verifier(statut == 200, "connexion administrateur")
        jeton_csrf = next((c.value for c in jarre if c.name == "csrf_token"), None)
        ouvreur.addheaders = [("X-CSRF-Token", jeton_csrf or "")]

        # --- enrôlement par le connecteur ------------------------------------
        enrolement = subprocess.run(
            [
                str(PYTHON),
                str(CONNECTEUR),
                "enroll",
                "--url",
                base,
                "--state",
                str(etat_connecteur),
                "--enrollment-key",
                environnement["ENROLLMENT_KEY"],
                "--name",
                "Agent Hermes de recette",
                "--role",
                "general",
                "--runtime",
                "hermes",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if enrolement.returncode != 0:
            print(enrolement.stdout[-600:], enrolement.stderr[-600:])
        verifier(enrolement.returncode == 0 and etat_connecteur.is_file(), "agent enrôlé par le connecteur")
        if not etat_connecteur.is_file():
            return 1
        agent_id = json.loads(etat_connecteur.read_text(encoding="utf-8"))["agent_id"]

        # --- tâche confiée à Hermes ------------------------------------------
        statut, tache = admin(
            "POST",
            "/api/v1/tasks",
            {
                "title": "Vérification d'intégration Hermes",
                "description": (
                    "Réponds exactement par cette chaîne, sans aucun autre texte : "
                    f"{ENONCE_ATTENDU}"
                ),
                "priority": "high",
                "assigned_agent_id": agent_id,
            },
        )
        verifier(statut in (200, 201), "tâche créée et attribuée à l'agent")
        if statut not in (200, 201):
            return 1
        identifiant = tache["id"]
        print(f"  tâche             : {identifiant}")

        # --- exécution par la CLI Hermes, via le connecteur -------------------
        print("  exécution par Hermes (peut prendre une minute)…")
        debut = time.time()
        execution = subprocess.run(
            [
                str(PYTHON),
                str(CONNECTEUR),
                "run",
                "--url",
                base,
                "--state",
                str(etat_connecteur),
                "--tours",
                "3",
                "--interval",
                "1",
                "--task-timeout",
                "240",
                "--hermes",
                "--hermes-binaire",
                hermes,
            ],
            capture_output=True,
            text=True,
            timeout=420,
        )
        print(f"  durée d'exécution : {time.time() - debut:.0f} s")
        if execution.returncode != 0:
            print("  sortie du connecteur :", execution.stdout[-800:], execution.stderr[-400:])
        verifier(execution.returncode == 0, "boucle du connecteur terminée sans erreur")

        statut, detail = admin("GET", f"/api/v1/tasks/{identifiant}")
        verifier(
            statut == 200 and detail.get("status") == "COMPLETED",
            f"tâche terminée côté serveur (état : {detail.get('status') if statut == 200 else statut})",
        )

        resultat = detail.get("result") if isinstance(detail, dict) else None
        texte = json.dumps(resultat, ensure_ascii=False) if resultat is not None else ""
        verifier(ENONCE_ATTENDU in texte, "le résultat enregistré provient bien de Hermes")
        verifier(
            isinstance(resultat, dict) and resultat.get("moteur") == "hermes-cli",
            "le résultat identifie le moteur d'exécution (hermes-cli)",
        )
        if resultat:
            resume = str(resultat.get("resume", ""))[:120].replace("\n", " ")
            print(f"  réponse de Hermes : {resume!r}")

        statut, agent = admin("GET", f"/api/v1/agents/{agent_id}")
        verifier(statut == 200 and agent.get("status") == "ONLINE", "agent vu en ligne par le serveur")

    finally:
        serveur.terminate()
        try:
            serveur.wait(timeout=15)
        except subprocess.TimeoutExpired:
            serveur.kill()

    reussis = sum(1 for ok, _ in controles if ok)
    print(f"\n  Contrôles réussis : {reussis} — échecs : {len(controles) - reussis}")
    return 0 if reussis == len(controles) else 1


if __name__ == "__main__":
    sys.exit(main())
