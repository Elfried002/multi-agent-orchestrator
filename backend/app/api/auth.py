"""Routes d'authentification administrateur (API.md §5).

- ``POST /api/v1/auth/login`` : connexion, création d'une session serveur et pose
  du cookie ``HttpOnly`` ;
- ``GET  /api/v1/auth/me`` : session courante ;
- ``POST /api/v1/auth/logout`` : déconnexion, session invalidée **côté serveur** ;
- ``POST /api/v1/auth/change-password`` : modification du mot de passe.

Le mot de passe n'est jamais renvoyé, ni journalisé, ni placé dans un cookie.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.audit.event_service import record_event
from app.core.authentication import (
    ContexteAdmin,
    administrateur_courant,
    client_ip,
    creer_session,
    require_admin,
    require_admin_csrf,
    revoquer_session,
    user_agent,
)
from app.core.config import settings
from app.core.errors import erreur_authentification
from app.core.rate_limit import limiter
from app.database.connection import get_db
from app.models.event import EventActorType, EventSeverity, EventType
from app.schemas.auth import (
    AdminOut,
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginRequest,
    LoginResponse,
    SessionInfo,
)
from app.services.admin_service import authentifier, changer_mot_de_passe
from app.core.security import creer_jeton_csrf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Authentification administrateur"])


def _poser_cookie_csrf(response: Response, jeton: str) -> None:
    """Pose le cookie anti-CSRF lisible par le frontend.

    Le jeton est signé et lié à la session (SECURITY.md §4) : il ne s'agit pas
    d'un secret d'authentification, donc le cookie n'est volontairement pas
    ``HttpOnly``. Le rendre lisible est même nécessaire : le tableau de bord lit
    ce cookie pour alimenter l'en-tête ``X-CSRF-Token`` de ses requêtes
    d'écriture, y compris après un rechargement de page.
    """
    response.set_cookie(
        key="csrf_token",
        value=jeton,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=int(settings.session_ttl.total_seconds()),
        path="/",
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Connexion administrateur",
    responses={
        401: {"description": "Identifiants invalides"},
        429: {"description": "Trop de tentatives"},
    },
)
@limiter.limit(lambda: settings.login_rate_limit)
def connexion(
    request: Request,
    response: Response,
    identifiants: LoginRequest,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Authentifie l'administrateur et ouvre une session."""
    admin = authentifier(
        db,
        username=identifiants.username,
        password=identifiants.password,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )

    session, jeton, jeton_csrf = creer_session(
        db, admin, source_ip=client_ip(request), user_agent_=user_agent(request)
    )

    # Cookie de session : inaccessible au JavaScript, limité au site, Secure en
    # production (SECURITY.md §4).
    response.set_cookie(
        key=settings.cookie_name,
        value=jeton,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=int(settings.session_ttl.total_seconds()),
        path="/",
    )

    # Jeton anti-CSRF : lisible par le frontend (double soumission). Il n'est pas
    # un secret d'authentification — il est signé et lié à la session — donc il
    # n'est volontairement pas HttpOnly, sinon l'interface ne pourrait plus rien
    # modifier après un rechargement de page. Sans ce cookie, toute action
    # d'écriture du tableau de bord serait refusée avec 403.
    _poser_cookie_csrf(response, jeton_csrf)

    record_event(
        db,
        event_type=EventType.ADMIN_LOGIN_SUCCEEDED,
        message=f"Connexion réussie de l'administrateur « {admin.username} ».",
        severity=EventSeverity.INFO,
        actor_type=EventActorType.ADMIN,
        actor_id=admin.id,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
        object_type="session",
        object_id=session.id,
        success=True,
        metadata={"user_agent": user_agent(request)},
    )

    return LoginResponse(
        admin=AdminOut.model_validate(admin),
        session=SessionInfo(
            created_at=session.created_at,
            expires_at=session.expires_at,
            idle_expires_at=session.created_at + settings.session_idle_timeout,
            source_ip=session.source_ip,
        ),
        csrf_token=jeton_csrf,
    )


@router.get(
    "/me",
    response_model=AdminOut,
    summary="Session administrateur courante",
    responses={401: {"description": "Session absente ou expirée"}},
)
def session_courante(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AdminOut:
    """Retourne les informations du compte connecté.

    Le jeton anti-CSRF est réémis à chaque lecture de session : le frontend
    interroge cette route à chaque initialisation (§13 de SECURITY.md), ce qui
    garantit qu'un onglet rechargé dispose d'un jeton valide pour ses actions
    d'écriture au lieu d'échouer en 403.
    """
    contexte = administrateur_courant(db, request)
    if contexte is None:
        raise erreur_authentification(
            "Session administrateur absente ou expirée. Veuillez vous reconnecter."
        )
    _poser_cookie_csrf(
        response, creer_jeton_csrf(contexte.session_id, settings.resolved_secret_key())
    )
    return AdminOut.model_validate(contexte.admin)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Déconnexion",
    responses={204: {"description": "Session invalidée"}, 401: {"description": "Session absente"}},
)
def deconnexion(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Invalide la session côté serveur puis efface le cookie."""
    contexte = administrateur_courant(db, request)
    if contexte is None:
        # Une déconnexion sans session valide n'est pas une erreur : le cookie est
        # simplement nettoyé.
        reponse = Response(status_code=status.HTTP_204_NO_CONTENT)
        reponse.delete_cookie(settings.cookie_name, path="/")
        reponse.delete_cookie("csrf_token", path="/")
        return reponse

    revoquer_session(db, contexte.session, motif="déconnexion demandée")
    record_event(
        db,
        event_type=EventType.ADMIN_LOGOUT,
        message=f"Déconnexion de l'administrateur « {contexte.admin.username} ».",
        severity=EventSeverity.INFO,
        actor_type=EventActorType.ADMIN,
        actor_id=contexte.admin_id,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
        object_type="session",
        object_id=contexte.session_id,
        success=True,
    )
    reponse = Response(status_code=status.HTTP_204_NO_CONTENT)
    reponse.delete_cookie(settings.cookie_name, path="/")
    reponse.delete_cookie("csrf_token", path="/")
    return reponse


@router.post(
    "/change-password",
    response_model=ChangePasswordResponse,
    summary="Modification du mot de passe",
    responses={
        401: {"description": "Mot de passe actuel incorrect"},
        422: {"description": "Nouveau mot de passe non conforme"},
    },
)
def modifier_mot_de_passe(
    request: Request,
    demande: ChangePasswordRequest,
    contexte: ContexteAdmin = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> ChangePasswordResponse:
    """Change le mot de passe après vérification de l'actuel."""
    revoquees = changer_mot_de_passe(
        db,
        contexte.admin,
        mot_de_passe_actuel=demande.current_password,
        nouveau_mot_de_passe=demande.new_password,
        session_id=contexte.session_id,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )
    return ChangePasswordResponse(
        message=(
            "Mot de passe modifié. Les autres sessions ont été fermées ; "
            "vous restez connecté sur celle-ci."
        ),
        sessions_revoquees=revoquees,
    )
