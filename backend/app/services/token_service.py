"""Service des jetons : jetons individuels des agents et clé d'enregistrement.

Invariants appliqués :

- un jeton n'existe en clair qu'une seule fois, au moment de sa création ;
- seule son empreinte SHA-256 est persistée (SECURITY.md §5.2) ;
- la clé d'enregistrement est en plus **chiffrée au repos** pour rester consultable
  par un administrateur authentifié (API.md §9.3) ;
- une rotation invalide immédiatement l'ancien secret (§5.3).
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.event_service import generer_identifiant, record_event
from app.core.config import settings
from app.core.errors import erreur_introuvable
from app.core.security import (
    decrypt_secret,
    empreinte_courte,
    encrypt_secret,
    generate_enrollment_key,
    generate_token,
    hash_token,
)
from app.models.event import EventActorType, EventSeverity, EventType
from app.models.token import Token, TokenType
from app.utils.datetime_utils import utcnow

logger = logging.getLogger(__name__)

#: Libellé de la clé d'enregistrement active.
LIBELLE_CLE_ENREGISTREMENT = "clé d'enregistrement active"


# ====================================================== jetons d'agent ======
def creer_jeton_agent(db: Session, agent_id: str, *, label: str | None = None) -> tuple[Token, str]:
    """Crée un jeton individuel pour un agent.

    Retourne le jeton et sa valeur en clair — unique occasion où elle est visible.
    """
    brut = generate_token()
    jeton = Token(
        id=generer_identifiant("tok"),
        token_hash=hash_token(brut),
        token_type=TokenType.AGENT,
        agent_id=agent_id,
        label=(label or "jeton agent")[:64],
        created_at=utcnow(),
        expires_at=None,  # pas d'expiration par défaut : la révocation est explicite
    )
    db.add(jeton)
    db.commit()
    db.refresh(jeton)
    return jeton, brut


def revoquer_jetons_agent(db: Session, agent_id: str, *, motif: str) -> int:
    """Révoque tous les jetons actifs d'un agent. Retourne le nombre révoqué."""
    maintenant = utcnow()
    jetons = (
        db.execute(
            select(Token).where(
                Token.agent_id == agent_id,
                Token.token_type == TokenType.AGENT,
                Token.revoked_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for jeton in jetons:
        jeton.revoked_at = maintenant
        jeton.revoked_reason = motif[:160]
        db.add(jeton)
    if jetons:
        db.commit()
    return len(jetons)


def compter_jetons_actifs(db: Session, agent_id: str | None = None) -> int:
    """Compte les jetons d'agent non révoqués."""
    requete = select(func.count()).select_from(Token).where(
        Token.token_type == TokenType.AGENT, Token.revoked_at.is_(None)
    )
    if agent_id:
        requete = requete.where(Token.agent_id == agent_id)
    return int(db.execute(requete).scalar_one())


def purge_jetons_expires(db: Session) -> int:
    """Marque comme révoqués les jetons expirés non encore révoqués."""
    maintenant = utcnow()
    jetons = (
        db.execute(
            select(Token).where(Token.revoked_at.is_(None), Token.expires_at.is_not(None))
        )
        .scalars()
        .all()
    )
    compte = 0
    for jeton in jetons:
        if jeton.expires_at and jeton.expires_at <= maintenant:
            jeton.revoked_at = maintenant
            jeton.revoked_reason = "jeton expiré"
            db.add(jeton)
            compte += 1
    if compte:
        db.commit()
    return compte


# ================================================ clé d'enregistrement ======
def trouver_cle_enregistrement_active(db: Session) -> Token | None:
    """Retourne la clé d'enregistrement active, s'il en existe une."""
    return db.execute(
        select(Token)
        .where(Token.token_type == TokenType.ENROLLMENT, Token.revoked_at.is_(None))
        .order_by(Token.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def valeur_cle_enregistrement(db: Session) -> tuple[str, Token] | None:
    """Retourne la clé d'enregistrement en clair (réservé aux administrateurs)."""
    cle = trouver_cle_enregistrement_active(db)
    if cle is None:
        return None
    valeur = decrypt_secret(cle.encrypted_value, settings.resolved_secret_key())
    if valeur is None:
        # La clé n'est pas déchiffrable (SECRET_KEY changée) : mieux vaut la
        # régénérer que laisser l'installation bloquée.
        logger.warning(
            "Clé d'enregistrement indéchiffrable (SECRET_KEY modifiée) : rotation automatique."
        )
        cle, nouvelle = _creer_cle(db, motif="rotation automatique (clé indéchiffrable)")
        return nouvelle, cle
    return valeur, cle


def _creer_cle(
    db: Session,
    *,
    motif: str,
    valeur_imposee: str | None = None,
    actor_id: str | None = None,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> tuple[Token, str]:
    """Crée une nouvelle clé d'enregistrement et révoque l'ancienne."""
    maintenant = utcnow()
    precedentes = (
        db.execute(
            select(Token).where(
                Token.token_type == TokenType.ENROLLMENT, Token.revoked_at.is_(None)
            )
        )
        .scalars()
        .all()
    )
    for ancienne in precedentes:
        ancienne.revoked_at = maintenant
        ancienne.revoked_reason = motif[:160]
        db.add(ancienne)

    valeur = valeur_imposee or generate_enrollment_key()
    cle = Token(
        id=generer_identifiant("env"),
        token_hash=hash_token(valeur),
        token_type=TokenType.ENROLLMENT,
        label=LIBELLE_CLE_ENREGISTREMENT,
        created_at=maintenant,
        encrypted_value=encrypt_secret(valeur, settings.resolved_secret_key()),
    )
    db.add(cle)
    db.commit()
    db.refresh(cle)

    record_event(
        db,
        event_type=EventType.ENROLLMENT_KEY_ROTATED if actor_id else EventType.ENROLLMENT_KEY_READ,
        message=(
            "Rotation de la clé d'enregistrement."
            if actor_id
            else "Clé d'enregistrement initialisée."
        ),
        severity=EventSeverity.WARNING if actor_id else EventSeverity.INFO,
        actor_type=EventActorType.ADMIN if actor_id else EventActorType.SYSTEM,
        actor_id=actor_id,
        source_ip=source_ip,
        request_id=request_id,
        object_type="enrollment_key",
        # Seule une empreinte courte est conservée : jamais la valeur de la clé.
        object_id=empreinte_courte(valeur),
        success=True,
        metadata={"motif": motif, "cles_revoquees": len(precedentes)},
    )
    return cle, valeur


def assurer_cle_enregistrement(db: Session) -> Token:
    """Garantit qu'une clé d'enregistrement active existe.

    Si ``ENROLLMENT_KEY`` est fournie par l'environnement et qu'aucune clé active
    n'existe, elle est adoptée ; sinon une clé est générée. Cette fonction est
    idempotente : elle est appelée au démarrage du service.
    """
    existante = trouver_cle_enregistrement_active(db)
    if existante is not None:
        return existante

    imposee = settings.enrollment_key.strip() or None
    cle, _ = _creer_cle(db, motif="initialisation au démarrage", valeur_imposee=imposee)
    logger.info(
        "Clé d'enregistrement %s disponible (consultable dans le tableau de bord).",
        "adoptée depuis l'environnement" if imposee else "générée",
    )
    return cle


def rotation_cle_enregistrement(
    db: Session,
    *,
    actor_id: str,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> tuple[Token, str]:
    """Révoque la clé active et en génère une nouvelle (API.md §9.4)."""
    cle, valeur = _creer_cle(
        db,
        motif="rotation demandée par un administrateur",
        actor_id=actor_id,
        source_ip=source_ip,
        request_id=request_id,
    )
    return cle, valeur


def cle_pour_affichage(
    db: Session,
    *,
    actor_id: str,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> tuple[str, Token]:
    """Retourne la clé active et journalise la consultation (API.md §9.3).

    L'événement ne contient jamais la valeur de la clé : seule une empreinte
    courte permet de vérifier *quelle* clé a été consultée.
    """
    resultat = valeur_cle_enregistrement(db)
    if resultat is None:
        raise erreur_introuvable("Aucune clé d'enregistrement active.")
    valeur, cle = resultat
    record_event(
        db,
        event_type=EventType.ENROLLMENT_KEY_READ,
        message="Consultation de la clé d'enregistrement par un administrateur.",
        severity=EventSeverity.WARNING,
        actor_type=EventActorType.ADMIN,
        actor_id=actor_id,
        source_ip=source_ip,
        request_id=request_id,
        object_type="enrollment_key",
        object_id=empreinte_courte(valeur),
        success=True,
    )
    return valeur, cle
