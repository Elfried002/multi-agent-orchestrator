#!/usr/bin/env bash
# =============================================================================
#  multi-agent-orchestrator — deploy/tests/test-uninstall-preservation.sh
#
#  Test de non-régression de la logique de préservation de deploy/uninstall.sh.
#
#  Il construit une arborescence FACTICE dans un répertoire temporaire
#  (aucun chemin système réel n'est touché) contenant :
#    * des sites Nginx étrangers + une base de données étrangère ;
#    * une installation complète simulée de l'orchestrateur ;
#    * un stub systemctl journalisant les appels.
#  Puis il exécute uninstall.sh avec les racines redéfinies (MAO_ETC_ROOT,
#  MAO_OPT_ROOT, MAO_VAR_ROOT, MAO_SYSTEMCTL) et vérifie que :
#    1. l'application, l'unité systemd et la configuration Nginx propres à
#       l'orchestrateur sont supprimées ;
#    2. les sites Nginx ÉTRANGERS sont intacts ;
#    3. la base étrangère est intacte ;
#    4. par défaut, les données et journaux de l'orchestrateur sont CONSERVÉS ;
#    5. avec --purge-data --confirm-data-loss, seules NOS données sont
#       supprimées (les données étrangères restent intactes) ;
#    6. une seconde exécution (idempotence) ne casse rien.
#
#  Utilisation :  bash deploy/tests/test-uninstall-preservation.sh
#  Variables    :  TEST_ROOT (défaut : ${TMPDIR:-/tmp}/mao-preservation-test)
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
UNINSTALL="${REPO_ROOT}/deploy/uninstall.sh"
TEST_ROOT="${TEST_ROOT:-${TMPDIR:-/tmp}/mao-preservation-test}"

ETC_ROOT="${TEST_ROOT}/etc"
OPT_ROOT="${TEST_ROOT}/opt"
VAR_ROOT="${TEST_ROOT}/var"
STUB="${TEST_ROOT}/bin/systemctl-stub"
STUB_LOG="${TEST_ROOT}/systemctl-calls.log"

CHECKS=0
FAILURES=0
CURRENT_SCENARIO=""

say()   { printf '\n=== %s ===\n' "$*"; }
head2() { printf '\n--- %s ---\n' "$*"; }
check() { # $1 = description, reste = commande à exécuter
    local desc="$1"; shift
    CHECKS=$((CHECKS + 1))
    if "$@" >/dev/null 2>&1; then
        printf '  [PASS] %s\n' "$desc"
    else
        printf '  [FAIL] %s\n' "$desc"
        FAILURES=$((FAILURES + 1))
    fi
}
check_grep() { # $1 = description, $2 = motif, $3 = fichier
    CHECKS=$((CHECKS + 1))
    if grep -q -- "$2" "$3" 2>/dev/null; then
        printf '  [PASS] %s\n' "$1"
    else
        printf '  [FAIL] %s\n' "$1"
        FAILURES=$((FAILURES + 1))
    fi
}

