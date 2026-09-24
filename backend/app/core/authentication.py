"""Authentification et autorisation — dépendances appliquées côté backend.

Ce module est la seule porte d'entrée des identités :

- **administrateur** : session serveur portée par un cookie ``HttpOnly``, jeton
  stocké uniquement sous forme d'empreinte, expirable, à inactivité limitée et
  révocable ; protection CSRF par double soumission sur les requêtes modifiantes ;
- **agent** : jeton individuel ``Bearer``, haché en base, vérifié à chaque requête,
  révocable immédiatement ;
- **clé d'enregistrement** : autorise uniquement l'opération d'enregistrement.

Toute autorisation est vérifiée ici : le frontend n'est jamais une frontière de
sécurité (ARCHITECTURE.md §3.2, SECURITY.md §6).

Référence : API.md §3, SECURITY.md §4, §5, §6, §8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.event_service import generer_identifiant, record_event
from app.core.config import settings
from app.core.errors import (
    ErreurAPI,
    erreur_acces_refuse,
    erreur_authentification,
    erreur_indisponible,
    erreur_introuvable,
)
from app.core.security import (
    creer_jeton_csrf,
    generate_token,
    hash_token,
    tokens_equal,
    verifier_jeton_csrf,
)
from app.core.state import ETAT_EN_LIGNE, get_desired_state
from app.database.connection import get_db
from app.models.admin import Admin
from app.models.agent import Agent, AgentStatus
from app.models.event import EventActorType, EventSeverity, EventType
from app.models.session import AdminSession
from app.models.token import Token, TokenType
from app.utils.datetime_utils import age_seconds, utcnow

logger = logging.getLogger(__name__)

#: Fréquence minimale d'actualisation de ``last_seen_at`` d'une session, en secondes.
#: Évite une écriture en base à chaque requête tout en conservant un suivi fidèle.
INTERVALLE_MAJ_SESSION = 60

#: Méthodes considérées comme modifiant l'état (soumises au contrôle CSRF).
METHODES_MUTANTES = ("POST", "PUT", "PATCH", "DELETE")


# ============================================================ contexte =======
def client_ip(request: Request) -> str:
    """Adresse IP observée de l'appelant.

    ``X-Forwarded-For`` n'est pris en compte que si la connexion provient
    directement d'un proxy déclaré de confiance (SECURITY.md §8) : sinon un client
    pourrait falsifier son adresse dans les journaux.
    """
    pairs = getattr(request, "client", None)
    adresse_directe = pairs.host if pairs else "inconnue"
    proxys = settings.trusted_proxies
    entete = request.headers.get("x-forwarded-for")
    if entete and proxys and adresse_directe in proxys:
        premier = entete.split(",")[0].strip()
        if premier:
            return premier[:45]
    return adresse_directe[:45]


def user_agent(request: Request) -> str | None:
    valeur = request.headers.get("user-agent")
    return valeur[:255] if valeur else None


def request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _extraire_bearer(request: Request) -> str | None:
    """Extrait le jeton d'un en-tête ``Authorization: Bearer ...``."""
    entete = request.headers.get("authorization") or ""
    if not entete.lower().startswith("bearer "):
        return None
    jeton = entete[7:].strip()
    return jeton or None


# ===================================================== sessions admin =======
@dataclass(frozen=True)
class ContexteAdmin:
    """Administrateur authentifié et sa session courante."""

    admin: Admin
    session: AdminSession

    @property
    def admin_id(self) -> str:
        return self.admin.id

    @property
    def session_id(self) -> str:
        return self.session.id


