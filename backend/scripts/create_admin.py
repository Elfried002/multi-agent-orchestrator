#!/usr/bin/env python3
"""Création du compte administrateur de l'orchestrateur.

Utilisé par ``deploy/setup.sh`` (étape 8) et utilisable manuellement.

Sécurité de la saisie :

- ``--password-stdin`` lit le mot de passe sur l'entrée standard : il n'apparaît
  donc **jamais** dans la ligne de commande (invisible dans ``ps`` et dans
  l'historique du shell) — INSTALLATION.md §11, SECURITY.md §15 ;
- sans cette option, le mot de passe est demandé en saisie masquée avec
  confirmation ;
- le mot de passe n'est jamais affiché, journalisé ni renvoyé.

Exemples :

    # Non interactif, mot de passe fourni par un tube (usage recommandé en script)
    printf '%s' "$MOT_DE_PASSE" | python scripts/create_admin.py --username admin --password-stdin

    # Interactif, saisie masquée avec confirmation
    python scripts/create_admin.py --username admin

Le script est idempotent : réexécuté avec les mêmes identifiants et le même mot de
passe, il ne crée pas de doublon.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from pathlib import Path

# Permet l'exécution directe depuis le répertoire scripts/ ou depuis backend/.
RACINE = Path(__file__).resolve().parents[1]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from app.audit.audit_logger import configure_logging  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.errors import ErreurAPI  # noqa: E402
from app.database.connection import configure_database, session_scope  # noqa: E402
from app.database.migrations import run_migrations  # noqa: E402
from app.services.admin_service import compter_admins, creer_admin  # noqa: E402
from app.services.token_service import assurer_cle_enregistrement  # noqa: E402
from app.utils.validators import ErreurValidation  # noqa: E402

CODE_SUCCES = 0
CODE_ERREUR_USAGE = 2
CODE_ERREUR_METIER = 3


def _lire_mot_de_passe(depuis_stdin: bool) -> str:
    """Récupère le mot de passe sans jamais l'exposer."""
    if depuis_stdin:
        valeur = sys.stdin.readline().rstrip("\n")
        if not valeur:
            print("Erreur : aucun mot de passe reçu sur l'entrée standard.", file=sys.stderr)
            raise SystemExit(CODE_ERREUR_USAGE)
        return valeur

    if not sys.stdin.isatty():
        print(
            "Erreur : la saisie masquée requiert un terminal. "
            "Utilisez --password-stdin lorsque le script n'est pas interactif.",
            file=sys.stderr,
        )
        raise SystemExit(CODE_ERREUR_USAGE)

    premier = getpass.getpass("Mot de passe administrateur : ")
    second = getpass.getpass("Confirmation du mot de passe : ")
    if premier != second:
        print("Erreur : les deux saisies ne correspondent pas.", file=sys.stderr)
        raise SystemExit(CODE_ERREUR_METIER)
    return premier


def main() -> int:
    analyseur = argparse.ArgumentParser(
        description="Crée le compte administrateur du Multi-Agent Orchestrator."
    )
    analyseur.add_argument("--username", required=True, help="Nom d'utilisateur administrateur")
    analyseur.add_argument(
        "--password-stdin",
        action="store_true",
        help="Lit le mot de passe sur l'entrée standard (recommandé en script)",
    )
    analyseur.add_argument(
        "--database",
        default=None,
        help="Chemin du fichier SQLite (par défaut : configuration de l'application)",
    )
    arguments = analyseur.parse_args()

    configure_logging(level=settings.log_level, vers_fichiers=False)

    chemin = arguments.database or settings.database_file
    moteur = configure_database(chemin)
    run_migrations(moteur)

    mot_de_passe = _lire_mot_de_passe(arguments.password_stdin)

    try:
        with session_scope() as db:
            admin, cree = creer_admin(db, username=arguments.username, password=mot_de_passe)
            assurer_cle_enregistrement(db)
            total = compter_admins(db)
    except ErreurValidation as erreur:
        print(f"Erreur de validation ({erreur.code}) : {erreur.message}", file=sys.stderr)
        return CODE_ERREUR_METIER
    except ErreurAPI as erreur:
        print(f"Erreur ({erreur.code}) : {erreur.message}", file=sys.stderr)
        return CODE_ERREUR_METIER
    finally:
        mot_de_passe = None  # la référence est abandonnée au plus tôt

    if cree:
        print(f"Compte administrateur « {admin.username} » créé (identifiant {admin.id}).")
    else:
        print(
            f"Compte administrateur « {admin.username} » déjà présent avec ce mot de passe : "
            "aucune modification effectuée."
        )
    print(f"Comptes administrateur enregistrés : {total}")
    print(f"Base de données : {chemin}")
    print(
        "Mot de passe : non affiché, non journalisé. "
        "Modifiable ensuite depuis le tableau de bord (Paramètres → compte)."
    )
    return CODE_SUCCES


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(main())