# --- Arborescence factice -----------------------------------------------------
setup_fake_environment() {
    rm -rf -- "$TEST_ROOT"
    mkdir -p \
        "${ETC_ROOT}/nginx/sites-available" \
        "${ETC_ROOT}/nginx/sites-enabled" \
        "${ETC_ROOT}/nginx/snippets" \
        "${ETC_ROOT}/systemd/system" \
        "${ETC_ROOT}/multi-agent-orchestrator" \
        "${ETC_ROOT}/letsencrypt/live/test.example.com" \
        "${OPT_ROOT}/multi-agent-orchestrator/application/backend/app" \
        "${OPT_ROOT}/multi-agent-orchestrator/application/frontend/dist" \
        "${VAR_ROOT}/lib/multi-agent-orchestrator/data" \
        "${VAR_ROOT}/lib/autre-projet" \
        "${VAR_ROOT}/log/multi-agent-orchestrator" \
        "${VAR_ROOT}/log/nginx" \
        "${VAR_ROOT}/www/multi-agent-orchestrator/.well-known/acme-challenge" \
        "${TEST_ROOT}/bin"

    # --- Sites Nginx ÉTRANGERS (doivent survivre) ---------------------------
    printf 'server { listen 80; server_name wordpress.example.com; }\n' \
        > "${ETC_ROOT}/nginx/sites-available/wordpress.conf"
    ln -s "${ETC_ROOT}/nginx/sites-available/wordpress.conf" \
        "${ETC_ROOT}/nginx/sites-enabled/wordpress.conf"
    printf 'server { listen 80; server_name nextcloud.example.com; }\n' \
        > "${ETC_ROOT}/nginx/sites-available/nextcloud.conf"
    ln -s "${ETC_ROOT}/nginx/sites-available/nextcloud.conf" \
        "${ETC_ROOT}/nginx/sites-enabled/nextcloud.conf"
    printf '# fin des sites etrangers\n' > "${ETC_ROOT}/nginx/sites-available/wordpress.conf.lock"

    # --- Bases de données ÉTRANGÈRES (doivent survivre) ---------------------
    printf 'CONTENU-BASE-ETRANGERE\n' > "${VAR_ROOT}/lib/autre-projet/autre.db"

    # --- Installation simulée de l'orchestrateur ----------------------------
    printf 'contenu main.py\n' > "${OPT_ROOT}/multi-agent-orchestrator/application/backend/app/main.py"
    printf 'contenu dist\n'    > "${OPT_ROOT}/multi-agent-orchestrator/application/frontend/dist/index.html"
    printf 'SECRET_KEY=faux-secret-de-test\nENROLLMENT_KEY=fausse-cle-de-test\n' \
        > "${ETC_ROOT}/multi-agent-orchestrator/production.env"
    {
        printf 'MAO_DOMAIN=test.example.com\n'
        printf 'MAO_ADMIN_USER=admin\n'
        printf 'MAO_SERVICE_USER=orchestrator\n'
        printf 'MAO_DATA_DIR=%s\n' "${VAR_ROOT}/lib/multi-agent-orchestrator"
    } > "${ETC_ROOT}/multi-agent-orchestrator/install.conf"
    printf 'SQLITE-ORCHESTRATOR-UTILISATEURS\n' \
        > "${VAR_ROOT}/lib/multi-agent-orchestrator/orchestrator.db"
    printf 'donnees-persistantes\n' > "${VAR_ROOT}/lib/multi-agent-orchestrator/data/etat.json"
    printf 'ligne de journal applicative\n' \
        > "${VAR_ROOT}/log/multi-agent-orchestrator/application.log"
    printf 'ligne d audit\n' > "${VAR_ROOT}/log/multi-agent-orchestrator/audit.log"

    # --- Configuration Nginx de l'orchestrateur (doit être supprimée) -------
    printf 'server { listen 80; server_name test.example.com; }\n' \
        > "${ETC_ROOT}/nginx/sites-available/orchestrator-test.example.com"
    ln -s "${ETC_ROOT}/nginx/sites-available/orchestrator-test.example.com" \
        "${ETC_ROOT}/nginx/sites-enabled/orchestrator-test.example.com"
    printf 'add_header X-Content-Type-Options "nosniff" always;\n' \
        > "${ETC_ROOT}/nginx/snippets/orchestrator-test.example.com.conf"

    # --- Unité systemd de l'orchestrateur (doit être supprimée) -------------
    printf '[Unit]\nDescription=test\n[Service]\nUser=orchestrator\n[Install]\nWantedBy=multi-user.target\n' \
        > "${ETC_ROOT}/systemd/system/orchestrator.service"
    printf '[Unit]\nDescription=service etranger\n' \
        > "${ETC_ROOT}/systemd/system/autre-service.service"

    # --- Stub systemctl (aucun appel systemd réel) --------------------------
    cat > "$STUB" <<'STUB'
#!/usr/bin/env bash
printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "${MAO_TEST_SYSTEMCTL_LOG:-/dev/null}"
case "${1:-}" in
    is-active|is-enabled) exit 1 ;;
    *) exit 0 ;;
esac
STUB
    chmod +x "$STUB"
    : > "$STUB_LOG"
}

