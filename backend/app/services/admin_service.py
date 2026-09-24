"""Service administrateur : création de compte, authentification, mot de passe.

Mesures appliquées (SECURITY.md §4) :

- mot de passe stocké uniquement sous forme d'empreinte Argon2id ;
- message d'erreur **identique** que le compte existe ou non ;
- vérification « à vide » lorsque le compte est inconnu, pour ne pas révéler son
  existence par le temps de réponse ;
- verrouillage temporaire après plusieurs échecs ;
- changement de mot de passe qui révoque les autres sessions.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.event_service import generer_identifiant, record_event
from app.core.errors import erreur_conflit, erreur_identifiants_invalides, erreur_invalide
from app.core.security import hash_password, password_needs_rehash, verify_password
from app.models.admin import Admin
from app.models.event import EventActorType, EventSeverity, EventType
from app.utils.datetime_utils import utcnow
from app.utils.validators import ErreurValidation, validate_password, validate_username

logger = logging.getLogger(__name__)

#: Nombre d'échecs consécutifs avant verrouillage temporaire.
MAX_TENTATIVES = 5
#: Durée du verrouillage, en minutes.
DUREE_VERROUILLAGE_MINUTES = 15

#: Empreinte factice utilisée pour égaliser le temps de réponse quand le compte
#: n'existe pas (elle ne correspond à aucun mot de passe utilisable).
EMPREINTE_FACTICE = hash_password("empreinte-factice-jamais-utilisee-pour-un-compte")


def compter_admins(db: Session) -> int:
    """Nombre de comptes administrateur enregistrés."""
    return int(db.execute(select(func.count()).select_from(Admin)).scalar_one())


def get_par_nom(db: Session, username: str) -> Admin | None:
    """Retourne un administrateur par son nom d'utilisateur."""
    return db.execute(
        select(Admin).where(func.lower(Admin.username) == username.strip().lower())
    ).scalar_one_or_none()


