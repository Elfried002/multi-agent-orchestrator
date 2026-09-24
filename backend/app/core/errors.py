"""Gestion centralisée des erreurs.

Toutes les erreurs sortent au même format (API.md §2.3) :

```json
{"error": {"code": "...", "message": "...", "request_id": "..."}}
```

Deux règles de sécurité appliquées ici :

- aucun secret n'est renvoyé dans un message d'erreur, et les détails de validation
  ne réexposent **pas** les valeurs reçues (une erreur sur un mot de passe ne doit
  pas renvoyer le mot de passe) ;
- une erreur interne ne divulgue ni trace d'exécution, ni contenu technique :
  la trace part dans le journal applicatif, le client reçoit un code générique.

Référence : SECURITY.md §12, API.md §2.2 et §2.3.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

#: Correspondance code HTTP → code d'erreur applicatif.
CODES_PAR_STATUT = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMIT_EXCEEDED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


class ErreurAPI(Exception):
    """Erreur applicative porteuse d'un code stable et d'un message lisible."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        entetes: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.entetes = entetes or {}


# ------------------------------------------------------- raccourcis usuels ---
def erreur_authentification(message: str = "Authentification requise ou invalide.") -> ErreurAPI:
    return ErreurAPI("UNAUTHENTICATED", message, status_code=401)


def erreur_identifiants_invalides() -> ErreurAPI:
    """Message volontairement identique quel que soit le motif (SECURITY.md §4)."""
    return ErreurAPI(
        "INVALID_CREDENTIALS",
        "Identifiants invalides.",
        status_code=401,
    )


def erreur_acces_refuse(message: str = "Autorisation insuffisante pour cette opération.") -> ErreurAPI:
    return ErreurAPI("FORBIDDEN", message, status_code=403)


def erreur_introuvable(message: str = "Ressource introuvable.") -> ErreurAPI:
    return ErreurAPI("NOT_FOUND", message, status_code=404)


def erreur_conflit(code: str, message: str) -> ErreurAPI:
    return ErreurAPI(code, message, status_code=409)


def erreur_invalide(code: str, message: str) -> ErreurAPI:
    return ErreurAPI(code, message, status_code=422)


def erreur_indisponible(message: str = "Service temporairement indisponible.") -> ErreurAPI:
    return ErreurAPI("SERVICE_UNAVAILABLE", message, status_code=503)


# ------------------------------------------------------------- enveloppe -----
def enveloppe(code: str, message: str, request_id: str | None = None) -> dict[str, dict[str, str]]:
    """Construit le corps d'erreur normalisé."""
    detail: dict[str, str] = {"code": code, "message": message}
    detail["request_id"] = request_id or "-"
    return {"error": detail}


def _request_id(requete: Request) -> str | None:
    return getattr(requete.state, "request_id", None)


def _resume_validation(erreurs: list[dict]) -> str:
    """Résume les erreurs de validation **sans réexposer les valeurs reçues**.

    ``RequestValidationError`` contient par défaut la valeur fautive ; la restituer
    reviendrait à renvoyer un mot de passe ou un jeton dans la réponse d'erreur.
    """
    morceaux: list[str] = []
    for erreur in erreurs[:5]:
        emplacement = ".".join(str(partie) for partie in erreur.get("loc", ()) if partie != "body")
        message = str(erreur.get("msg", "valeur invalide")).replace("Value error, ", "")
        morceaux.append(f"{emplacement or 'corps'} : {message}")
    if len(erreurs) > 5:
        morceaux.append(f"… et {len(erreurs) - 5} autre(s) erreur(s)")
    return "Données invalides — " + " ; ".join(morceaux)


def registrer_gestionnaires(app: FastAPI) -> None:
    """Enregistre les gestionnaires d'exception sur l'application."""

    @app.exception_handler(ErreurAPI)
    async def _erreur_api(requete: Request, exc: ErreurAPI) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("Erreur applicative %s : %s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=enveloppe(exc.code, exc.message, _request_id(requete)),
            headers=exc.entetes or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _erreur_validation(requete: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=enveloppe(
                "VALIDATION_ERROR",
                _resume_validation(list(exc.errors())),
                _request_id(requete),
            ),
        )

    @app.exception_handler(RateLimitExceeded)
    async def _limite_atteinte(requete: Request, exc: RateLimitExceeded) -> JSONResponse:
        logger.warning(
            "Limite de débit atteinte sur %s", requete.url.path,
            extra={"request_id": _request_id(requete) or "-"},
        )
        return JSONResponse(
            status_code=429,
            content=enveloppe(
                "RATE_LIMIT_EXCEEDED",
                "Trop de requêtes. Merci de patienter avant de réessayer.",
                _request_id(requete),
            ),
            headers={"Retry-After": "60"},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _erreur_http(requete: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = CODES_PAR_STATUT.get(exc.status_code, "ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "Requête refusée."
        return JSONResponse(
            status_code=exc.status_code,
            content=enveloppe(code, message, _request_id(requete)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _erreur_interne(requete: Request, exc: Exception) -> JSONResponse:
        # La trace complète reste côté serveur ; le client reçoit un message générique
        # (SECURITY.md §12 : pas de divulgation, pas de mode debug en production).
        logger.exception(
            "Erreur interne non traitée sur %s %s",
            requete.method,
            requete.url.path,
            extra={"request_id": _request_id(requete) or "-"},
        )
        return JSONResponse(
            status_code=500,
            content=enveloppe(
                "INTERNAL_ERROR",
                "Erreur interne du serveur. L'incident a été journalisé.",
                _request_id(requete),
            ),
        )
