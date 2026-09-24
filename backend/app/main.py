"""Application FastAPI de l'orchestrateur — assemblage et cycle de vie.

Responsabilités de ce module :

- configurer la journalisation, la base et les migrations au démarrage ;
- restaurer l'**état logique persistant** (ONLINE / OFFLINE) — jamais confondu avec
  l'état du processus (ARCHITECTURE.md §8) ;
- garantir qu'une clé d'enregistrement active existe ;
- démarrer la surveillance de présence ;
- appliquer les protections transverses : identifiant de requête, limite de taille,
  en-têtes de sécurité, CORS restreint ;
- protéger les routes de documentation (API.md §11) ;
- servir le frontend compilé lorsqu'il est présent.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import agents, auth, health, logs
from app.api import settings as settings_api
from app.api import tasks
from app.audit.audit_logger import configure_logging
from app.audit.event_service import record_event
from app.core.authentication import require_admin
from app.core.config import settings
from app.core.errors import enveloppe, registrer_gestionnaires
from app.core.rate_limit import limiter
from app.core.state import health_status, marquer_demarrage, mode_supervision
from app.database.connection import configure_database, session_scope
from app.database.migrations import run_migrations
from app.models.event import EventActorType, EventSeverity, EventType
from app.monitoring.health_monitor import problemes_bloquants
from app.monitoring.heartbeat import arreter_surveillance, demarrer_surveillance
from app.services.token_service import assurer_cle_enregistrement

logger = logging.getLogger(__name__)

#: Taille maximale d'un corps de requête, appliquée même sans en-tête Content-Length.
TAILLE_LECTURE_MAX = 8 * 1024 * 1024


# ============================================================ middlewares ====
class MiddlewareIdentifiantRequete(BaseHTTPMiddleware):
    """Attribue un identifiant unique à chaque requête et le renvoie au client."""

    async def dispatch(self, request: Request, call_next):
        identifiant = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:16]}"
        request.state.request_id = identifiant
        try:
            reponse = await call_next(request)
        except Exception:
            # Les gestionnaires d'exception produisent la réponse ; on garantit
            # seulement la traçabilité ici.
            logger.exception(
                "Échec non intercepté sur %s %s",
                request.method,
                request.url.path,
                extra={"request_id": identifiant},
            )
            raise
        reponse.headers["X-Request-ID"] = identifiant
        return reponse


class MiddlewareTailleRequete(BaseHTTPMiddleware):
    """Refuse les corps de requête dépassant la limite configurée (SECURITY.md §12)."""

    async def dispatch(self, request: Request, call_next):
        limite = settings.max_request_bytes
        annonce = request.headers.get("content-length")
        if annonce is not None:
            try:
                if int(annonce) > limite:
                    return JSONResponse(
                        status_code=413,
                        content=enveloppe(
                            "PAYLOAD_TOO_LARGE",
                            f"Corps de requête trop volumineux (limite {limite} octets).",
                            getattr(request.state, "request_id", None),
                        ),
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content=enveloppe(
                        "INVALID_CONTENT_LENGTH",
                        "En-tête Content-Length invalide.",
                        getattr(request.state, "request_id", None),
                    ),
                )

        # Contrôle effectif : un client peut mentir sur Content-Length ou utiliser
        # un transfert par blocs.
        if annonce is None:
            corps = b""
            morceaux: list[bytes] = []
            async for morceau in request.stream():
                corps += morceau
                morceaux.append(morceau)
                if len(corps) > limite:
                    return JSONResponse(
                        status_code=413,
                        content=enveloppe(
                            "PAYLOAD_TOO_LARGE",
                            f"Corps de requête trop volumineux (limite {limite} octets).",
                            getattr(request.state, "request_id", None),
                        ),
                    )

            async def recevoir_rejoue():
                for morceau in morceaux:
                    yield {"type": "http.request", "body": morceau, "more_body": False}

            request._receive = recevoir_rejoue  # noqa: SLF001 - réinjection nécessaire

        return await call_next(request)


class MiddlewareEntetesSecurite(BaseHTTPMiddleware):
    """Ajoute les en-têtes de sécurité à chaque réponse (SECURITY.md §13)."""

    #: Politique de contenu : le frontend compilé est servi par la même origine ;
    #: aucune ressource distante n'est autorisée hormis les styles en ligne de Swagger.
    CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    async def dispatch(self, request: Request, call_next):
        reponse = await call_next(request)
        reponse.headers.setdefault("X-Content-Type-Options", "nosniff")
        reponse.headers.setdefault("X-Frame-Options", "DENY")
        reponse.headers.setdefault("Referrer-Policy", "no-referrer")
        reponse.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        reponse.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # La documentation interactive a besoin des scripts CDN : on n'applique pas
        # la CSP aux pages de documentation, mais aux réponses de l'application.
        chemin = request.url.path
        if not chemin.startswith(("/docs", "/redoc")):
            reponse.headers.setdefault("Content-Security-Policy", self.CSP)
        # Les réponses d'API ne doivent jamais être mises en cache par un proxy.
        if chemin.startswith("/api/") or chemin == "/health":
            reponse.headers.setdefault("Cache-Control", "no-store")
        if settings.is_production and settings.cookie_secure:
            reponse.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return reponse


# ============================================================== cycle de vie =
@asynccontextmanager
async def cycle_de_vie(app: FastAPI):
    """Démarrage et arrêt propres du service."""
    marquer_demarrage()

    dossier_journaux = Path(settings.log_dir) if settings.log_dir else None
    chemins = configure_logging(
        level=settings.log_level,
        dossier_journaux=dossier_journaux,
        vers_fichiers=bool(dossier_journaux),
    )
    logger.info(
        "Démarrage du service %s v%s (environnement %s)",
        settings.app_name,
        settings.version,
        settings.environment,
    )
    if chemins:
        logger.info("Journaux : %s", ", ".join(str(chemin) for chemin in chemins.values()))

    settings.validate_settings()
    configure_database(settings.database_file)
    appliquees = run_migrations(configure_database(settings.database_file))
    if appliquees:
        logger.info("Migrations appliquées au démarrage : %s", appliquees)

    with session_scope() as db:
        # La clé d'enregistrement doit exister pour que de nouveaux agents puissent
        # s'enregistrer, même après une réinstallation du service.
        assurer_cle_enregistrement(db)
        # L'état logique est lu depuis la base : il est restauré, pas réinitialisé.
        record_event(
            db,
            event_type=EventType.SERVICE_STARTED,
            message=f"Service démarré (v{settings.version}, environnement {settings.environment}).",
            severity=EventSeverity.INFO,
            actor_type=EventActorType.SYSTEM,
            success=True,
            metadata={"mode_supervision": mode_supervision()},
        )
        bloquants = problemes_bloquants(db)
        sante = health_status(db)
        if bloquants:
            for probleme in bloquants:
                logger.error("Problème au démarrage : %s", probleme)

    logger.info("État de santé initial : %s", sante)
    demarrer_surveillance(app)

    try:
        yield
    finally:
        await arreter_surveillance(app)
        with session_scope() as db:
            record_event(
                db,
                event_type=EventType.SERVICE_STOPPED,
                message="Service arrêté proprement.",
                severity=EventSeverity.INFO,
                actor_type=EventActorType.SYSTEM,
                success=True,
            )
        logger.info("Arrêt du service terminé.")


# ================================================================ fabrique ===
def creer_app() -> FastAPI:
    """Construit et configure l'application FastAPI."""
    doc_public = settings.enable_docs
    application = FastAPI(
        title="Multi-Agent Orchestrator",
        version=settings.version,
        description=(
            "Plateforme centralisée d'enregistrement, d'authentification, de supervision "
            "et d'orchestration d'agents IA.\n\n"
            "Authentification : session administrateur (cookie HttpOnly) pour les routes "
            "d'administration ; jeton individuel `Bearer` pour les routes des agents."
        ),
        lifespan=cycle_de_vie,
        # Les routes de documentation sont déclarées explicitement plus bas afin
        # d'être protégées en production (API.md §11).
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        contact={"name": "Exploitation", "url": "https://example.invalid"},
        license_info={"name": "Propriétaire"},
    )

    application.state.limiter = limiter
    registrer_gestionnaires(application)

    # --- En-têtes de sécurité, taille, identifiant ---------------------------
    # Ordre d'empilement : sécurité (externe) → identifiant → taille → application.
    application.add_middleware(MiddlewareTailleRequete)
    application.add_middleware(MiddlewareIdentifiantRequete)
    application.add_middleware(MiddlewareEntetesSecurite)

    # --- CORS : uniquement les origines déclarées ---------------------------
    if settings.allowed_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID", "Authorization"],
            max_age=600,
        )

    # --- Routes --------------------------------------------------------------
    application.include_router(health.router)
    application.include_router(auth.router)
    application.include_router(agents.router)
    application.include_router(tasks.router)
    application.include_router(logs.router)
    application.include_router(settings_api.router)

    # --- Documentation protégée ---------------------------------------------
    if doc_public:
        from fastapi.openapi.utils import get_openapi

        @application.get("/openapi.json", include_in_schema=False)
        def schema_ouvert() -> JSONResponse:
            return JSONResponse(
                get_openapi(
                    title=application.title,
                    version=application.version,
                    description=application.description,
                    routes=application.routes,
                )
            )

        @application.get("/docs", include_in_schema=False)
        def documentation() -> Response:
            return get_swagger_ui_html(
                openapi_url="/openapi.json", title=f"{application.title} — documentation"
            )

        @application.get("/redoc", include_in_schema=False)
        def documentation_alternative() -> Response:
            return get_redoc_html(
                openapi_url="/openapi.json", title=f"{application.title} — documentation"
            )
    else:
        # Routes de documentation protégées par authentification administrateur :
        # elles restent accessibles à l'exploitant, jamais au public.
        from fastapi.openapi.utils import get_openapi

        @application.get("/openapi.json", include_in_schema=False)
        def schema_protege(_admin=Depends(require_admin)) -> JSONResponse:
            return JSONResponse(
                get_openapi(
                    title=application.title,
                    version=application.version,
                    description=application.description,
                    routes=application.routes,
                )
            )

        @application.get("/docs", include_in_schema=False)
        def documentation_protegee(_admin=Depends(require_admin)) -> Response:
            return get_swagger_ui_html(
                openapi_url="/openapi.json", title=f"{application.title} — documentation"
            )

        @application.get("/redoc", include_in_schema=False)
        def documentation_alternative_protegee(_admin=Depends(require_admin)) -> Response:
            return get_redoc_html(
                openapi_url="/openapi.json", title=f"{application.title} — documentation"
            )

    # --- Frontend compilé ----------------------------------------------------
    _servir_frontend(application)

    return application