def creer_admin(
    db: Session,
    *,
    username: str,
    password: str,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> tuple[Admin, bool]:
    """Crée un compte administrateur.

    Retourne ``(admin, cree)`` : si le compte existe déjà avec le même mot de passe
    vérifiable, il est retourné sans modification, ce qui rend l'initialisation
    idempotente lors d'une réexécution du script d'installation
    (INSTALLATION.md §5, étape 8).
    """
    try:
        nom = validate_username(username)
        validate_password(password)
    except ErreurValidation as erreur:
        raise erreur_invalide(erreur.code, erreur.message) from erreur

    existant = get_par_nom(db, nom)
    if existant is not None:
        if verify_password(existant.password_hash, password):
            return existant, False
        raise erreur_conflit(
            "ADMIN_ALREADY_EXISTS",
            f"Le compte « {nom} » existe déjà. Utilisez un autre nom ou modifiez son mot "
            "de passe depuis le tableau de bord.",
        )

    maintenant = utcnow()
    admin = Admin(
        id=generer_identifiant("adm"),
        username=nom,
        password_hash=hash_password(password),
        is_active=True,
        created_at=maintenant,
        password_changed_at=maintenant,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    record_event(
        db,
        event_type=EventType.ADMIN_CREATED,
        message=f"Création du compte administrateur « {admin.username} ».",
        severity=EventSeverity.INFO,
        actor_type=EventActorType.SYSTEM,
        actor_id=admin.id,
        source_ip=source_ip,
        request_id=request_id,
        object_type="admin",
        object_id=admin.id,
        success=True,
    )
    return admin, True


def authentifier(
    db: Session,
    *,
    username: str,
    password: str,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> Admin:
    """Authentifie un administrateur.

    Lève toujours la même erreur en cas d'échec, quel qu'en soit le motif.
    """
    maintenant = utcnow()
    admin = get_par_nom(db, username)

    if admin is None:
        # Vérification à vide : le temps de réponse ne révèle pas l'absence de compte.
        verify_password(EMPREINTE_FACTICE, password)
        record_event(
            db,
            event_type=EventType.ADMIN_LOGIN_FAILED,
            message="Tentative de connexion avec un nom d'utilisateur inconnu.",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.ANONYMOUS,
            actor_id=username[:64],
            source_ip=source_ip,
            request_id=request_id,
            object_type="admin",
            success=False,
        )
        raise erreur_identifiants_invalides()

    if admin.locked_until is not None and admin.locked_until > maintenant:
        restant = int((admin.locked_until - maintenant).total_seconds() // 60) + 1
        # Refus d'une tentative pendant un verrouillage : ce n'est pas un nouveau
        # verrouillage, l'événement correspondant serait donc trompeur.
        record_event(
            db,
            event_type=EventType.ADMIN_LOGIN_FAILED,
            message=f"Connexion refusée : compte verrouillé pour encore {restant} minute(s).",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.ANONYMOUS,
            actor_id=admin.id,
            source_ip=source_ip,
            request_id=request_id,
            object_type="admin",
            object_id=admin.id,
            success=False,
        )
        raise erreur_identifiants_invalides()

    if not verify_password(admin.password_hash, password):
        admin.failed_login_count = (admin.failed_login_count or 0) + 1
        admin.last_failed_login_at = maintenant
        verrouille = False
        if admin.failed_login_count >= MAX_TENTATIVES:
            admin.locked_until = maintenant + timedelta(minutes=DUREE_VERROUILLAGE_MINUTES)
            verrouille = True
        db.add(admin)
        db.commit()
        record_event(
            db,
            event_type=EventType.ADMIN_ACCOUNT_LOCKED if verrouille else EventType.ADMIN_LOGIN_FAILED,
            message=(
                f"Compte verrouillé après {admin.failed_login_count} échecs consécutifs."
                if verrouille
                else f"Mot de passe incorrect (échec n°{admin.failed_login_count})."
            ),
            severity=EventSeverity.ERROR if verrouille else EventSeverity.WARNING,
            actor_type=EventActorType.ANONYMOUS,
            actor_id=admin.id,
            source_ip=source_ip,
            request_id=request_id,
            object_type="admin",
            object_id=admin.id,
            success=False,
        )
        raise erreur_identifiants_invalides()

    if not admin.is_active:
        record_event(
            db,
            event_type=EventType.ADMIN_LOGIN_FAILED,
            message="Connexion refusée : compte désactivé.",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.ANONYMOUS,
            actor_id=admin.id,
            source_ip=source_ip,
            request_id=request_id,
            object_type="admin",
            object_id=admin.id,
            success=False,
        )
        raise erreur_identifiants_invalides()

    # Succès : compteurs remis à zéro et empreinte rehaussée si nécessaire.
    if password_needs_rehash(admin.password_hash):
        admin.password_hash = hash_password(password)
    admin.failed_login_count = 0
    admin.locked_until = None
    admin.last_login_at = maintenant
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def changer_mot_de_passe(
    db: Session,
    admin: Admin,
    *,
    mot_de_passe_actuel: str,
    nouveau_mot_de_passe: str,
    session_id: str | None = None,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> int:
    """Change le mot de passe après vérification du mot de passe actuel (API.md §5.4).

    Retourne le nombre de sessions révoquées (toutes sauf la session courante).
    """
    from app.core.authentication import revoquer_toutes_sessions

    if not verify_password(admin.password_hash, mot_de_passe_actuel):
        record_event(
            db,
            event_type=EventType.ADMIN_LOGIN_FAILED,
            message="Changement de mot de passe refusé : mot de passe actuel incorrect.",
            severity=EventSeverity.WARNING,
            actor_type=EventActorType.ADMIN,
            actor_id=admin.id,
            source_ip=source_ip,
            request_id=request_id,
            object_type="admin",
            object_id=admin.id,
            success=False,
        )
        raise erreur_identifiants_invalides()

    try:
        validate_password(nouveau_mot_de_passe)
    except ErreurValidation as erreur:
        raise erreur_invalide(erreur.code, erreur.message) from erreur

    if verify_password(admin.password_hash, nouveau_mot_de_passe):
        raise erreur_invalide(
            "PASSWORD_UNCHANGED", "Le nouveau mot de passe doit être différent de l'actuel."
        )

    admin.password_hash = hash_password(nouveau_mot_de_passe)
    admin.password_changed_at = utcnow()
    admin.failed_login_count = 0
    admin.locked_until = None
    db.add(admin)
    db.commit()

    revoquees = revoquer_toutes_sessions(
        db,
        admin.id,
        motif="changement de mot de passe",
        sauf=session_id,
    )

    record_event(
        db,
        event_type=EventType.ADMIN_PASSWORD_CHANGED,
        message=f"Mot de passe modifié ; {revoquees} autre(s) session(s) révoquée(s).",
        severity=EventSeverity.WARNING,
        actor_type=EventActorType.ADMIN,
        actor_id=admin.id,
        source_ip=source_ip,
        request_id=request_id,
        object_type="admin",
        object_id=admin.id,
        success=True,
        metadata={"sessions_revoquees": revoquees},
    )
    return revoquees


def verifier_verrouillage(admin: Admin) -> bool:
    """Indique si un compte est actuellement verrouillé."""
    if admin.locked_until is None:
        return False
    return admin.locked_until > utcnow()
