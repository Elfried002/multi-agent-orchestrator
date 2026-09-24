"""Détection et restitution des alertes de sécurité.

Les alertes sont **dérivées du journal applicatif** : le système ne fait aucune
capture réseau et ne prétend pas remplacer un IDS (SECURITY.md §11). Chaque alerte
est construite à partir d'événements réellement enregistrés, avec un seuil
explicite, et reste donc vérifiable dans la page « Logs ».

Règles implémentées (SECURITY.md §11) :

- échecs d'authentification répétés ;
- tentative d'utilisation d'un jeton révoqué ;
- erreurs d'autorisation ;
- agents passés hors ligne ;
- rejets anti-CSRF et dépassements de limite de débit ;
- erreurs applicatives répétées.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import Agent, AgentStatus
from app.models.event import Event, EventActorType, EventSeverity, EventType
from app.models.task import Task, TaskStatus
from app.utils.datetime_utils import humanize_duration, utcnow

#: Seuils déclenchant une alerte (documentés et volontairement bas).
SEUIL_ECHECS_AUTH = 5
SEUIL_JETONS_REVOQUES = 1
SEUIL_ERREURS_API = 10
NIVEAU_ALERTE = {
    EventSeverity.CRITICAL: "CRITIQUE",
    EventSeverity.ERROR: "ÉLEVÉE",
    EventSeverity.WARNING: "MOYENNE",
    EventSeverity.INFO: "INFO",
}


def _compter(
    db: Session, types: tuple[str, ...], depuis, *, severites: tuple[str, ...] | None = None
) -> int:
    requete = (
        select(func.count())
        .select_from(Event)
        .where(Event.event_type.in_(types), Event.created_at >= depuis)
    )
    if severites:
        requete = requete.where(Event.severity.in_(severites))
    return int(db.execute(requete).scalar_one())


def _premiers_evenements(
    db: Session, types: tuple[str, ...], depuis, *, limite: int = 3
) -> list[Event]:
    return list(
        db.execute(
            select(Event)
            .where(Event.event_type.in_(types), Event.created_at >= depuis)
            .order_by(Event.created_at.desc())
            .limit(limite)
        )
        .scalars()
        .all()
    )


def alertes(db: Session, *, fenetre_heures: int = 24, limite: int = 50) -> list[dict[str, Any]]:
    """Alertes de sécurité déduites des événements de la fenêtre considérée."""
    depuis = utcnow() - timedelta(hours=fenetre_heures)
    resultat: list[dict[str, Any]] = []

    # 1. Échecs d'authentification administrateur
    echecs = _compter(
        db, (EventType.ADMIN_LOGIN_FAILED, EventType.ADMIN_ACCOUNT_LOCKED), depuis
    )
    if echecs >= SEUIL_ECHECS_AUTH:
        derniers = _premiers_evenements(
            db, (EventType.ADMIN_LOGIN_FAILED, EventType.ADMIN_ACCOUNT_LOCKED), depuis
        )
        resultat.append(
            {
                "id": "alerte_auth_echecs",
                "niveau": "ÉLEVÉE",
                "gravite": EventSeverity.ERROR,
                "titre": "Échecs d'authentification répétés",
                "message": (
                    f"{echecs} échec(s) de connexion administrateur sur les "
                    f"{fenetre_heures} dernières heures (seuil {SEUIL_ECHECS_AUTH})."
                ),
                "nombre": echecs,
                "seuil": SEUIL_ECHECS_AUTH,
                "derniers_evenements": [evenement.id for evenement in derniers],
                "conseil": "Vérifiez l'origine des tentatives et envisagez un verrouillage de compte.",
            }
        )

    # 2. Utilisation de jetons révoqués
    jetons = _compter(db, (EventType.AGENT_TOKEN_REVOKED_USED,), depuis)
    if jetons >= SEUIL_JETONS_REVOQUES:
        resultat.append(
            {
                "id": "alerte_jetons_revoques",
                "niveau": "ÉLEVÉE",
                "gravite": EventSeverity.ERROR,
                "titre": "Utilisation d'un jeton révoqué",
                "message": (
                    f"{jetons} tentative(s) d'utilisation d'un jeton ou d'un agent révoqué."
                ),
                "nombre": jetons,
                "seuil": SEUIL_JETONS_REVOQUES,
                "conseil": "Un connecteur tente de fonctionner après révocation : identifiez-le.",
            }
        )

    # 3. Autorisations refusées
    refus = _compter(db, (EventType.AGENT_FORBIDDEN, EventType.AUTHORIZATION_DENIED), depuis)
    if refus:
        resultat.append(
            {
                "id": "alerte_autorisation",
                "niveau": "MOYENNE",
                "gravite": EventSeverity.WARNING,
                "titre": "Accès refusés",
                "message": f"{refus} opération(s) refusée(s) pour autorisation insuffisante.",
                "nombre": refus,
                "seuil": 1,
                "conseil": "Vérifiez que l'agent concerné ne tente pas d'accéder à des tâches tierces.",
            }
        )

    # 4. Rejets anti-CSRF et limites de débit
    csrf = _compter(db, (EventType.CSRF_REJECTED,), depuis)
    if csrf:
        resultat.append(
            {
                "id": "alerte_csrf",
                "niveau": "MOYENNE",
                "gravite": EventSeverity.WARNING,
                "titre": "Requêtes modifiantes rejetées (CSRF)",
                "message": f"{csrf} requête(s) refusée(s) faute de jeton anti-CSRF valide.",
                "nombre": csrf,
                "seuil": 1,
                "conseil": "Une session expirée ou un script tiers sollicite l'API de façon non conforme.",
            }
        )

    limites = _compter(db, (EventType.RATE_LIMIT_EXCEEDED,), depuis)
    if limites:
        resultat.append(
            {
                "id": "alerte_debit",
                "niveau": "MOYENNE",
                "gravite": EventSeverity.WARNING,
                "titre": "Limite de débit atteinte",
                "message": f"{limites} dépassement(s) de limite de requêtes détectés.",
                "nombre": limites,
                "seuil": 1,
                "conseil": "Vérifiez l'origine : erreur de configuration ou tentative d'énumération.",
            }
        )

    # 5. Erreurs applicatives
    erreurs = _compter(db, (EventType.SERVICE_ERROR,), depuis)
    if erreurs >= SEUIL_ERREURS_API:
        resultat.append(
            {
                "id": "alerte_erreurs",
                "niveau": "MOYENNE",
                "gravite": EventSeverity.WARNING,
                "titre": "Erreurs applicatives répétées",
                "message": f"{erreurs} erreur(s) applicative(s) sur {fenetre_heures} heures.",
                "nombre": erreurs,
                "seuil": SEUIL_ERREURS_API,
                "conseil": "Consultez application.log pour identifier la cause racine.",
            }
        )

    # 6. Agents hors ligne
    hors_ligne = int(
        db.execute(
            select(func.count()).select_from(Agent).where(Agent.status == AgentStatus.OFFLINE)
        ).scalar_one()
    )
    if hors_ligne:
        resultat.append(
            {
                "id": "alerte_agents_hors_ligne",
                "niveau": "MOYENNE",
                "gravite": EventSeverity.WARNING,
                "titre": "Agents hors ligne",
                "message": f"{hors_ligne} agent(s) sans signal de présence au-delà du seuil.",
                "nombre": hors_ligne,
                "seuil": 1,
                "conseil": "Vérifiez la connectivité des connecteurs concernés.",
            }
        )

    # 7. Tâches en échec ou en dépassement de délai
    en_echec = int(
        db.execute(
            select(func.count())
            .select_from(Task)
            .where(Task.status.in_((TaskStatus.FAILED, TaskStatus.TIMEOUT)))
        ).scalar_one()
    )
    if en_echec:
        resultat.append(
            {
                "id": "alerte_taches_echec",
                "niveau": "INFO",
                "gravite": EventSeverity.INFO,
                "titre": "Tâches en échec ou hors délai",
                "message": f"{en_echec} tâche(s) en échec ou en dépassement de délai.",
                "nombre": en_echec,
                "seuil": 1,
                "conseil": "Consultez le détail des tâches pour identifier la cause.",
            }
        )

    ordre = {"ÉLEVÉE": 0, "CRITIQUE": 1, "MOYENNE": 2, "INFO": 3}
    resultat.sort(key=lambda alerte: ordre.get(str(alerte["niveau"]), 9))
    return resultat[:limite]


def resume_securite(db: Session, *, fenetre_heures: int = 24) -> dict[str, Any]:
    """Synthèse de sécurité destinée à la page « Security » du tableau de bord."""
    depuis = utcnow() - timedelta(hours=fenetre_heures)

    par_severite = {
        gravite: int(
            db.execute(
                select(func.count())
                .select_from(Event)
                .where(Event.created_at >= depuis, Event.severity == gravite)
            ).scalar_one()
        )
        for gravite in EventSeverity.TOUS
    }

    compteurs = {
        "connexions_reussies": _compter(db, (EventType.ADMIN_LOGIN_SUCCEEDED,), depuis),
        "connexions_echouees": _compter(db, (EventType.ADMIN_LOGIN_FAILED,), depuis),
        "comptes_verrouilles": _compter(db, (EventType.ADMIN_ACCOUNT_LOCKED,), depuis),
        "deconnexions": _compter(db, (EventType.ADMIN_LOGOUT,), depuis),
        "echecs_authentification_agent": _compter(db, (EventType.AGENT_AUTH_FAILED,), depuis),
        "jetons_revoques_utilises": _compter(db, (EventType.AGENT_TOKEN_REVOKED_USED,), depuis),
        "enregistrements_refuses": _compter(db, (EventType.AGENT_ENROLLMENT_REFUSED,), depuis),
        "agents_revoques": _compter(db, (EventType.AGENT_REVOKED,), depuis),
        "acces_refuses": _compter(
            db, (EventType.AGENT_FORBIDDEN, EventType.AUTHORIZATION_DENIED), depuis
        ),
        "csrf_rejetes": _compter(db, (EventType.CSRF_REJECTED,), depuis),
        "limites_depassees": _compter(db, (EventType.RATE_LIMIT_EXCEEDED,), depuis),
        "changements_etat": _compter(db, (EventType.STATE_CHANGED,), depuis),
        "consultations_cle_enregistrement": _compter(db, (EventType.ENROLLMENT_KEY_READ,), depuis),
        "rotations_cle_enregistrement": _compter(db, (EventType.ENROLLMENT_KEY_ROTATED,), depuis),
    }

    agents_par_etat = {
        etat: int(
            db.execute(
                select(func.count()).select_from(Agent).where(Agent.status == etat)
            ).scalar_one()
        )
        for etat in AgentStatus.TOUS
    }

    return {
        "fenetre_heures": fenetre_heures,
        "depuis": depuis,
        "par_severite": par_severite,
        "compteurs": compteurs,
        "agents_par_etat": agents_par_etat,
        "alertes": alertes(db, fenetre_heures=fenetre_heures),
        "portee": (
            "Surveillance applicative fondée sur les journaux d'audit : "
            "elle ne constitue ni une capture réseau ni un IDS."
        ),
        "anciennete": humanize_duration(fenetre_heures * 3600),
    }