def creer_session(
    db: Session,
    admin: Admin,
    *,
    source_ip: str | None,
    user_agent_: str | None,
) -> tuple[AdminSession, str, str]:
    """Crée une session serveur et retourne ``(session, jeton, jeton_csrf)``.

    Le jeton en clair n'est retourné qu'ici : seule son empreinte est persistée.
    """
    maintenant = utcnow()
    jeton = generate_token()
    session = AdminSession(
        id=generer_identifiant("ses"),
        admin_id=admin.id,
        token_hash=hash_token(jeton),
        created_at=maintenant,
        expires_at=maintenant + settings.session_ttl,
        last_seen_at=maintenant,
        source_ip=source_ip,
        user_agent=user_agent_,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    jeton_csrf = creer_jeton_csrf(session.id, settings.resolved_secret_key())
    return session, jeton, jeton_csrf


def _session_depuis_cookie(db: Session, request: Request) -> AdminSession | None:
    """Charge et valide la session portée par le cookie de session."""
    brut = request.cookies.get(settings.cookie_name)
    if not brut:
        return None
    empreinte = hash_token(brut)
    session = db.execute(
        select(AdminSession).where(AdminSession.token_hash == empreinte)
    ).scalar_one_or_none()
    if session is None:
        return None

    maintenant = utcnow()
    if session.revoked_at is not None:
        return None
    if session.expires_at and session.expires_at <= maintenant:
        # Une session arrivée à échéance est révoquée : le cookie rejoué ne doit
        # plus pouvoir être présenté comme valide.
        session.revoked_at = maintenant
        session.revoked_reason = "échéance atteinte"
        db.add(session)
        db.commit()
        record_event(
            db,
            event_type=EventType.ADMIN_SESSION_REVOKED,
            message="Session administrateur arrivée à échéance : révoquée.",
            severity=EventSeverity.INFO,
            actor_type=EventActorType.SYSTEM,
            actor_id=session.admin_id,
            source_ip=session.source_ip,
            object_type="session",
            object_id=session.id,
            success=True,
        )
        return None
    # Expiration par inactivité : une session laissée ouverte se ferme seule.
    inactivite = age_seconds(session.last_seen_at or session.created_at, maintenant) or 0
    if inactivite > settings.session_idle_timeout.total_seconds():
        session.revoked_at = maintenant
        session.revoked_reason = "expiration par inactivité"
        db.add(session)
        db.commit()
        record_event(
            db,
            event_type=EventType.ADMIN_SESSION_REVOKED,
            message="Session administrateur fermée pour inactivité.",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.SYSTEM,
            actor_id=session.admin_id,
            source_ip=session.source_ip,
            object_type="session",
            object_id=session.id,
            success=True,
        )
        return None

    # Actualisation de la dernière activité (au plus une fois par minute).
    if age_seconds(session.last_seen_at, maintenant) is None or (
        age_seconds(session.last_seen_at, maintenant) or 0
    ) > INTERVALLE_MAJ_SESSION:
        session.last_seen_at = maintenant
        db.add(session)
        db.commit()
    return session


def administrateur_courant(db: Session, request: Request) -> ContexteAdmin | None:
    """Contexte administrateur si la session est valide, sinon ``None``."""
    session = _session_depuis_cookie(db, request)
    if session is None:
        return None
    admin = db.get(Admin, session.admin_id)
    if admin is None or not admin.is_active:
        return None
    return ContexteAdmin(admin=admin, session=session)


def require_admin(
    request: Request,
    db: Session = Depends(get_db),
) -> ContexteAdmin:
    """Exige une session administrateur valide (API.md §7, §8, §9, §10)."""
    contexte = administrateur_courant(db, request)
    if contexte is None:
        raise erreur_authentification(
            "Session administrateur absente ou expirée. Veuillez vous reconnecter."
        )
    return contexte


def _origine_autorisee(request: Request, origine: str) -> bool:
    """Indique si une origine est acceptable pour une requête modifiante.

    Deux cas seulement sont acceptés :

    - l'origine figure dans la liste déclarée en configuration ;
    - l'origine correspond exactement à l'hôte de la requête (déploiement
      même-origine derrière Nginx, sans liste à maintenir).

    Une origine ``null`` (document local ou iframe isolée) est toujours refusée.
    """
    nettoyee = origine.strip().rstrip("/").lower()
    if not nettoyee or nettoyee == "null":
        return False

    autorisees = {o.strip().rstrip("/").lower() for o in settings.allowed_origins if o.strip()}
    if nettoyee in autorisees:
        return True

    hote = (request.headers.get("host") or "").strip().lower()
    if not hote:
        return False
    return urlparse(nettoyee).netloc == hote


def require_admin_csrf(
    request: Request,
    db: Session = Depends(get_db),
) -> ContexteAdmin:
    """Exige une session valide **et** un jeton anti-CSRF correct.

    Utilisé par toutes les routes administrateur qui modifient l'état.
    """
    contexte = require_admin(request, db)
    if request.method.upper() not in METHODES_MUTANTES:
        return contexte

    jeton = request.headers.get("x-csrf-token")
    if not verifier_jeton_csrf(jeton, contexte.session_id, settings.resolved_secret_key()):
        record_event(
            db,
            event_type=EventType.CSRF_REJECTED,
            message="Requête modifiante refusée : jeton anti-CSRF absent ou invalide.",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.ADMIN,
            actor_id=contexte.admin_id,
            source_ip=client_ip(request),
            request_id=request_id(request),
            object_type="endpoint",
            object_id=request.url.path,
            success=False,
        )
        raise ErreurAPI(
            "CSRF_TOKEN_INVALID",
            "Jeton anti-CSRF absent ou invalide : rechargez la page et réessayez.",
            status_code=403,
        )

    # Contrôle d'origine : complémentaire au jeton anti-CSRF. Une origine
    # étrangère est refusée même lorsque aucune liste d'origines n'est déclarée
    # (déploiement même-origine via Nginx).
    origine = request.headers.get("origin")
    if origine and not _origine_autorisee(request, origine):
        record_event(
            db,
            event_type=EventType.CSRF_REJECTED,
            message=f"Origine refusée pour une requête modifiante : {origine[:80]}",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.ADMIN,
            actor_id=contexte.admin_id,
            source_ip=client_ip(request),
            request_id=request_id(request),
            success=False,
        )
        raise erreur_acces_refuse("Origine non autorisée pour cette opération.")
    return contexte


def revoquer_session(db: Session, session: AdminSession, *, motif: str) -> None:
    """Révoque une session (déconnexion ou changement de mot de passe)."""
    if session.revoked_at is not None:
        return
    session.revoked_at = utcnow()
    session.revoked_reason = motif[:120]
    db.add(session)
    db.commit()


def revoquer_toutes_sessions(
    db: Session, admin_id: str, *, motif: str, sauf: str | None = None
) -> int:
    """Révoque les sessions actives d'un administrateur.

    Retourne le nombre de sessions fermées. ``sauf`` permet de conserver la session
    courante (cas du changement de mot de passe).
    """
    maintenant = utcnow()
    sessions = (
        db.execute(
            select(AdminSession).where(
                AdminSession.admin_id == admin_id,
                AdminSession.revoked_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    compte = 0
    for session in sessions:
        if sauf and session.id == sauf:
            continue
        session.revoked_at = maintenant
        session.revoked_reason = motif[:120]
        db.add(session)
        compte += 1
    if compte:
        db.commit()
    return compte


# ============================================================ agents ========
def _charger_jeton_agent(db: Session, jeton_brut: str) -> tuple[Token | None, bool]:
    """Retourne ``(jeton, etait_revoque)``."""
    empreinte = hash_token(jeton_brut)
    jeton = db.execute(select(Token).where(Token.token_hash == empreinte)).scalar_one_or_none()
    if jeton is None:
        return None, False
    return jeton, jeton.revoked_at is not None


def require_agent(
    request: Request,
    db: Session = Depends(get_db),
) -> Agent:
    """Exige un jeton d'agent valide et non révoqué (API.md §3.2).

    Toute anomalie est journalisée : jeton inconnu, expiré, révoqué ou agent
    désactivé.
    """
    jeton_brut = _extraire_bearer(request)
    if not jeton_brut:
        record_event(
            db,
            event_type=EventType.AGENT_AUTH_FAILED,
            message="Requête agent sans jeton d'authentification.",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.ANONYMOUS,
            source_ip=client_ip(request),
            request_id=request_id(request),
            object_type="endpoint",
            object_id=request.url.path,
            success=False,
        )
        raise erreur_authentification("Jeton d'agent requis.")

    jeton, revoque = _charger_jeton_agent(db, jeton_brut)
    if jeton is None or jeton.token_type != TokenType.AGENT:
        record_event(
            db,
            event_type=EventType.AGENT_AUTH_FAILED,
            message="Jeton d'agent inconnu présenté.",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.ANONYMOUS,
            source_ip=client_ip(request),
            request_id=request_id(request),
            object_type="endpoint",
            object_id=request.url.path,
            success=False,
        )
        raise erreur_authentification("Jeton d'agent invalide.")

    if revoque:
        record_event(
            db,
            event_type=EventType.AGENT_TOKEN_REVOKED_USED,
            message="Utilisation d'un jeton révoqué refusée.",
            severity=EventSeverity.ERROR,
            actor_type=EventActorType.AGENT,
            actor_id=jeton.agent_id,
            agent_id=jeton.agent_id,
            source_ip=client_ip(request),
            request_id=request_id(request),
            object_type="token",
            object_id=jeton.id,
            success=False,
        )
        raise erreur_authentification("Jeton d'agent révoqué.")

    maintenant = utcnow()
    if jeton.expires_at is not None and jeton.expires_at <= maintenant:
        record_event(
            db,
            event_type=EventType.AGENT_AUTH_FAILED,
            message="Jeton d'agent expiré présenté.",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.AGENT,
            actor_id=jeton.agent_id,
            agent_id=jeton.agent_id,
            source_ip=client_ip(request),
            request_id=request_id(request),
            success=False,
        )
        raise erreur_authentification("Jeton d'agent expiré.")

    agent = db.get(Agent, jeton.agent_id) if jeton.agent_id else None
    if agent is None:
        raise erreur_authentification("Agent associé au jeton introuvable.")
    if agent.status == AgentStatus.REVOKED:
        record_event(
            db,
            event_type=EventType.AGENT_TOKEN_REVOKED_USED,
            message="Requête d'un agent révoqué refusée.",
            severity=EventSeverity.ERROR,
            actor_type=EventActorType.AGENT,
            actor_id=agent.id,
            agent_id=agent.id,
            source_ip=client_ip(request),
            request_id=request_id(request),
            success=False,
        )
        raise erreur_acces_refuse("Agent révoqué : accès refusé.")

    # Trace d'usage du jeton (le jeton lui-même n'est jamais journalisé).
    if age_seconds(jeton.last_used_at, maintenant) is None or (
        age_seconds(jeton.last_used_at, maintenant) or 0
    ) > INTERVALLE_MAJ_SESSION:
        jeton.last_used_at = maintenant
        db.add(jeton)
        db.commit()

    return agent


def require_enrollment_key(
    request: Request,
    db: Session = Depends(get_db),
) -> Token:
    """Exige une clé d'enregistrement valide (API.md §6.1).

    Cette clé n'autorise que l'enregistrement : elle ne donne accès à aucune autre
    opération.
    """
    from app.services.token_service import trouver_cle_enregistrement_active

    jeton_brut = _extraire_bearer(request)
    if not jeton_brut:
        raise erreur_authentification("Clé d'enregistrement requise.")

    cle = trouver_cle_enregistrement_active(db)
    if cle is None or not tokens_equal(hash_token(jeton_brut), cle.token_hash):
        record_event(
            db,
            event_type=EventType.AGENT_ENROLLMENT_REFUSED,
            message="Demande d'enregistrement refusée : clé d'enregistrement invalide.",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.ANONYMOUS,
            source_ip=client_ip(request),
            request_id=request_id(request),
            object_type="endpoint",
            object_id=request.url.path,
            success=False,
        )
        raise erreur_authentification("Clé d'enregistrement invalide.")
    return cle


def require_orchestrator_online(db: Session = Depends(get_db)) -> None:
    """Refuse les opérations métier lorsque l'orchestrateur est logiquement OFFLINE.

    L'administrateur conserve l'accès au tableau de bord : ce contrôle ne s'applique
    qu'aux routes destinées aux agents (ARCHITECTURE.md §8).
    """
    etat = get_desired_state(db)
    if etat != ETAT_EN_LIGNE:
        raise erreur_indisponible(
            "L'orchestrateur est logiquement hors ligne : les opérations des agents "
            "sont momentanément refusées."
        )


def agent_autorise_sur_tache(agent: Agent, tache_agent_id: str | None) -> None:
    """Vérifie qu'une tâche appartient bien à l'agent demandeur (SECURITY.md §6).

    Un agent ne doit jamais pouvoir lire ni modifier la tâche d'un autre agent.
    """
    if tache_agent_id is None:
        raise erreur_acces_refuse("Cette tâche n'est attribuée à aucun agent.")
    if tache_agent_id != agent.id:
        raise erreur_acces_refuse("Cette tâche n'est pas attribuée à cet agent.")


def charger_agent_ou_404(db: Session, agent_id: str) -> Agent:
    """Charge un agent ou lève une erreur 404."""
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise erreur_introuvable(f"Agent introuvable : {agent_id}")
    return agent


def _horodatage_securise(valeur: datetime | None) -> datetime | None:
    return valeur
