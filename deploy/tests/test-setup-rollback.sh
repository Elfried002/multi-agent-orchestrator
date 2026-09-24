#!/usr/bin/env bash
# =============================================================================
#  multi-agent-orchestrator — deploy/tests/test-setup-rollback.sh
#
#  Test unitaire de la logique d'annulation partielle de deploy/setup.sh :
#  lorsqu'une étape échoue après avoir remplacé une configuration préexistante,
#  l'ancienne configuration doit être RESTAURÉE et les fichiers créés par le
#  script retirés — sans jamais toucher aux autres fichiers du serveur.
#
#  Le script charge setup.sh en mode « bibliothèque » (MAO_LIB_ONLY=1) et
#  travaille dans une arborescence isolée : aucun chemin système réel n'est
#  utilisé.
#
#  Les fonctions d'assertion sont préfixées « t_ » : setup.sh définit ses
#  propres helpers et le chargement écraserait des noms génériques.
#
#  Utilisation : bash deploy/tests/test-setup-rollback.sh
#  Variable    : TEST_ROOT (défaut : ${TMPDIR:-/tmp}/mao-setup-rollback-test)
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
TEST_ROOT="${TEST_ROOT:-${TMPDIR:-/tmp}/mao-setup-rollback-test}"

T_CHECKS=0
T_FAILURES=0

t_say() { printf '\n=== %s ===\n' "$*"; }

t_check() { # $1 = description, reste = commande à exécuter
    local desc="$1"; shift
    T_CHECKS=$((T_CHECKS + 1))
    if "$@" >/dev/null 2>&1; then
        printf '  [PASS] %s\n' "$desc"
    else
        printf '  [FAIL] %s\n' "$desc"
        T_FAILURES=$((T_FAILURES + 1))
    fi
}

t_grep() { # $1 description, $2 motif attendu, $3 fichier
    T_CHECKS=$((T_CHECKS + 1))
    if grep -q -- "$2" "$3" 2>/dev/null; then
        printf '  [PASS] %s\n' "$1"
    else
        printf '  [FAIL] %s\n' "$1"
        T_FAILURES=$((T_FAILURES + 1))
    fi
}

t_not_grep() { # $1 description, $2 motif interdit, $3 fichier
    T_CHECKS=$((T_CHECKS + 1))
    if grep -q -- "$2" "$3" 2>/dev/null; then
        printf '  [FAIL] %s\n' "$1"
        T_FAILURES=$((T_FAILURES + 1))
    else
        printf '  [PASS] %s\n' "$1"
    fi
}

# --- Chargement de setup.sh en mode bibliothèque -----------------------------
export MAO_LIB_ONLY=1
export MAO_ETC_ROOT="${TEST_ROOT}/etc"
export MAO_OPT_ROOT="${TEST_ROOT}/opt"
export MAO_VAR_ROOT="${TEST_ROOT}/var"
export MAO_SKIP_USER_OPS=1
export MAO_SKIP_SYSTEMD=1
# shellcheck disable=SC1091
. "${REPO_ROOT}/deploy/setup.sh"

reset_tree() {
    rm -rf -- "$TEST_ROOT"
    mkdir -p "$NGINX_AVAILABLE_DIR" "$NGINX_ENABLED_DIR" "$NGINX_SNIPPETS_DIR"
    DOMAIN_LOWER="${DOMAIN_LOWER:-test.example.com}"
    set_derived_names
    # Variables consommées par les fonctions chargées depuis setup.sh : leur
    # remise à zéro est volontaire (shellcheck ne voit pas cet usage croisé).
    # shellcheck disable=SC2034
    ROLLBACK_FILES=()
    # shellcheck disable=SC2034
    ROLLBACK_RESTORES=()
    # shellcheck disable=SC2034
    NGINX_ACTIVATED=0
    # shellcheck disable=SC2034
    UNIT_CREATED_THIS_RUN=0
}

# =============================================================================
#  Scénario 1 — une configuration préexistante est remplacée puis restaurée
# =============================================================================
scenario_restore() {
    t_say "Scénario 1 — échec après remplacement d'une configuration préexistante"
    reset_tree

    local site="${NGINX_AVAILABLE_DIR}/orchestrator-test.example.com"
    local backup="${site}.bak.20260101T000000Z"
    local foreign="${NGINX_AVAILABLE_DIR}/wordpress.conf"

    printf 'ANCIENNE-CONFIG-DE-L-ADMINISTRATEUR\n' > "$site"
    cp -a -- "$site" "$backup"
    printf 'CONF-ETRANGERE-A-PRESERVER\n' > "$foreign"

    # Simulation de l'étape Nginx : sauvegarde enregistrée (restauration), puis
    # remplacement des fichiers, puis échec de « nginx -t ».
    register_rollback_restore "$backup" "$site"
    printf 'NOUVELLE-CONFIG-GENEREE-PAR-SETUP\n' > "$site"
    printf 'SNIPPET-GENERE-PAR-SETUP\n' > "$SNIPPET_FILE"
    register_rollback_file "$site"
    register_rollback_file "$SNIPPET_FILE"

    rollback_partial_state

    t_grep "l'ancienne configuration est restaurée" "ANCIENNE-CONFIG-DE-L-ADMINISTRATEUR" "$site"
    t_not_grep "la configuration générée a disparu" "NOUVELLE-CONFIG-GENEREE-PAR-SETUP" "$site"
    t_check "le snippet généré a été retiré" test ! -e "$SNIPPET_FILE"
    t_check "la sauvegarde horodatée est conservée" test -f "$backup"
    t_grep "le fichier étranger est intact" "CONF-ETRANGERE-A-PRESERVER" "$foreign"
}