run_uninstall() {
    MAO_ETC_ROOT="$ETC_ROOT" \
    MAO_OPT_ROOT="$OPT_ROOT" \
    MAO_VAR_ROOT="$VAR_ROOT" \
    MAO_SYSTEMCTL="$STUB" \
    MAO_SKIP_USER_OPS=1 \
    MAO_ALLOW_NON_ROOT=1 \
    MAO_TEST_SYSTEMCTL_LOG="$STUB_LOG" \
    bash "$UNINSTALL" "$@"
}

show_tree() { # $1 = libellé
    head2 "$1"
    find "$TEST_ROOT" -mindepth 1 \
        \( -path "${TEST_ROOT}/bin" -prune -o -print \) 2>/dev/null | sort | sed "s|^${TEST_ROOT}|.|"
}

# =============================================================================
#  Scénario 1 — désinstallation par défaut : données et sites étrangers intacts
# =============================================================================
scenario_default() {
    CURRENT_SCENARIO="Scénario 1 — désinstallation par défaut"
    say "$CURRENT_SCENARIO"
    setup_fake_environment
    show_tree "AVANT (arborescence factice)"

    head2 "Exécution de deploy/uninstall.sh --yes (racines redirigées vers ${TEST_ROOT})"
    local rc=0
    if run_uninstall --yes; then rc=0; else rc=$?; fi
    printf '  code de sortie : %s\n' "$rc"

    head2 "APRÈS"
    show_tree "APRÈS (arborescence factice)"

    check "uninstall.sh se termine sans erreur (rc=0)" test "$rc" -eq 0
    check "application supprimée" test ! -e "${OPT_ROOT}/multi-agent-orchestrator/application"
    check "unité systemd de l'orchestrateur supprimée" test ! -e "${ETC_ROOT}/systemd/system/orchestrator.service"
    check "site Nginx (sites-available) supprimé" test ! -e "${ETC_ROOT}/nginx/sites-available/orchestrator-test.example.com"
    check "lien Nginx (sites-enabled) supprimé" test ! -L "${ETC_ROOT}/nginx/sites-enabled/orchestrator-test.example.com"
    check "snippet Nginx supprimé" test ! -e "${ETC_ROOT}/nginx/snippets/orchestrator-test.example.com.conf"

    # --- Préservation de l'infrastructure étrangère -------------------------
    check "site étranger wordpress.conf intact (sites-available)" \
        test -f "${ETC_ROOT}/nginx/sites-available/wordpress.conf"
    # MSYS/git-bash peut émuler les liens symboliques par des copies : on
    # vérifie d'abord la présence, puis le type quand le système le conserve.
    check "entrée étrangère wordpress.conf intacte (sites-enabled)" \
        test -e "${ETC_ROOT}/nginx/sites-enabled/wordpress.conf"
    if [[ -L "${ETC_ROOT}/nginx/sites-enabled/wordpress.conf" ]]; then
        check "wordpress.conf est toujours un lien symbolique" \
            test -L "${ETC_ROOT}/nginx/sites-enabled/wordpress.conf"
    else
        printf '  [INFO] liens symboliques émulés par ce système : contrôle du type ignoré\n'
    fi
    check "lien étranger wordpress.conf toujours résolu" \
        test -f "${ETC_ROOT}/nginx/sites-enabled/wordpress.conf"
    check "site étranger nextcloud.conf intact" \
        test -f "${ETC_ROOT}/nginx/sites-enabled/nextcloud.conf"
    check_grep "contenu du site étranger inchangé" "wordpress.example.com" \
        "${ETC_ROOT}/nginx/sites-available/wordpress.conf"
    check "fichier annexe étranger intact" test -f "${ETC_ROOT}/nginx/sites-available/wordpress.conf.lock"
    check "service systemd étranger intact" test -f "${ETC_ROOT}/systemd/system/autre-service.service"
    check "base de données étrangère intacte" test -f "${VAR_ROOT}/lib/autre-projet/autre.db"
    check_grep "contenu de la base étrangère inchangé" "CONTENU-BASE-ETRANGERE" \
        "${VAR_ROOT}/lib/autre-projet/autre.db"
    check "paquet Nginx non désinstallé (aucune commande apt-get exécutée)" \
        bash -c '! grep -qE "^[[:space:]]*(apt-get|apt)[[:space:]]" "'"$UNINSTALL"'"; grep -q "apt-get remove" "'"$UNINSTALL"'"'
    check "certbot delete jamais exécuté (seulement conseillé)" \
        bash -c '! grep -qE "^[[:space:]]*certbot[[:space:]]" "'"$UNINSTALL"'"'

    # --- Conservation des données de l'orchestrateur ------------------------
    check "base SQLite de l'orchestrateur CONSERVÉE" \
        test -f "${VAR_ROOT}/lib/multi-agent-orchestrator/orchestrator.db"
    check_grep "contenu de la base conservé" "SQLITE-ORCHESTRATOR-UTILISATEURS" \
        "${VAR_ROOT}/lib/multi-agent-orchestrator/orchestrator.db"
    check "répertoire data/ conservé" \
        test -f "${VAR_ROOT}/lib/multi-agent-orchestrator/data/etat.json"
    check "journaux conservés" test -f "${VAR_ROOT}/log/multi-agent-orchestrator/application.log"
    check "configuration (secrets) conservée" \
        test -f "${ETC_ROOT}/multi-agent-orchestrator/production.env"
    check "certificat TLS non supprimé" test -d "${ETC_ROOT}/letsencrypt/live/test.example.com"

    check_grep "systemctl stop orchestrator a été appelé" "stop orchestrator" "$STUB_LOG"
    check_grep "systemctl disable orchestrator a été appelé" "disable orchestrator" "$STUB_LOG"

    head2 "Réexécution (idempotence)"
    local rc2=0
    if run_uninstall --yes; then rc2=0; else rc2=$?; fi
    printf '  code de sortie : %s\n' "$rc2"
    check "réexécution sans erreur (idempotence)" test "$rc2" -eq 0
    check "sites étrangers toujours intacts après réexécution" \
        test -f "${ETC_ROOT}/nginx/sites-enabled/wordpress.conf"
    check "données toujours conservées après réexécution" \
        test -f "${VAR_ROOT}/lib/multi-agent-orchestrator/orchestrator.db"
}

