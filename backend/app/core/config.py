"""Configuration centrale de l'application.

Toutes les valeurs sensibles proviennent de l'environnement ou du fichier
``backend/.env`` (jamais committé). Aucune valeur par défaut ne contient de secret
réel : si ``SECRET_KEY`` est absente en production, l'application refuse de démarrer
(SECURITY.md §15, INSTALLATION.md §11).

Référence : ARCHITECTURE.md §5 (module ``core/config.py``).
"""

from __future__ import annotations

import secrets
import warnings
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# .../backend  et racine du dépôt
BASE_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BASE_DIR.parent

# Rôles qu'un agent peut se voir attribuer. Une valeur hors de cette liste est
# ramenée à « general » : l'agent ne choisit jamais librement son rôle
# (ARCHITECTURE.md §2.1).
ROLES_AUTORISES = ("research", "builder", "analyst", "operations", "general")
ROLE_PAR_DEFAUT = "general"

# Environnements reconnus
ENVIRONNEMENTS_PRODUCTION = ("production", "prod")


def _decouper_liste(valeur: str) -> list[str]:
    """Découpe une liste fournie sous forme de chaîne (virgules ou points-virgules)."""
    if not valeur:
        return []
    brut = valeur.strip()
    if brut.startswith("[") and brut.endswith("]"):
        brut = brut[1:-1]
    morceaux = brut.replace(";", ",").split(",")
    return [m.strip().strip("\"'") for m in morceaux if m.strip()]


# Clé éphémère générée une seule fois par processus, utilisée uniquement en
# développement lorsque SECRET_KEY n'est pas fournie. Elle n'est jamais écrite
# sur le disque ni journalisée.
_CLE_EPHEMERE = secrets.token_urlsafe(64)