def _servir_frontend(application: FastAPI) -> None:
    """Sert le frontend compilé s'il est présent, sans masquer les erreurs d'API."""
    dist = settings.frontend_dist
    index = dist / "index.html"
    if not index.is_file():
        logger.info(
            "Frontend compilé absent (%s) : seules les routes d'API sont servies.", dist
        )
        return

    assets = dist / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @application.get("/{chemin:path}", include_in_schema=False)
    async def application_frontend(chemin: str):
        """Sert les fichiers statiques du frontend, avec repli SPA sur index.html."""
        if chemin.startswith(("api/", "health", "docs", "redoc", "openapi.json")):
            # Ces chemins appartiennent à l'API : ne jamais renvoyer du HTML à leur place.
            return JSONResponse(
                status_code=404,
                content=enveloppe("NOT_FOUND", "Ressource introuvable.", None),
            )
        candidat = (dist / chemin).resolve()
        try:
            candidat.relative_to(dist.resolve())
        except ValueError:
            return JSONResponse(
                status_code=404,
                content=enveloppe("NOT_FOUND", "Ressource introuvable.", None),
            )
        if chemin and candidat.is_file():
            return FileResponse(candidat)
        return FileResponse(index)

    logger.info("Frontend compilé servi depuis %s", dist)


app = creer_app()
