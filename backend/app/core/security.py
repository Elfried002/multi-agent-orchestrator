"""Primitives cryptographiques du système.

Responsabilités :

- hachage des mots de passe administrateur (**Argon2id**, SECURITY.md §4) ;
- génération et hachage des jetons (agents, sessions, clé d'enregistrement) ;
- chiffrement au repos de la clé d'enregistrement, qui doit rester consultable
  par un administrateur authentifié (API.md §9.3) sans être stockée en clair ;
- expurgation des secrets dans les textes journalisés.

Aucune valeur secrète n'est jamais écrite dans un journal par ce module.
Référence : SECURITY.md §4, §5, §10, §15.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.utils.validators import MARQUEUR_EXPURGE

# Paramètres Argon2id conformes aux recommandations OWASP actuelles
# (19 Mio de mémoire, 2 passes, parallélisme 1).
_HACHEUR = PasswordHasher(
    time_cost=2,
    memory_cost=19456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)

# 32 octets d'entropie pour un jeton agent ou une session : 256 bits.
OCTETS_JETON = 32
# Clé d'enregistrement : 48 octets (384 bits), cf. SECURITY.md §5.1.
OCTETS_CLE_ENREGISTREMENT = 48

_CONTEXTE_CHIFFREMENT = b"multi-agent-orchestrator/enrollment-key/v1"
_SEL_CHIFFREMENT = b"multi-agent-orchestrator/kdf"


# ==================================================== mots de passe ==========
def hash_password(password: str) -> str:
    """Retourne l'empreinte Argon2id du mot de passe (jamais le mot de passe)."""
    return _HACHEUR.hash(password)


def verify_password(hash_: str | None, password: str) -> bool:
    """Vérifie un mot de passe contre son empreinte, sans lever d'exception."""
    if not hash_ or not password:
        return False
    try:
        return _HACHEUR.verify(hash_, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(hash_: str) -> bool:
    """Indique si une empreinte doit être recalculée (paramètres obsolètes)."""
    try:
        return _HACHEUR.check_needs_rehash(hash_)
    except (InvalidHashError, VerificationError):
        return True


# ==================================================== jetons =================
def generate_token(nbytes: int = OCTETS_JETON) -> str:
    """Génère un jeton cryptographiquement sûr (URL-safe)."""
    return secrets.token_urlsafe(nbytes)


def generate_enrollment_key() -> str:
    """Génère une clé d'enregistrement longue et imprévisible."""
    return secrets.token_urlsafe(OCTETS_CLE_ENREGISTREMENT)


def hash_token(raw: str) -> str:
    """Empreinte SHA-256 d'un jeton — seule forme stockée en base.

    Les jetons ont une entropie élevée : un hachage rapide suffit ici, contrairement
    aux mots de passe qui exigent une fonction lente.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def tokens_equal(a: str | None, b: str | None) -> bool:
    """Comparaison à temps constant (protège contre l'analyse temporelle)."""
    if not a or not b:
        return False
    return hmac.compare_digest(a, b)


def verify_token_hash(raw: str, hash_stocke: str | None) -> bool:
    """Vérifie un jeton contre son empreinte stockée."""
    if not raw or not hash_stocke:
        return False
    return tokens_equal(hash_token(raw), hash_stocke)


# ============================== chiffrement de la clé d'enregistrement ======
def _cle_de_chiffrement(secret_key: str) -> bytes:
    """Dérive une clé AES-256 à partir de la clé de signature de l'application."""
    if not secret_key:
        raise RuntimeError("SECRET_KEY est requise pour chiffrer la clé d'enregistrement.")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SEL_CHIFFREMENT,
        info=_CONTEXTE_CHIFFREMENT,
    ).derive(secret_key.encode("utf-8"))


def encrypt_secret(plaintext: str, secret_key: str) -> str:
    """Chiffre un secret (AES-256-GCM) et retourne ``base64(nonce || chiffré)``."""
    if plaintext is None:
        raise ValueError("Aucun secret à chiffrer.")
    nonce = secrets.token_bytes(12)
    chiffre = AESGCM(_cle_de_chiffrement(secret_key)).encrypt(
        nonce, plaintext.encode("utf-8"), None
    )
    return base64.urlsafe_b64encode(nonce + chiffre).decode("ascii")


def decrypt_secret(blob: str | None, secret_key: str) -> str | None:
    """Déchiffre un secret produit par :func:`encrypt_secret`.

    Retourne ``None`` si le contenu est absent ou illisible (par exemple après un
    changement de ``SECRET_KEY``) : l'appelant décide alors de la rotation.
    """
    if not blob:
        return None
    try:
        brut = base64.urlsafe_b64decode(blob.encode("ascii"))
        nonce, chiffre = brut[:12], brut[12:]
        return AESGCM(_cle_de_chiffrement(secret_key)).decrypt(nonce, chiffre, None).decode("utf-8")
    except Exception:  # contenu corrompu ou clé changée
        return None


# ==================================================== jeton anti-CSRF ========
# Protection CSRF par double soumission : le serveur émet un jeton signé, lié à
# l'identifiant de session, que le client renvoie dans l'en-tête ``X-CSRF-Token``
# pour toute requête modifiant l'état (SECURITY.md §4).
_SEL_CSRF = "multi-agent-orchestrator-csrf"


def creer_jeton_csrf(session_id: str, secret_key: str, *, ttl_secondes: int = 86400) -> str:
    """Émet un jeton anti-CSRF signé, valable ``ttl_secondes``."""
    from itsdangerous import URLSafeTimedSerializer

    serie = URLSafeTimedSerializer(secret_key, salt=_SEL_CSRF)
    return serie.dumps({"sid": session_id, "ttl": ttl_secondes})


def verifier_jeton_csrf(
    jeton: str | None, session_id: str, secret_key: str, *, ttl_secondes: int = 86400
) -> bool:
    """Vérifie un jeton anti-CSRF : signature, ancienneté et appartenance à la session."""
    if not jeton or not session_id:
        return False
    from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

    serie = URLSafeTimedSerializer(secret_key, salt=_SEL_CSRF)
    try:
        charge = serie.loads(jeton, max_age=ttl_secondes)
    except (BadSignature, SignatureExpired):
        return False
    return bool(isinstance(charge, dict) and charge.get("sid") == session_id)


# ==================================================== expurgation ============
_MOTIFS_SECRETS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)\b(?:token|secret|api[_-]?key|enrollment[_-]?key)\s*[=:]\s*[^\s,;\"']{6,}"),
    re.compile(r"(?i)\b(?:password|mot_de_passe)\s*[=:]\s*[^\s,;\"']+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def redact_text(texte: str) -> str:
    """Remplace toute apparence de secret dans un texte destiné aux journaux."""
    if not texte:
        return texte
    resultat = texte
    for motif in _MOTIFS_SECRETS:
        resultat = motif.sub(MARQUEUR_EXPURGE, resultat)
    return resultat


def redact_value(valeur: str | None, visibles: int = 4) -> str:
    """Représentation d'un secret montrant au plus ses derniers caractères."""
    if not valeur:
        return ""
    if len(valeur) <= visibles:
        return MARQUEUR_EXPURGE
    return f"{MARQUEUR_EXPURGE}{valeur[-visibles:]}"


def empreinte_courte(valeur: str) -> str:
    """Empreinte courte non réversible, pour tracer un secret dans les journaux."""
    return hashlib.sha256(valeur.encode("utf-8")).hexdigest()[:12]