class Settings(BaseSettings):
    """Paramètres de l'application, lus depuis l'environnement."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        case_sensitive=False,
    )

    # --- Identité du service -------------------------------------------------
    app_name: str = "multi-agent-orchestrator"
    version: str = "1.0.0"

    # --- Environnement -------------------------------------------------------
    environment: str = Field(
        "development",
        validation_alias=AliasChoices("ORCHESTRATOR_ENV", "ENVIRONMENT"),
    )
    log_level: str = Field("INFO", validation_alias="LOG_LEVEL")
    #: Répertoire des journaux. Vide = sortie standard uniquement (cas d'un
    #: lancement manuel ou d'un test). En production : /var/log/multi-agent-orchestrator.
    log_dir: str = Field("", validation_alias="LOG_DIR")
    enable_docs: bool = Field(False, validation_alias="ENABLE_DOCS")

    # --- Persistance ---------------------------------------------------------
    database_path: str = Field("./data/orchestrator.db", validation_alias="DATABASE_PATH")

    # --- Secrets -------------------------------------------------------------
    secret_key: str = Field("", validation_alias="SECRET_KEY")
    enrollment_key: str = Field("", validation_alias="ENROLLMENT_KEY")

    # --- Sessions administrateur --------------------------------------------
    admin_session_ttl_hours: int = Field(12, validation_alias="ADMIN_SESSION_TTL_HOURS")
    admin_session_idle_minutes: int = Field(120, validation_alias="ADMIN_SESSION_IDLE_MINUTES")
    admin_session_max_active: int = Field(10, validation_alias="ADMIN_SESSION_MAX_ACTIVE")

    # --- Présence des agents -------------------------------------------------
    agent_offline_threshold_seconds: int = Field(
        90, validation_alias="AGENT_OFFLINE_THRESHOLD_SECONDS"
    )
    monitor_interval_seconds: int = Field(15, validation_alias="MONITOR_INTERVAL_SECONDS")
    #: Activation de la boucle de surveillance (désactivée dans les tests pour
    #: garantir des exécutions déterministes).
    monitor_enabled: bool = Field(True, validation_alias="MONITOR_ENABLED")
    task_default_timeout_seconds: int = Field(
        3600, validation_alias="TASK_DEFAULT_TIMEOUT_SECONDS"
    )

    # --- Réseau et proxys ----------------------------------------------------
    # Chaînes brutes : un champ de type liste serait analysé comme du JSON par
    # pydantic-settings, ce qui rejette « a,b ». Les accesseurs ci-dessous font
    # le découpage.
    allowed_origins_raw: str = Field("", validation_alias="ALLOWED_ORIGINS")
    trusted_proxies_raw: str = Field("", validation_alias="TRUSTED_PROXIES")

    # --- Limites -------------------------------------------------------------
    max_request_bytes: int = Field(1_000_000, validation_alias="MAX_REQUEST_BYTES")
    max_result_bytes: int = Field(262_144, validation_alias="MAX_RESULT_BYTES")
    login_rate_limit: str = Field("5/minute", validation_alias="LOGIN_RATE_LIMIT")
    enroll_rate_limit: str = Field("10/hour", validation_alias="ENROLL_RATE_LIMIT")
    agent_rate_limit: str = Field("300/minute", validation_alias="AGENT_RATE_LIMIT")
    admin_rate_limit: str = Field("600/minute", validation_alias="ADMIN_RATE_LIMIT")

    # --- Divers --------------------------------------------------------------
    heartbeat_fresh_seconds: int = Field(30, validation_alias="HEARTBEAT_FRESH_SECONDS")

    # ------------------------------------------------------------------ listes
    @property
    def allowed_origins(self) -> list[str]:
        return _decouper_liste(self.allowed_origins_raw)

    @property
    def trusted_proxies(self) -> list[str]:
        return _decouper_liste(self.trusted_proxies_raw)

    # ------------------------------------------------------------- commodités
    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in ENVIRONNEMENTS_PRODUCTION

    @property
    def database_file(self) -> Path:
        """Chemin absolu de la base SQLite."""
        chemin = Path(self.database_path)
        if not chemin.is_absolute():
            chemin = BASE_DIR / chemin
        return chemin

    @property
    def data_dir(self) -> Path:
        return self.database_file.parent

    @property
    def frontend_dist(self) -> Path:
        return PROJECT_DIR / "frontend" / "dist"

    @property
    def session_ttl(self) -> timedelta:
        return timedelta(hours=self.admin_session_ttl_hours)

    @property
    def session_idle_timeout(self) -> timedelta:
        return timedelta(minutes=self.admin_session_idle_minutes)

    @property
    def offline_threshold(self) -> timedelta:
        return timedelta(seconds=self.agent_offline_threshold_seconds)

    @property
    def cookie_name(self) -> str:
        return "orchestrator_session"

    @property
    def cookie_secure(self) -> bool:
        """Cookie marqué Secure en production (HTTPS), toléré en développement local."""
        return self.is_production

    @property
    def cookie_samesite(self) -> str:
        return "lax"

    def resolved_secret_key(self) -> str:
        """Clé de signature effective.

        En production, l'absence de ``SECRET_KEY`` est une erreur bloquante : les
        sessions et la clé d'enregistrement reposent sur cette clé.
        En développement, une clé éphémère est générée (les sessions ne survivent
        alors pas au redémarrage, ce qui est signalé par un avertissement).
        """
        if self.secret_key:
            return self.secret_key
        if self.is_production:
            raise RuntimeError(
                "SECRET_KEY est absente : l'application refuse de démarrer en production. "
                "Générez une clé avec « python -c \"import secrets;print(secrets.token_urlsafe(64))\" » "
                "et placez-la dans /etc/multi-agent-orchestrator/production.env."
            )
        warnings.warn(
            "SECRET_KEY absente : clé éphémère utilisée (développement uniquement). "
            "Les sessions seront invalidées à chaque redémarrage.",
            RuntimeWarning,
            stacklevel=2,
        )
        return _CLE_EPHEMERE

    def validate_settings(self) -> None:
        """Contrôles de cohérence appliqués au démarrage."""
        problemes: list[str] = []
        if self.admin_session_ttl_hours <= 0:
            problemes.append("ADMIN_SESSION_TTL_HOURS doit être strictement positif")
        if self.admin_session_idle_minutes <= 0:
            problemes.append("ADMIN_SESSION_IDLE_MINUTES doit être strictement positif")
        if self.agent_offline_threshold_seconds < 10:
            problemes.append("AGENT_OFFLINE_THRESHOLD_SECONDS doit valoir au moins 10 secondes")
        if self.monitor_interval_seconds <= 0:
            problemes.append("MONITOR_INTERVAL_SECONDS doit être strictement positif")
        if self.task_default_timeout_seconds <= 0:
            problemes.append("TASK_DEFAULT_TIMEOUT_SECONDS doit être strictement positif")
        if self.max_request_bytes < 1024:
            problemes.append("MAX_REQUEST_BYTES doit valoir au moins 1024 octets")
        if self.log_level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            problemes.append(f"LOG_LEVEL inconnu : {self.log_level}")
        # La clé de signature doit être disponible (lève en production si absente).
        self.resolved_secret_key()
        if problemes:
            raise RuntimeError("Configuration invalide : " + " ; ".join(problemes))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retourne l'instance unique de configuration."""
    return Settings()


def reset_settings_cache() -> None:
    """Vide le cache de configuration (utilisé par les tests)."""
    get_settings.cache_clear()


settings = get_settings()
