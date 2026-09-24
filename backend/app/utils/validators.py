"""Validation des entrées et expurgation des secrets.

Ces contrôles sont appliqués **côté serveur** : la validation du frontend ne
remplace jamais celle du backend (SECURITY.md §3.4).

Deux responsabilités :

1. Valider les valeurs reçues (mot de passe, nom d'utilisateur, identifiants).
2. Retirer les secrets des structures avant persistance ou journalisation
   (SECURITY.md §10 : ne jamais journaliser mots de passe, jetons, clés
   d'enregistrement, cookies ni en-têtes ``Authorization``).

Référence : SECURITY.md §3.4, §10, §15 ; API.md §11.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

# --- Politique de mot de passe ------------------------------------------------
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
PASSWORD_CLASSES_MINIMUM = 3  # minuscule, majuscule, chiffre, caractère spécial

# Mots de passe manifestement faibles ou présents dans toutes les listes de
# compromission. La comparaison est faite en minuscules.
MOTS_DE_PASSE_INTERDITS = frozenset(
    {
        "password", "password1", "password123", "passw0rd", "motdepasse",
        "motdepasse123", "123456789", "1234567890", "12345678", "azerty",
        "azerty123", "qwerty", "qwerty123", "admin", "admin123", "administrator",
        "letmein", "welcome", "welcome1", "changeme", "changeme123", "iloveyou",
        "monkey123", "dragon123", "football", "baseball", "sunshine",
        "orchestrator", "orchestrateur", "multiagent", "multi-agent",
        "password1234", "p@ssw0rd", "passw0rd123", "testtest", "test1234",
    }
)

MOTIF_NOM_UTILISATEUR = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}$")
MOTIF_INSTANCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
MOTIF_CARACTERES_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ErreurValidation(ValueError):
    """Entrée invalide — convertie en réponse 422 par la couche API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ============================================================ validateurs ====
def validate_password(password: str) -> str:
    """Vérifie la robustesse d'un mot de passe et le retourne inchangé."""
    if not isinstance(password, str) or not password:
        raise ErreurValidation("PASSWORD_REQUIRED", "Le mot de passe est obligatoire.")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ErreurValidation(
            "PASSWORD_TOO_SHORT",
            f"Le mot de passe doit contenir au moins {PASSWORD_MIN_LENGTH} caractères.",
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ErreurValidation(
            "PASSWORD_TOO_LONG",
            f"Le mot de passe ne doit pas dépasser {PASSWORD_MAX_LENGTH} caractères.",
        )
    if password.strip().lower() in MOTS_DE_PASSE_INTERDITS:
        raise ErreurValidation(
            "PASSWORD_TOO_COMMON",
            "Ce mot de passe figure parmi les plus courants et ne peut pas être utilisé.",
        )

    classes = 0
    if any(c.islower() for c in password):
        classes += 1
    if any(c.isupper() for c in password):
        classes += 1
    if any(c.isdigit() for c in password):
        classes += 1
    if any(not c.isalnum() for c in password):
        classes += 1
    if classes < PASSWORD_CLASSES_MINIMUM:
        raise ErreurValidation(
            "PASSWORD_TOO_SIMPLE",
            "Le mot de passe doit mélanger au moins trois catégories parmi : "
            "minuscules, majuscules, chiffres, caractères spéciaux.",
        )
    if len(set(password)) < 6:
        raise ErreurValidation(
            "PASSWORD_TOO_REPETITIVE",
            "Le mot de passe comporte trop de caractères identiques.",
        )
    return password


def validate_username(username: str) -> str:
    """Vérifie le format d'un nom d'utilisateur administrateur."""
    if not isinstance(username, str) or not username.strip():
        raise ErreurValidation("USERNAME_REQUIRED", "Le nom d'utilisateur est obligatoire.")
    nom = username.strip()
    if not MOTIF_NOM_UTILISATEUR.match(nom):
        raise ErreurValidation(
            "USERNAME_INVALID",
            "Le nom d'utilisateur doit faire de 3 à 64 caractères et ne contenir que "
            "des lettres, des chiffres, des points, des tirets et des soulignés.",
        )
    return nom


def validate_client_instance_id(value: str) -> str:
    """Identifiant local stable du connecteur (AGENT_CONNECTION.md §4).

    Ce n'est **pas** une identité de confiance : il sert uniquement à empêcher la
    création de doublons lors des reconnexions.
    """
    if not isinstance(value, str) or not value.strip():
        raise ErreurValidation(
            "CLIENT_INSTANCE_REQUIRED",
            "L'identifiant d'instance locale est obligatoire.",
        )
    identifiant = value.strip()
    if not MOTIF_INSTANCE.match(identifiant):
        raise ErreurValidation(
            "CLIENT_INSTANCE_INVALID",
            "L'identifiant d'instance doit faire de 8 à 128 caractères "
            "(lettres, chiffres, points, deux-points, tirets et soulignés).",
        )
    return identifiant


def validate_role(role: str | None, roles_autorises: tuple[str, ...], defaut: str) -> str:
    """Ramène un rôle déclaré dans la liste autorisée.

    Un agent ne choisit jamais librement son rôle : une valeur hors liste est
    remplacée par le rôle par défaut (ARCHITECTURE.md §2.1).
    """
    if not role or not isinstance(role, str):
        return defaut
    candidat = role.strip().lower()
    return candidat if candidat in roles_autorises else defaut


def sanitize_text(value: Any, max_length: int = 2000, *, defaut: str = "") -> str:
    """Nettoie un texte libre : caractères de contrôle retirés, taille bornée."""
    if value is None:
        return defaut
    texte = value if isinstance(value, str) else str(value)
    texte = unicodedata.normalize("NFC", texte)
    texte = MOTIF_CARACTERES_CONTROLE.sub("", texte)
    texte = texte.replace("\r\n", "\n").replace("\r", "\n")
    texte = texte.strip()
    if len(texte) > max_length:
        texte = texte[: max_length - 1].rstrip() + "…"
    return texte


def validate_capabilities(capacites: Any, max_items: int = 32, max_length: int = 64) -> list[str]:
    """Valide la liste de capacités déclarées par un agent.

    Les capacités sont des **déclarations** : elles ne constituent jamais une preuve
    de confiance et n'accordent aucun privilège (ARCHITECTURE.md §7.2,
    SECURITY.md §7).
    """
    if capacites is None:
        return []
    if not isinstance(capacites, (list, tuple)):
        raise ErreurValidation("CAPABILITIES_INVALID", "Les capacités doivent être une liste.")
    resultat: list[str] = []
    for element in capacites:
        if not isinstance(element, str):
            raise ErreurValidation(
                "CAPABILITY_INVALID", "Chaque capacité doit être une chaîne de caractères."
            )
        valeur = sanitize_text(element, max_length=max_length).lower()
        if not valeur:
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9._ -]{0,63}", valeur):
            raise ErreurValidation(
                "CAPABILITY_INVALID",
                f"Capacité refusée (format invalide) : {valeur[:32]!r}.",
            )
        if valeur not in resultat:
            resultat.append(valeur)
    if len(resultat) > max_items:
        raise ErreurValidation(
            "CAPABILITIES_TOO_MANY", f"Au maximum {max_items} capacités sont acceptées."
        )
    return resultat