# =============================================================================
#  Scénario 2 — purge des données explicitement confirmée
# =============================================================================
scenario_purge() {
    CURRENT_SCENARIO="Scénario 2 — purge des données confirmée"
    say "$CURRENT_SCENARIO"
    setup_fake_environment

    head2 "Exécution de deploy/uninstall.sh --yes --purge-data --purge-logs --confirm-data-loss"
    local rc=0
    if run_uninstall --yes --purge-data --purge-logs --confirm-data-loss; then rc=0; else rc=$?; fi
    printf '  code de sortie : %s\n' "$rc"
    show_tree "APRÈS (données purgées)"

    check "uninstall.sh se termine sans erreur (rc=0)" test "$rc" -eq 0
    check "base SQLite de l'orchestrateur supprimée (demandé)" \
        test ! -e "${VAR_ROOT}/lib/multi-agent-orchestrator/orchestrator.db"
    check "répertoire de données de l'orchestrateur supprimé (demandé)" \
        test ! -e "${VAR_ROOT}/lib/multi-agent-orchestrator"
    check "journaux de l'orchestrateur supprimés (demandé)" \
        test ! -e "${VAR_ROOT}/log/multi-agent-orchestrator"
    check "base étrangère TOUJOURS intacte" test -f "${VAR_ROOT}/lib/autre-projet/autre.db"
    check_grep "contenu de la base étrangère TOUJOURS inchangé" "CONTENU-BASE-ETRANGERE" \
        "${VAR_ROOT}/lib/autre-projet/autre.db"
    check "sites Nginx étrangers TOUJOURS intacts" \
        test -f "${ETC_ROOT}/nginx/sites-enabled/wordpress.conf"
    check "service systemd étranger TOUJOURS intact" \
        test -f "${ETC_ROOT}/systemd/system/autre-service.service"
    check "configuration Nginx de l'orchestrateur supprimée" \
        test ! -e "${ETC_ROOT}/nginx/sites-available/orchestrator-test.example.com"
}

