#!/usr/bin/env python3
"""Validation statique d'un fichier de configuration Nginx rendu.

Utilisé lorsque le binaire nginx n'est pas disponible (poste de développement
Windows, CI avant installation de nginx). Complète « nginx -t » mais ne le
remplace pas : la CI exécute les deux.

Contrôles effectués :
  * équilibre des accolades, en ignorant commentaires et chaînes entre guillemets ;
  * toute directive est terminée par « ; », « { » ou « } » ;
  * bloc « server {} » correctement délimité et directives obligatoires présentes ;
  * présence des en-têtes de sécurité, du proxy vers le backend, de la limite de
    taille de corps, des refus de chemins sensibles ;
  * en mode TLS : deux blocs server, écoute 443, redirection 301, HSTS ;
  * en mode HTTP seul : un seul bloc server, aucune référence de certificat ;
  * aucun placeholder « __XXX__ » non substitué.

Sortie : 0 si conforme, 1 sinon (les problèmes sont listés sur stdout).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SECURITY_HEADERS = (
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
)


class Problems(list):
    def add(self, message: str) -> None:
        self.append(message)


def strip_comments_and_track(line: str, in_quote: bool) -> tuple[str, bool]:
    """Retourne (contenu_sans_commentaire, etat_guillemet)."""
    out: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            out.append(line[i : i + 2])
            i += 2
            continue
        if ch == '"':
            in_quote = not in_quote
        elif ch == "#" and not in_quote:
            break
        out.append(ch)
        i += 1
    return "".join(out), in_quote


def analyse(path: Path, mode: str) -> tuple[Problems, int, list[str]]:
    """Retourne (problemes, nombre_de_blocs_server, directives_listen)."""
    problems = Problems()
    text = path.read_text(encoding="utf-8")

    depth = 0
    in_quote = False
    server_blocks = 0
    listens: list[str] = []
    lines = text.splitlines()

    for number, raw in enumerate(lines, start=1):
        code, in_quote = strip_comments_and_track(raw, in_quote)
        stripped = code.strip()
        if not stripped:
            continue

        for ch in stripped:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    problems.add(f"ligne {number} : accolade fermante sans ouvrante")
                    depth = 0

        if stripped.endswith(("{", "}", ";")):
            pass
        else:
            problems.add(
                f"ligne {number} : directive sans terminaison (« ; » manquant ?) : {stripped[:70]}"
            )

        if re.match(r"^server\s*\{", stripped):
            server_blocks += 1
        if stripped.startswith("listen"):
            listens.append(stripped)

    if in_quote:
        problems.add("chaîne entre guillemets non fermée")
    if depth != 0:
        problems.add(f"accolades déséquilibrées : niveau final {depth}")

    # --- Contrôles fonctionnels (attendus selon le fichier produit) ----------
    # Les directives partagées (proxy, en-têtes, statique, en-têtes de sécurité)
    # sont dans le snippet, inclus par chaque bloc server{} du site.
    expected_in_site = {
        "inclusion du snippet partagé": r"include\s+\S*orchestrator-[^;]+\.conf;",
        "écoute sur le port 80": r"^\s*listen\s+80;",
        "racine du challenge ACME": r"\.well-known/acme-challenge/",
        "journal d'accès dédié": r"access_log\s+\S*orchestrator-",
    }
    expected_in_snippet = {
        "proxy_pass vers le backend": r"proxy_pass\s+http://127\.0\.0\.1:8000;",
        "en-tête X-Forwarded-For": r"proxy_set_header\s+X-Forwarded-For\s",
        "en-tête X-Forwarded-Proto": r"proxy_set_header\s+X-Forwarded-Proto\s",
        "en-tête Host transmis": r"proxy_set_header\s+Host\s",
        "limite de taille de corps": r"client_max_body_size\s",
        "délai proxy en lecture": r"proxy_read_timeout\s",
        "refus des fichiers sensibles": r"deny all;",
        "racine du frontend compilé": r"root\s+\S*frontend/dist;",
        "repli SPA vers index.html": r"try_files\s+\$uri\s+\$uri/\s+/index\.html;",
    }
    expected = expected_in_snippet if mode == "snippet" else expected_in_site
    for label, pattern in expected.items():
        if not re.search(pattern, text, re.MULTILINE):
            problems.add(f"directive attendue absente : {label}")

    if mode == "snippet":
        for header in SECURITY_HEADERS:
            if header not in text:
                problems.add(f"en-tête de sécurité absent du snippet : {header}")
        if "Content-Security-Policy" not in text:
            problems.add("en-tête de sécurité absent du snippet : Content-Security-Policy")
    else:
        for header in SECURITY_HEADERS + ("Content-Security-Policy",):
            if header in text:
                problems.add(
                    f"{header} défini dans le site alors qu'il appartient au snippet "
                    "(risque de perte de l'héritage add_header dans les locations)"
                )

    if re.search(r"__[A-Z_]{2,}__", text):
        leftovers = sorted(set(re.findall(r"__[A-Z_]{2,}__", text)))
        problems.add(f"placeholder(s) non substitué(s) : {', '.join(leftovers)}")

    return problems, server_blocks, listens


def main() -> int:
    parser = argparse.ArgumentParser(description="Valide une configuration Nginx rendue.")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--mode", choices=("tls", "http", "snippet"), required=True,
                        help="mode de rendu attendu")
    parser.add_argument("--domain", default=None, help="domaine attendu dans server_name")
    args = parser.parse_args()

    failures = 0
    for path in args.files:
        if not path.is_file():
            print(f"[ERREUR] fichier absent : {path}")
            failures += 1
            continue

        problems, servers, listens = analyse(path, args.mode)
        text = path.read_text(encoding="utf-8")

        if args.mode == "tls":
            if servers != 2:
                problems.add(f"mode TLS : 2 blocs server{{}} attendus, {servers} trouvé(s)")
            if not any(re.match(r"^listen\s+443", item) for item in listens):
                problems.add("mode TLS : aucune écoute sur 443")
            if "return 301 https://" not in text:
                problems.add("mode TLS : redirection HTTP → HTTPS (301) absente")
            if "Strict-Transport-Security" not in text:
                problems.add("mode TLS : HSTS absent")
            if "ssl_certificate     /etc/letsencrypt/live/" not in text:
                problems.add("mode TLS : certificat Let's Encrypt non référencé")
        elif args.mode == "http":
            if servers != 1:
                problems.add(f"mode HTTP seul : 1 bloc server{{}} attendu, {servers} trouvé(s)")
            if "return 301 https://" in text:
                problems.add("mode HTTP seul : redirection HTTPS présente alors que TLS est inactif")
            if "ssl_certificate" in text:
                problems.add("mode HTTP seul : référence à un certificat TLS")
        else:  # snippet
            if servers != 0:
                problems.add("snippet : aucun bloc server{} ne doit y figurer")

        if args.domain:
            if f"server_name {args.domain};" not in text and args.mode != "snippet":
                problems.add(f"server_name {args.domain} absent")

        if problems:
            failures += 1
            print(f"[ÉCHEC] {path}")
            for problem in problems:
                print(f"    - {problem}")
        else:
            print(f"[ OK ] {path} — structure valide (mode {args.mode})")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
