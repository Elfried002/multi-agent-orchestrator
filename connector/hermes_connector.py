#!/usr/bin/env python3
"""Connecteur Hermes ↔ Multi-Agent Orchestrator.

Implémente le protocole de ``docs/AGENT_CONNECTION.md`` côté agent :

- ``enroll``     : enregistrement avec la clé d'enregistrement, conservation de
                   l'identité attribuée par le serveur et du jeton individuel ;
- ``run``        : boucle heartbeat + récupération des tâches + transmission du
                   résultat, avec backoff et protection contre les doublons ;
- ``status``     : affichage de l'identité locale (jamais du jeton entier) ;
- ``disconnect`` : déconnexion logique demandée au serveur.

Principes de sécurité appliqués :

1. **Le jeton n'est jamais affiché** : seul son préfixe et son empreinte le sont.
2. **Vérification TLS toujours active** : aucune option ne permet de la désactiver.
3. **Aucune exécution implicite** : sans ``--commande``, le connecteur refuse
   Deux exécuteurs sont possibles, au choix : ``--commande`` (un exécuteur
   local explicite) ou ``--hermes`` (la CLI Hermes locale traite l'énoncé de
   la tâche et sa réponse devient le résultat).
   d'exécuter le contenu d'une tâche et la marque en échec avec un motif clair.
   Le contenu d'une tâche n'est jamais interprété comme une instruction système.
4. **Reprise après interruption** : le résultat est renvoyé de façon idempotente ;
   une tâche déjà terminée n'est jamais rejouée.

Dépendances : bibliothèque standard uniquement.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

VERSION = "1.0.0"
DELAI_DEFAUT = 30.0
BACKOFF_MAX = 300.0
TAILLE_MAX_RESULTAT = 65_536


# --------------------------------------------------------------------- utilitaires
class ErreurConnecteur(RuntimeError):
    """Erreur fonctionnelle du connecteur."""


def journal(niveau: str, message: str) -> None:
    """Journal structuré sur la sortie standard (repris par journald)."""
    print(
        json.dumps(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "niveau": niveau,
                "composant": "hermes-connector",
                "message": message,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def chemin_etat(argument: str | None) -> Path:
    if argument:
        return Path(argument).expanduser()
    base = Path(os.environ.get("ORCHESTRATOR_STATE_DIR", Path.home() / ".orchestrator-agent"))
    return base / "state.json"


def charger_etat(chemin: Path) -> dict:
    if not chemin.is_file():
        return {}
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as erreur:
        raise ErreurConnecteur(f"état local illisible ({chemin}) : {erreur}") from erreur


def enregistrer_etat(chemin: Path, etat: dict) -> None:
    """Écrit l'état local en 0600 : il contient un jeton."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_suffix(".tmp")
    temporaire.write_text(json.dumps(etat, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(temporaire, stat.S_IRUSR | stat.S_IWUSR)
    temporaire.replace(chemin)


def empreinte(valeur: str) -> str:
    import hashlib

    return hashlib.sha256(valeur.encode("utf-8")).hexdigest()[:12]


class Client:
    """Client HTTP minimal, avec vérification TLS systématique."""

    def __init__(self, base: str, jeton: str | None = None, timeout: float = 30.0) -> None:
        self.base = base.rstrip("/")
        self.jeton = jeton
        self.timeout = timeout

    def requete(self, methode: str, chemin: str, corps: dict | None = None) -> tuple[int, dict | list | None]:
        url = f"{self.base}{chemin}"
        donnees = json.dumps(corps).encode("utf-8") if corps is not None else None
        requete = urllib.request.Request(url, data=donnees, method=methode)
        requete.add_header("Accept", "application/json")
        if donnees is not None:
            requete.add_header("Content-Type", "application/json")
        if self.jeton:
            requete.add_header("Authorization", f"Bearer {self.jeton}")
        try:
            # Vérification TLS active : jamais désactivée, même pour les tests.
            with urllib.request.urlopen(requete, timeout=self.timeout) as reponse:
                brut = reponse.read().decode("utf-8")
                return reponse.status, (json.loads(brut) if brut else None)
        except urllib.error.HTTPError as erreur:
            brut = erreur.read().decode("utf-8", errors="replace")
            try:
                charge = json.loads(brut) if brut else None
            except ValueError:
                charge = {"error": {"code": "REPONSE_ILLISIBLE", "message": brut[:200]}}
            return erreur.code, charge
        except urllib.error.URLError as erreur:
            raise ErreurConnecteur(f"orchestrateur injoignable : {erreur.reason}") from erreur


def code_erreur(charge: dict | list | None) -> str:
    if isinstance(charge, dict) and isinstance(charge.get("error"), dict):
        return str(charge["error"].get("code", "ERREUR"))
    return "ERREUR"


def message_erreur(charge: dict | list | None) -> str:
    if isinstance(charge, dict) and isinstance(charge.get("error"), dict):
        return str(charge["error"].get("message", ""))
    return ""


# --------------------------------------------------------------------- commandes
def identite_locale() -> dict:
    """Identité locale stable du poste (sert à empêcher les doublons côté serveur)."""
    return {
        "hote": platform.node(),
        "systeme": f"{platform.system()}-{platform.release()}",
        "machine": platform.machine(),
    }


def identifiant_instance() -> str:
    """Identifiant d'instance stable, dérivé de l'identité locale du poste."""
    identite = identite_locale()
    brut = f"{identite['hote']}|{identite['systeme']}|{identite['machine']}"
    return f"hermes-{uuid.uuid5(uuid.NAMESPACE_DNS, brut).hex[:16]}"


def enregistrer(args) -> dict:
    chemin = chemin_etat(args.state)
    etat = charger_etat(chemin)
    if etat.get("access_token") and not args.force:
        raise ErreurConnecteur(
            "un jeton existe déjà pour ce poste ; utilisez --force pour réenregistrer"
        )
    if not args.enrollment_key:
        raise ErreurConnecteur(
            "clé d'enregistrement requise (--enrollment-key ou ORCHESTRATOR_ENROLLMENT_KEY)"
        )

    instance = identifiant_instance()
    corps = {
        "runtime": args.runtime,
        "client_instance_id": instance,
        "requested_name": args.name,
        "declared_role": args.role,
        "capabilities": args.capability or ["heartbeat", "tasks"],
        "version": VERSION,
    }
    client = Client(args.url, jeton=args.enrollment_key, timeout=args.timeout)
    statut, charge = client.requete("POST", "/api/v1/agents/enroll", corps)

    if statut == 201 and isinstance(charge, dict):
        etat = {
            "agent_id": charge["agent_id"],
            "name": charge.get("name", args.name),
            "role": charge.get("role", args.role),
            # ``runtime`` n'est pas repris dans la réponse d'enregistrement
            # documentée : on retombe sur la valeur demandée localement.
            "runtime": charge.get("runtime", args.runtime),
            "access_token": charge["access_token"],
            "client_instance_id": instance,
            "url": args.url,
            "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "identite_locale": identite_locale(),
            "traitees": [],
        }
        enregistrer_etat(chemin, etat)
        journal("info", f"agent enregistré : {etat['agent_id']} (jeton {empreinte(etat['access_token'])})")
        return etat

    if statut == 409 and code_erreur(charge) == "AGENT_ALREADY_ENROLLED":
        raise ErreurConnecteur(
            "un agent actif existe déjà pour cette identité locale ; réutilisez le jeton "
            "conservé ou révoquez l'agent avant de réenregistrer "
            f"({message_erreur(charge)})"
        )
    raise ErreurConnecteur(f"enregistrement refusé ({statut}) : {message_erreur(charge) or charge}")


def exiger_jeton(args) -> dict:
    etat = charger_etat(chemin_etat(args.state))
    if not etat.get("access_token"):
        raise ErreurConnecteur("aucun jeton local : lancez d'abord « enroll »")
    return etat


def battre(etat: dict, args) -> dict:
    """Envoie un heartbeat et renvoie la réponse du serveur."""
    client = Client(args.url or etat["url"], jeton=etat["access_token"], timeout=args.timeout)
    statut, charge = client.requete(
        "POST",
        "/api/v1/agents/heartbeat",
        {"status": "ONLINE", "runtime_status": "idle", "version": VERSION},
    )
    if statut == 200 and isinstance(charge, dict):
        return charge
    if statut == 401:
        raise ErreurConnecteur(f"jeton refusé ({code_erreur(charge)}) : réenregistrement requis")
    if statut == 403:
        raise ErreurConnecteur(f"agent révoqué ou désactivé : {message_erreur(charge)}")
    raise ErreurConnecteur(f"heartbeat refusé ({statut}) : {message_erreur(charge)}")


def _cli_hermes() -> str | None:
    """Localise la CLI Hermes utilisée pour exécuter les tâches."""
    return shutil.which("hermes")


def executer_avec_hermes(tache: dict, args) -> tuple[str, dict]:
    """Exécute une tâche en confiant son énoncé à la CLI Hermes.

    C'est l'intégration réelle avec le runtime : le connecteur ne décide pas de ce
    qu'il faut faire, il transmet l'énoncé de la tâche à Hermes en mode ponctuel et
    renvoie la réponse obtenue au serveur comme résultat de la tâche.
    """
    binaire = args.hermes_binaire or _cli_hermes()
    if not binaire:
        return "FAILED", {
            "erreur": "CLI Hermes introuvable",
            "detail": "Aucune commande « hermes » dans le PATH : passez --hermes-binaire.",
        }

    enonce = (tache.get("description") or "").strip() or (tache.get("title") or "").strip()
    if not enonce:
        return "FAILED", {"erreur": "tâche sans énoncé exploitable"}

    consigne = (
        "Tu exécutes une tâche confiée par un orchestrateur d'agents. Réponds "
        "directement avec le livrable demandé, sans préambule ni question.\n\n"
        f"Tâche : {tache.get('title', '')}\n\n{enonce}"
    )

    try:
        acheve = subprocess.run(
            [binaire, "-z", consigne],
            capture_output=True,
            timeout=args.task_timeout,
        )
    except subprocess.TimeoutExpired:
        return "FAILED", {"erreur": f"délai dépassé ({args.task_timeout}s) avec la CLI Hermes"}
    except OSError as erreur:
        return "FAILED", {"erreur": f"lancement de la CLI Hermes impossible : {erreur}"}

    sortie = (acheve.stdout or b"").decode("utf-8", errors="replace").strip()
    erreur = (acheve.stderr or b"").decode("utf-8", errors="replace").strip()

    if acheve.returncode != 0:
        return "FAILED", {
            "erreur": f"la CLI Hermes a retourné le code {acheve.returncode}",
            "detail": (erreur or sortie)[-8000:],
        }

    return "COMPLETED", {
        "moteur": "hermes-cli",
        "agent": "hermes",
        "resume": sortie[-TAILLE_MAX_RESULTAT:],
    }


def executer_tache(tache: dict, args) -> tuple[str, dict]:
    """Exécute une tâche et renvoie (statut, résultat).

    Sans ``--commande``, le contenu de la tâche n'est jamais exécuté : il est
    refusé explicitement, pour qu'aucune instruction distante ne devienne une
    commande locale par accident.
    """
    if getattr(args, "hermes", False):
        return executer_avec_hermes(tache, args)

    if not args.commande:
        return "FAILED", {
            "erreur": "aucun exécuteur configuré",
            "detail": (
                "Le connecteur n'exécute pas le contenu d'une tâche sans « --commande ». "
                "Fournissez un exécuteur local explicite pour autoriser cette exécution."
            ),
            "tache": {"id": tache.get("id"), "titre": tache.get("title")},
        }

    environnement = dict(os.environ)
    environnement.update(
        {
            "ORCHESTRATOR_TASK_ID": str(tache.get("id", "")),
            "ORCHESTRATOR_TASK_TITLE": str(tache.get("title", "")),
            "ORCHESTRATOR_TASK_DESCRIPTION": str(tache.get("description") or ""),
            "ORCHESTRATOR_AGENT_ID": str(args.agent_id or ""),
        }
    )
    try:
        acheve = subprocess.run(
            args.commande,
            shell=True,
            capture_output=True,
            timeout=args.task_timeout,
            env=environnement,
        )
    except subprocess.TimeoutExpired:
        return "FAILED", {"erreur": f"délai dépassé ({args.task_timeout}s)"}

    sortie = (acheve.stdout or b"").decode("utf-8", errors="replace")
    erreur = (acheve.stderr or b"").decode("utf-8", errors="replace")
    resultat = {
        "code_retour": acheve.returncode,
        "sortie": sortie[-TAILLE_MAX_RESULTAT:],
        "erreur": erreur[-8000:],
    }
    return ("COMPLETED" if acheve.returncode == 0 else "FAILED"), resultat


def traiter_une_tache(tache: dict, etat: dict, args) -> bool:
    """Traite une tâche de bout en bout. Renvoie True si un résultat a été transmis."""
    client = Client(args.url or etat["url"], jeton=etat["access_token"], timeout=args.timeout)
    identifiant = tache["id"]

    if identifiant in etat.get("traitees", []):
        journal("info", f"tâche {identifiant} déjà traitée localement : ignorée")
        return False

    # Prise en charge (idempotente côté serveur).
    statut, charge = client.requete("POST", f"/api/v1/agents/tasks/{identifiant}/ack")
    if statut not in (200, 201):
        journal("avertissement", f"acquittement refusé pour {identifiant} ({statut})")
        return False

    if args.dry_run:
        journal("info", f"[simulation] tâche {identifiant} acquittée, aucun exécuteur appelé")
        return False

    journal("info", f"exécution de la tâche {identifiant}")
    resultat_statut, resultat = executer_tache(tache, args)

    statut, charge = client.requete(
        "POST",
        f"/api/v1/agents/tasks/{identifiant}/result",
        {"status": resultat_statut, "result": resultat},
    )
    if statut != 200:
        journal("erreur", f"transmission du résultat refusée pour {identifiant} ({statut})")
        return False

    etat.setdefault("traitees", []).append(identifiant)
    etat["traitees"] = etat["traitees"][-500:]
    etat["derniere_tache"] = identifiant
    enregistrer_etat(chemin_etat(args.state), etat)
    journal("info", f"tâche {identifiant} terminée avec l'état {resultat_statut}")
    return True


def boucler(args) -> int:
    etat = exiger_jeton(args)
    args.agent_id = etat["agent_id"]
    arret = {"demande": False}

    def _arreter(signal_recu, _frame):
        arret["demande"] = True
        journal("info", f"arrêt demandé (signal {signal_recu}) : fin de la boucle en cours")

    for signe in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signe, _arreter)
        except (ValueError, OSError):
            pass

    journal(
        "info",
        f"boucle démarrée : agent {etat['agent_id']} → {args.url or etat['url']} "
        f"(intervalle {args.interval}s)",
    )
    delai = args.interval
    tours = 0
    while not arret["demande"]:
        if args.tours and tours >= args.tours:
            break
        tours += 1
        try:
            reponse = battre(etat, args)
            journal(
                "info",
                f"heartbeat accepté (agent={reponse.get('agent_status')} "
                f"orchestrateur={reponse.get('orchestrator_status')})",
            )
            if reponse.get("orchestrator_status") != "ONLINE":
                journal("avertissement", "orchestrateur hors ligne : aucune tâche récupérée")
                delai = min(delai * 2, BACKOFF_MAX)
            else:
                delai = args.interval
                statut, charge = Client(
                    args.url or etat["url"], jeton=etat["access_token"], timeout=args.timeout
                ).requete("GET", "/api/v1/agents/tasks")
                if statut == 200 and isinstance(charge, list):
                    for tache in charge:
                        if arret["demande"]:
                            break
                        traiter_une_tache(tache, etat, args)
                elif statut not in (200,):
                    journal("avertissement", f"récupération des tâches refusée ({statut})")
        except ErreurConnecteur as erreur:
            journal("erreur", str(erreur))
            if "jeton refusé" in str(erreur) or "révoqué" in str(erreur):
                return 2
            delai = min(delai * 2, BACKOFF_MAX) + random.uniform(0, 2)
        except Exception as erreur:  # noqa: BLE001 — la boucle ne doit jamais mourir
            journal("erreur", f"erreur inattendue : {type(erreur).__name__}: {erreur}")
            delai = min(delai * 2, BACKOFF_MAX)

        fin = time.time() + delai
        while time.time() < fin and not arret["demande"]:
            time.sleep(min(0.5, max(0.0, fin - time.time())))

    journal("info", "boucle arrêtée proprement")
    return 0


def afficher_statut(args) -> int:
    etat = charger_etat(chemin_etat(args.state))
    if not etat:
        print("Aucun état local : cet agent n'est pas enregistré.")
        return 1
    jeton = etat.get("access_token", "")
    print(json.dumps(
        {
            "agent_id": etat.get("agent_id"),
            "nom": etat.get("name"),
            "role": etat.get("role"),
            "runtime": etat.get("runtime"),
            "orchestrateur": etat.get("url"),
            "enregistre_le": etat.get("enrolled_at"),
            "instantane_du_jeton": f"{jeton[:6]}…{jeton[-4:]}" if jeton else None,
            "empreinte_du_jeton": empreinte(jeton) if jeton else None,
            "taches_traitees": len(etat.get("traitees", [])),
            "derniere_tache": etat.get("derniere_tache"),
        },
        indent=2,
        ensure_ascii=False,
    ))
    return 0


def deconnecter(args) -> int:
    etat = exiger_jeton(args)
    statut, charge = Client(args.url or etat["url"], jeton=etat["access_token"], timeout=args.timeout).requete(
        "POST", "/api/v1/agents/me/disconnect", {"reason": args.reason}
    )
    if statut in (404, 405):
        raise ErreurConnecteur(
            "la route de déconnexion côté agent n'existe pas sur ce serveur ; "
            "la déconnexion doit être demandée par un administrateur authentifié"
        )
    if statut not in (200, 201, 202, 204):
        raise ErreurConnecteur(f"déconnexion refusée ({statut}) : {message_erreur(charge)}")
    journal("info", "déconnexion logique acceptée par le serveur")
    return 0


def oublier(args) -> int:
    """Supprime l'état local (le jeton) — action irréversible, confirmée par l'appelant."""
    chemin = chemin_etat(args.state)
    if not chemin.is_file():
        print("Aucun état local à supprimer.")
        return 0
    chemin.unlink()
    print(f"État local supprimé : {chemin}")
    print("Rappel : le jeton reste valide côté serveur jusqu'à révocation de l'agent.")
    return 0


# ------------------------------------------------------------------------- CLI
def construire_analyseur() -> argparse.ArgumentParser:
    analyseur = argparse.ArgumentParser(
        prog="hermes_connector",
        description="Connecteur Hermes ↔ Multi-Agent Orchestrator",
    )
    analyseur.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sous = analyseur.add_subparsers(dest="commande", required=True)

    def communes(analyseur_fils):
        analyseur_fils.add_argument(
            "--url",
            default=os.environ.get("ORCHESTRATOR_URL", "http://127.0.0.1:8000"),
            help="URL de base de l'orchestrateur",
        )
        analyseur_fils.add_argument("--state", default=None, help="chemin du fichier d'état local")
        analyseur_fils.add_argument("--timeout", type=float, default=30.0)

    enroll = sous.add_parser("enroll", help="enregistrer cet agent auprès de l'orchestrateur")
    communes(enroll)
    enroll.add_argument(
        "--enrollment-key",
        default=os.environ.get("ORCHESTRATOR_ENROLLMENT_KEY"),
        help="clé d'enregistrement (ou variable ORCHESTRATOR_ENROLLMENT_KEY)",
    )
    enroll.add_argument("--name", default=f"Hermes {platform.node()}")
    enroll.add_argument("--role", default="general")
    enroll.add_argument("--runtime", default="hermes")
    enroll.add_argument("--capability", action="append")
    enroll.add_argument("--force", action="store_true", help="remplacer un jeton existant")
    enroll.set_defaults(fonction=enregistrer)

    run = sous.add_parser("run", help="boucle heartbeat + tâches")
    communes(run)
    run.add_argument("--interval", type=float, default=DELAI_DEFAUT)
    run.add_argument("--tours", type=int, default=0, help="0 = illimité (tests : 1 ou 2)")
    run.add_argument("--commande", default=None, help="exécuteur local explicite")
    run.add_argument("--task-timeout", type=float, default=120.0)
    run.add_argument(
        "--hermes",
        action="store_true",
        help="executer les taches avec la CLI Hermes locale (integration du runtime)",
    )
    run.add_argument(
        "--hermes-binaire",
        default=None,
        help="chemin explicite vers la commande hermes si absente du PATH",
    )
    run.add_argument("--dry-run", action="store_true", help="acquitter sans exécuter")
    run.set_defaults(fonction=boucler)

    statut = sous.add_parser("status", help="afficher l'identité locale")
    communes(statut)
    statut.set_defaults(fonction=afficher_statut)

    deco = sous.add_parser("disconnect", help="demander la déconnexion logique")
    communes(deco)
    deco.add_argument("--reason", default="arrêt de l'agent")
    deco.set_defaults(fonction=deconnecter)

    oubli = sous.add_parser("forget", help="supprimer le jeton local")
    communes(oubli)
    oubli.set_defaults(fonction=oublier)
    return analyseur


def main(arguments: list[str] | None = None) -> int:
    args = construire_analyseur().parse_args(arguments)
    try:
        resultat = args.fonction(args)
        # Certaines commandes renvoient une structure (l'état enregistré) et non un
        # code de retour : seul un entier est interprété comme tel.
        return int(resultat) if isinstance(resultat, (int, bool)) else 0
    except ErreurConnecteur as erreur:
        journal("erreur", str(erreur))
        return 1


if __name__ == "__main__":
    sys.exit(main())
