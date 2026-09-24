"""Configuration de la journalisation structurée et expurgation des secrets.

Deux flux distincts (INSTALLATION.md §6) :

- ``application.log`` : journal technique de l'application ;
- ``audit.log`` : journal d'audit et de sécurité, également persisté en base
  (table ``events``) pour être consultable depuis le tableau de bord.

Un filtre d'expurgation s'applique à **tous** les enregistrements : même si un
appel de journalisation contient par erreur un jeton, il est masqué avant écriture
(SECURITY.md §10). Ce filtre est un filet de sécurité, pas une autorisation d'écrire
des secrets dans les journaux.

Portée : la surveillance décrite ici est **applicative**. Elle ne remplace ni un IDS
ni une capture réseau (SECURITY.md §11).
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.core.security import redact_text

FORMAT_JOURNAL = "%(asctime)s %(levelname)-8s %(name)s [%(request_id)s] %(message)s"
FORMAT_DATE = "%Y-%m-%dT%H:%M:%S"
NOM_LOGGER_AUDIT = "orchestrator.audit"


class FiltreExpurgation(logging.Filter):
    """Retire toute apparence de secret des messages journalisés."""

    def filter(self, enregistrement: logging.LogRecord) -> bool:
        try:
            if isinstance(enregistrement.msg, str):
                enregistrement.msg = redact_text(enregistrement.msg)
            elif isinstance(enregistrement.msg, dict):
                enregistrement.msg = {
                    cle: redact_text(valeur) if isinstance(valeur, str) else valeur
                    for cle, valeur in enregistrement.msg.items()
                }
            if enregistrement.args:
                if isinstance(enregistrement.args, dict):
                    enregistrement.args = {
                        cle: redact_text(valeur) if isinstance(valeur, str) else valeur
                        for cle, valeur in enregistrement.args.items()
                    }
                elif isinstance(enregistrement.args, tuple):
                    enregistrement.args = tuple(
                        redact_text(element) if isinstance(element, str) else element
                        for element in enregistrement.args
                    )
            if enregistrement.exc_text:
                enregistrement.exc_text = redact_text(enregistrement.exc_text)
        except Exception:  # pragma: no cover - le filtrage ne doit jamais casser une requête
            pass
        return True


class FiltreContexteRequete(logging.Filter):
    """Garantit la présence du champ ``request_id`` dans chaque enregistrement."""

    def filter(self, enregistrement: logging.LogRecord) -> bool:
        if not hasattr(enregistrement, "request_id"):
            enregistrement.request_id = "-"
        return True


def configure_logging(
    *,
    level: str = "INFO",
    dossier_journaux: Path | str | None = None,
    vers_fichiers: bool = True,
) -> dict[str, Path]:
    """Configure la journalisation de l'application.

    :param dossier_journaux: répertoire des journaux ; ``None`` en développement
        (écriture sur la sortie standard uniquement).
    :param vers_fichiers: écrire ``application.log`` et ``audit.log``.
    :return: chemins des journaux créés (vide si écriture console seulement).
    """
    niveau = getattr(logging, level.upper(), logging.INFO)
    racine = logging.getLogger()
    racine.setLevel(niveau)

    for gestionnaire in list(racine.handlers):
        racine.removeHandler(gestionnaire)

    formateur = logging.Formatter(FORMAT_JOURNAL, datefmt=FORMAT_DATE)
    formateur.converter = lambda *args: datetime.now(timezone.utc).timetuple()

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(formateur)
    console.addFilter(FiltreExpurgation())
    console.addFilter(FiltreContexteRequete())
    racine.addHandler(console)

    chemins: dict[str, Path] = {}
    if vers_fichiers and dossier_journaux:
        dossier = Path(dossier_journaux)
        dossier.mkdir(parents=True, exist_ok=True)

        fichier_application = dossier / "application.log"
        gestionnaire_app = logging.handlers.RotatingFileHandler(
            fichier_application, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        gestionnaire_app.setFormatter(formateur)
        gestionnaire_app.addFilter(FiltreExpurgation())
        gestionnaire_app.addFilter(FiltreContexteRequete())
        racine.addHandler(gestionnaire_app)
        chemins["application"] = fichier_application

        fichier_audit = dossier / "audit.log"
        gestionnaire_audit = logging.handlers.RotatingFileHandler(
            fichier_audit, maxBytes=5 * 1024 * 1024, backupCount=10, encoding="utf-8"
        )
        gestionnaire_audit.setFormatter(formateur)
        gestionnaire_audit.addFilter(FiltreExpurgation())
        gestionnaire_audit.addFilter(FiltreContexteRequete())
        logger_audit = logging.getLogger(NOM_LOGGER_AUDIT)
        logger_audit.setLevel(niveau)
        logger_audit.addHandler(gestionnaire_audit)
        logger_audit.propagate = True
        chemins["audit"] = fichier_audit
    else:
        # Sans fichiers, le logger d'audit doit tout de même se propager.
        logging.getLogger(NOM_LOGGER_AUDIT).propagate = True

    # Les journaux des bibliothèques tierces sont bridés : ils n'apportent rien ici
    # et peuvent contenir des en-têtes de requête.
    for bruyant in ("uvicorn.access", "sqlalchemy.engine", "multipart"):
        logging.getLogger(bruyant).setLevel(logging.WARNING)

    return chemins


def audit_logger() -> logging.Logger:
    """Logger dédié aux événements d'audit et de sécurité."""
    return logging.getLogger(NOM_LOGGER_AUDIT)