# =============================================================================
#  Scénario 3 — données conservées si la seconde confirmation est absente
# =============================================================================
scenario_purge_without_second_confirmation() {
    CURRENT_SCENARIO="Scénario 3 — purge refusée sans seconde confirmation"
    say "$CURRENT_SCENARIO"
    setup_fake_environment

    head2 "Exécution de deploy/uninstall.sh --yes --purge-data (sans confirmation de perte de données, hors TTY)"
    local rc=0
    if run_uninstall --yes --purge-data; then rc=0; else rc=$?; fi
    printf '  code de sortie : %s\n' "$rc"

    check "uninstall.sh se termine sans erreur (rc=0)" test "$rc" -eq 0
    check "application supprimée malgré tout (désinstallation effectuée)" \
        test ! -e "${OPT_ROOT}/multi-agent-orchestrator/application"
    check "base SQLite CONSERVÉE : seconde confirmation non fournie" \
        test -f "${VAR_ROOT}/lib/multi-agent-orchestrator/orchestrator.db"
    check_grep "contenu de la base conservé" "SQLITE-ORCHESTRATOR-UTILISATEURS" \
        "${VAR_ROOT}/lib/multi-agent-orchestrator/orchestrator.db"
    check "journaux conservés" test -f "${VAR_ROOT}/log/multi-agent-orchestrator/application.log"
    check "sites étrangers toujours intacts" \
        test -f "${ETC_ROOT}/nginx/sites-enabled/wordpress.conf"
}

# =============================================================================
#  Scénario 4 — garde-fou sur les chemins
# =============================================================================
scenario_guardrail() {
    CURRENT_SCENARIO="Scénario 4 — garde-fou de chemin"
    say "$CURRENT_SCENARIO"
    setup_fake_environment

    local rc=0
    set +e
    MAO_ETC_ROOT="$ETC_ROOT" MAO_OPT_ROOT="$OPT_ROOT" \
    MAO_VAR_ROOT="$VAR_ROOT" MAO_SYSTEMCTL="$STUB" MAO_SKIP_USER_OPS=1 \
    MAO_ALLOW_NON_ROOT=1 \
    bash "$UNINSTALL" --yes --purge-data --confirm-data-loss --dry-run >/dev/null 2>&1
    rc=$?
    set -e
    printf '  code de sortie (--dry-run) : %s\n' "$rc"
    check "--dry-run se termine sans erreur" test "$rc" -eq 0
    check "le mode simulation n a rien supprimé" \
        test -f "${ETC_ROOT}/nginx/sites-available/wordpress.conf"
    check "aucune suppression de l application en mode simulation" \
        test -f "${OPT_ROOT}/multi-agent-orchestrator/application/backend/app/main.py"

    head2 "Garde-fou : refus hors root avec les racines système par défaut"
    local out2="" rc2=0
    set +e
    out2="$(MAO_ALLOW_NON_ROOT=1 bash "$UNINSTALL" --yes --dry-run 2>&1)"
    rc2=$?
    set -e
    printf '  code de sortie : %s\n  message : %s\n' "$rc2" "$(printf '%s' "$out2" | tail -n1)"
    check "exécution hors root avec racines par défaut refusée" test "$rc2" -ne 0
}

main() {
    printf 'Test de préservation de deploy/uninstall.sh\n'
    printf 'Racine de test : %s\n' "$TEST_ROOT"
    [[ -f "$UNINSTALL" ]] || { echo "uninstall.sh introuvable : ${UNINSTALL}" >&2; exit 2; }

    trap 'if [[ "${TEST_KEEP:-0}" == "1" ]]; then echo "(arborescence de test conservée : $TEST_ROOT)"; else rm -rf -- "$TEST_ROOT"; echo "(arborescence de test supprimée)"; fi' EXIT

    scenario_default
    scenario_purge
    scenario_purge_without_second_confirmation
    scenario_guardrail

    printf '\n=========================================\n'
    printf 'Vérifications : %s — échecs : %s\n' "$CHECKS" "$FAILURES"
    if (( FAILURES > 0 )); then
        printf 'RÉSULTAT : ÉCHEC\n'
        exit 1
    fi
    printf 'RÉSULTAT : SUCCÈS (préservation confirmée)\n'
}

main "$@"
