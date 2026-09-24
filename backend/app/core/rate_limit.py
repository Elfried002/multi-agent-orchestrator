"""Limitation de débit des routes sensibles (SECURITY.md §12).

Le compteur est indexé sur l'adresse IP observée — celle du proxy de confiance
lorsqu'il est déclaré, sinon celle du pair TCP (voir ``core/authentication.py``).
Les limites sont configurables par environnement pour s'adapter à l'exploitation.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter


def cle_client(request: Request) -> str:
    """Clé de comptage : adresse IP observée de l'appelant."""
    from app.core.authentication import client_ip

    return client_ip(request)


#: Instance unique partagée par l'application et les routes.
limiter = Limiter(
    key_func=cle_client,
    # L'injection automatique des en-têtes X-RateLimit-* exigerait un paramètre
    # ``response: Response`` sur chaque route décorée ; ce n'est pas nécessaire ici.
    # Le client reçoit le délai de reprise via l'en-tête Retry-After de la réponse 429
    # (voir core/errors.py).
    headers_enabled=False,
    # Le stockage en mémoire suffit à un service mono-processus ; un déploiement
    # multi-processus devrait utiliser un stockage partagé.
    strategy="fixed-window",
)


def limites_actives() -> dict[str, str]:
    """Rappel des limites appliquées, pour la page « Paramètres »."""
    from app.core.config import settings

    return {
        "login": settings.login_rate_limit,
        "enroll": settings.enroll_rate_limit,
        "agent": settings.agent_rate_limit,
        "admin": settings.admin_rate_limit,
    }