# =============================================================================
#  Scénario 2 — installation neuve : les fichiers créés sont retirés
# =============================================================================
scenario_cleanup() {
    t_say "Scénario 2 — échec sur une installation neuve (aucune configuration préexistante)"
    reset_tree

    local site="${NGINX_AVAILABLE_DIR}/orchestrator-neuf.example.com"
    local foreign="${NGINX_ENABLED_DIR}/nextcloud.conf"
    printf 'AUTRE-SITE\n' > "$foreign"

    printf 'CONFIG-DU-PREMIER-RUN\n' > "$site"
    printf 'SNIPPET-DU-PREMIER-RUN\n' > "$SNIPPET_FILE"
    register_rollback_file "$site"
    register_rollback_file "$SNIPPET_FILE"

    rollback_partial_state

    t_check "le fichier de site créé a été retiré" test ! -e "$site"
    t_check "le snippet créé a été retiré" test ! -e "$SNIPPET_FILE"
    t_grep "le site étranger est intact" "AUTRE-SITE" "$foreign"
}

# =============================================================================
#  Scénario 3 — listes vides : aucune erreur (tableaux vides + « set -u »)
# =============================================================================
scenario_empty() {
    t_say "Scénario 3 — annulation sans aucun fichier enregistré"
    reset_tree

    local rc=0
    set +e
    rollback_partial_state
    rc=$?
    set -e
    printf '  code de retour : %s\n' "$rc"
    t_check "l'annulation partielle sans fichier enregistré ne provoque pas d'erreur" test "$rc" -eq 0
}

# =============================================================================
#  Scénario 4 — le rendu du gabarit reste fonctionnel après chargement
# =============================================================================
scenario_render() {
    t_say "Scénario 4 — rendu du gabarit toujours fonctionnel"
    reset_tree

    DOMAIN_LOWER="verif.example.com"
    set_derived_names
    local out="${TEST_ROOT}/render"
    install -d -m 0755 "$out"
    local rc=0
    set +e
    write_nginx_artifacts "$out" yes
    rc=$?
    set -e
    printf '  code de retour : %s\n' "$rc"
    t_check "le rendu du gabarit aboutit" test "$rc" -eq 0
    t_grep "server_name renseigné" "server_name verif.example.com;" "${out}/nginx-site.conf"
    t_grep "proxy vers le backend présent" "proxy_pass http://127.0.0.1:8000;" \
        "${out}/nginx-snippet.conf"
    t_grep "redirection HTTPS présente en mode TLS" "return 301 https://" "${out}/nginx-site.conf"

    local out_http="${TEST_ROOT}/render-http"
    install -d -m 0755 "$out_http"
    write_nginx_artifacts "$out_http" no
    t_grep "variante HTTP seule : inclusion du snippet" \
        "include .*orchestrator-verif.example.com.conf;" "${out_http}/nginx-site.conf"
    t_grep "variante HTTP seule : un seul bloc server" "^server {" "${out_http}/nginx-site.conf"
    t_grep "snippet : repli SPA vers index.html" "try_files \$uri \$uri/ /index.html;" \
        "${out_http}/nginx-snippet.conf"
    t_not_grep "aucun certificat dans la variante HTTP seule" "ssl_certificate" \
        "${out_http}/nginx-site.conf"
    t_not_grep "aucune redirection HTTPS dans la variante HTTP seule" "return 301 https://" \
        "${out_http}/nginx-site.conf"
}

main() {
    printf 'Test unitaire : annulation partielle et rendu de deploy/setup.sh\n'
    printf 'Racine de test : %s\n' "$TEST_ROOT"
    trap 'rm -rf -- "$TEST_ROOT"; echo "(arborescence de test supprimée)"' EXIT

    scenario_restore
    scenario_cleanup
    scenario_empty
    scenario_render

    printf '\n=========================================\n'
    printf 'Vérifications : %s — échecs : %s\n' "$T_CHECKS" "$T_FAILURES"
    if (( T_FAILURES > 0 )); then
        printf 'RÉSULTAT : ÉCHEC\n'
        exit 1
    fi
    printf 'RÉSULTAT : SUCCÈS\n'
}

main "$@"