# =================================================== expurgation des secrets ==
# Clés dont la valeur ne doit jamais être persistée dans un journal ou un
# résultat (SECURITY.md §10).
CLES_SENSIBLES = frozenset(
    {
        "password", "passwd", "mot_de_passe", "motdepasse", "current_password",
        "new_password", "password_hash", "token", "access_token", "token_hash",
        "agent_token", "bearer", "authorization", "auth", "cookie", "cookies",
        "session", "session_id", "secret", "secret_key", "enrollment_key",
        "registration_key", "api_key", "apikey", "private_key", "credential",
        "credentials", "csrf", "csrf_token", "x_csrf_token", "set_cookie",
    }
)
MARQUEUR_EXPURGE = "[EXPURGÉ]"


def strip_sensitive_keys(payload: Any, *, profondeur: int = 0) -> Any:
    """Retourne une copie d'une structure où toute clé sensible est expurgée.

    Utilisé avant d'écrire un événement d'audit, un journal ou un résultat d'agent.
    """
    if profondeur > 6:
        return MARQUEUR_EXPURGE
    if isinstance(payload, dict):
        resultat: dict[str, Any] = {}
        for cle, valeur in payload.items():
            nom = str(cle).lower().replace("-", "_")
            if nom in CLES_SENSIBLES or any(motif in nom for motif in ("password", "secret", "token", "cookie", "authorization")):
                resultat[str(cle)] = MARQUEUR_EXPURGE
            else:
                resultat[str(cle)] = strip_sensitive_keys(valeur, profondeur=profondeur + 1)
        return resultat
    if isinstance(payload, (list, tuple)):
        return [strip_sensitive_keys(element, profondeur=profondeur + 1) for element in payload]
    return payload


def assert_no_secrets(payload: Any) -> None:
    """Lève une erreur si une valeur ressemble à un secret en clair.

    Contrôle de dernier recours avant persistance d'un résultat ou d'un événement.
    """
    for motif, etiquette in (
        (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"), "en-tête Authorization"),
        (re.compile(r"eyJ[A-Za-z0-9._\-]{20,}"), "jeton encodé"),
        (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "clé privée"),
    ):
        texte = json.dumps(payload, ensure_ascii=False, default=str)
        if motif.search(texte):
            raise ErreurValidation(
                "PAYLOAD_CONTAINS_SECRET",
                f"Le contenu transmis semble contenir un secret ({etiquette}) et a été refusé.",
            )


def serialiser_json(valeur: Any, max_bytes: int | None = None) -> str:
    """Sérialise en JSON de façon sûre, avec contrôle de taille."""
    try:
        texte = json.dumps(valeur, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError) as erreur:
        raise ErreurValidation("PAYLOAD_NOT_SERIALIZABLE", "Le contenu n'est pas sérialisable.") from erreur
    taille = len(texte.encode("utf-8"))
    if max_bytes is not None and taille > max_bytes:
        raise ErreurValidation(
            "PAYLOAD_TOO_LARGE",
            f"Le contenu dépasse la taille maximale autorisée ({max_bytes} octets).",
        )
    return texte


def deserialiser_json(texte: str | None, defaut: Any = None) -> Any:
    """Analyse un JSON persisté, tolérant aux valeurs vides ou corrompues."""
    if not texte:
        return defaut
    try:
        return json.loads(texte)
    except (TypeError, ValueError):
        return defaut
