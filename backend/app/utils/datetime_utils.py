"""Utilitaires de temps — tout est stocké et échangé en UTC.

Format retenu : ISO 8601 à **précision fixe** (microsecondes) avec suffixe ``Z``,
par exemple ``2026-09-24T12:00:00.000000Z``.

La précision fixe n'est pas cosmétique : les horodatages sont stockés sous forme de
chaînes et les filtres de l'API (``start_date``, ``end_date``) s'appuient sur des
comparaisons lexicographiques, qui ne sont correctes que si toutes les valeurs ont
exactement la même forme.

Référence : API.md §2.1 (dates ISO 8601, UTC).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

FORMAT_ISO = "%Y-%m-%dT%H:%M:%S.%fZ"


def utcnow() -> datetime:
    """Instant courant, conscient du fuseau (UTC)."""
    return datetime.now(timezone.utc)


def to_iso(value: Any) -> str | None:
    """Convertit une date en chaîne ISO 8601 UTC à précision fixe.

    ``None`` reste ``None``. Une chaîne est analysée puis re-formatée, afin de
    garantir une forme unique quelle que soit la source.
    """
    if value is None:
        return None
    if isinstance(value, str):
        analyse = parse_iso(value)
        if analyse is None:
            raise ValueError(f"Horodatage illisible : {value!r}")
        value = analyse
    if not isinstance(value, datetime):
        raise TypeError(f"Type de date non pris en charge : {type(value)!r}")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(FORMAT_ISO)


def parse_iso(value: Any) -> datetime | None:
    """Analyse une date ISO 8601 (tolérante sur la précision et le suffixe Z)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None

    texte = value.strip()
    candidats = [texte]
    if texte.endswith("Z"):
        candidats.append(texte[:-1] + "+00:00")
    for candidat in candidats:
        try:
            analyse = datetime.fromisoformat(candidat)
        except ValueError:
            continue
        return analyse if analyse.tzinfo else analyse.replace(tzinfo=timezone.utc)

    # Dernier recours : formes sans séparateur de fuseau
    for forme in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(texte, forme).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def iso_now() -> str:
    """Horodatage courant, prêt à être persisté."""
    return to_iso(utcnow())


def age_seconds(moment: Any, reference: datetime | None = None) -> float | None:
    """Âge en secondes d'un horodatage par rapport à maintenant (ou à ``reference``)."""
    analyse = parse_iso(moment) if not isinstance(moment, datetime) else moment
    if analyse is None:
        return None
    reference = reference or utcnow()
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - analyse).total_seconds()


def is_older_than(moment: Any, seconds: float, reference: datetime | None = None) -> bool:
    """Indique si ``moment`` est plus ancien que ``seconds`` secondes."""
    age = age_seconds(moment, reference)
    if age is None:
        return False
    return age > seconds


def add_seconds(moment: datetime, seconds: float) -> datetime:
    return moment + timedelta(seconds=seconds)


def humanize_duration(seconds: float | None) -> str:
    """Formate une durée en français, pour les messages d'événement."""
    if seconds is None:
        return "inconnue"
    secondes = int(max(0, seconds))
    if secondes < 60:
        return f"{secondes} s"
    minutes, reste = divmod(secondes, 60)
    if minutes < 60:
        return f"{minutes} min {reste} s" if reste else f"{minutes} min"
    heures, minutes = divmod(minutes, 60)
    if heures < 24:
        return f"{heures} h {minutes} min" if minutes else f"{heures} h"
    jours, heures = divmod(heures, 24)
    return f"{jours} j {heures} h" if heures else f"{jours} j"
