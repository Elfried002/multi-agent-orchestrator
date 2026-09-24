#!/usr/bin/env bash
# =============================================================================
#  Multi-Agent Orchestrator — deploy/tests/test-ubuntu-container.sh
#
#  Banc d'essai REPRODUCTIBLE de « deploy/setup.sh » sur un Ubuntu LTS VIERGE
#  (ubuntu:24.04 par défaut, autre version via MAO_TEST_IMAGE_BASE)
#  (conteneur Docker exécutant systemd). Il répond à une seule question :
#  après exécution du script officiel sur un système où rien n'est installé,
#  l'orchestrateur est-il réellement opérationnel ?
#
#  Ce que fait le banc d'essai :
#    1. construit une image Ubuntu LTS (24.04 par défaut) contenant seulement systemd : ni
#       python3, ni curl, ni node/npm, ni nginx, ni certbot, ni sqlite3 — c'est
#       l'étape 5 de setup.sh qui doit TOUT installer (démarrage à froid réel) ;
#    2. démarre un conteneur avec systemd en PID 1 et y copie le projet en
#       excluant frontend/dist et frontend/node_modules : le frontend doit être
#       compilé par setup.sh (le backend sert frontend/dist) ;
#    3. exécute de bout en bout dans un pseudo-terminal :
#         bash deploy/setup.sh --domain <domaine> --no-https --yes \
#              --admin-user <compte>
#       le mot de passe administrateur étant généré à l'exécution puis transmis
#       sur l'ENTRÉE STANDARD (jamais en argument, jamais dans un fichier, jamais
#       affiché : il est masqué dans toutes les sorties du banc d'essai) ;
#    4. vérifie par des commandes réelles : service systemd actif et activé,
#       GET /health = 200 (direct et via Nginx), page d'accueil HTML et bundle JS
#       servis par Nginx, base SQLite initialisée, compte administrateur présent
#       PUIS authentifié (POST /api/v1/auth/login = 200, mot de passe erroné
#       = 401), permissions restrictives, absence d'erreur dans les journaux ;
#    5. réexécute setup.sh après avoir installé npm 11 (dont la politique
#       « allow-scripts » bloque par défaut les scripts d'installation, ce qui
#       casse l'installation d'esbuild, moteur de Vite) : preuve d'idempotence
#       ET de la prise en charge de npm 11 ;
#    6. exécute deploy/update.sh --yes (mise à jour réelle, données conservées).
#
#  Chaque contrôle affiche OK ou ÉCHEC. Le code de sortie final vaut 0
#  uniquement si TOUS les contrôles ont réussi.
#
#  Utilisation :
#    bash deploy/tests/test-ubuntu-container.sh
#    bash deploy/tests/test-ubuntu-container.sh --keep --out /tmp/mao-bench
#    bash deploy/tests/test-ubuntu-container.sh --no-update --no-npm11
#
#  Prérequis : Docker opérationnel (« docker info » répond) et accès Internet
#  (dépôts apt, NodeSource, PyPI, registry.npmjs.org). Durée typique : 10-25 min.
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"

IMAGE_BASE="${MAO_TEST_IMAGE_BASE:-ubuntu:24.04}"
IMAGE_TEST="${MAO_TEST_IMAGE:-mao-ubuntu-setup-test:24.04}"
CONTAINER="${MAO_TEST_CONTAINER:-mao-setup-bench}"
# Étiquette de version affichée (ubuntu:22.04 -> « 22.04 ») : le banc d'essai
# peut être rejoué sur une autre version LTS via MAO_TEST_IMAGE_BASE.
RELEASE="${IMAGE_BASE##*:}"
[[ -n "$RELEASE" && "$RELEASE" != "$IMAGE_BASE" ]] || RELEASE="?"
DOMAIN="${MAO_TEST_DOMAIN:-orchestrator.test}"
ADMIN_USER="${MAO_TEST_ADMIN_USER:-admin}"
PASSWORD_DELAY="${MAO_TEST_PASSWORD_DELAY:-45}"
RUN_TIMEOUT="${MAO_TEST_RUN_TIMEOUT:-3600}"
KEEP=0
RUN_UPDATE=1
RUN_NPM11=1
OUT_DIR=""

APP_DIR="/opt/multi-agent-orchestrator/application"
ENV_FILE="/etc/multi-agent-orchestrator/production.env"
DB_PATH="/var/lib/multi-agent-orchestrator/orchestrator.db"
BACKUP_DIR="/var/lib/multi-agent-orchestrator/backups"

PASS_COUNT=0
FAIL_COUNT=0
FAILED_CHECKS=()
PASSWORD=""
SETUP_LOG=""
SETUP_LOG2=""
UPDATE_LOG=""

if [[ -t 1 ]]; then
    C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'
    C_BLUE=$'\033[0;36m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
    C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''; C_OFF=''
fi

log_step()   { printf '\n%s=== %s ===%s\n' "$C_BOLD" "$*" "$C_OFF"; }
log_info()   { printf '%s[INFO]%s %s\n' "$C_BLUE" "$C_OFF" "$*"; }
log_detail() { printf '         %s\n' "$*"; }
die()        { printf '%s[ÉCHEC]%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Banc d'essai de deploy/setup.sh sur un Ubuntu LTS VIERGE (conteneur Docker).

Usage :
  bash deploy/tests/test-ubuntu-container.sh [options]

Options :
  --out <dir>        Répertoire des journaux (défaut : mktemp -d).
  --domain <fqdn>    Domaine de test (défaut : orchestrator.test, résolu vers
                     127.0.0.1 dans le conteneur via --add-host).
  --admin-user <n>   Compte administrateur de test (défaut : admin).
  --keep             Conserver le conteneur et l'image à la fin.
  --no-update        Ne pas tester deploy/update.sh.
  --no-npm11         Ne pas tester la politique « allow-scripts » de npm 11.
  -h, --help         Afficher cette aide.

Aucun secret n'est écrit dans un fichier : le mot de passe administrateur de test
est généré à l'exécution, transmis sur l'entrée standard de setup.sh et masqué
dans les journaux produits par ce banc d'essai.
EOF
}

parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            --out)         [[ -n "${2:-}" ]] || die "--out attend un répertoire"; OUT_DIR="$2"; shift 2 ;;
            --domain)      [[ -n "${2:-}" ]] || die "--domain attend une valeur"; DOMAIN="$2"; shift 2 ;;
            --admin-user)  [[ -n "${2:-}" ]] || die "--admin-user attend une valeur"; ADMIN_USER="$2"; shift 2 ;;
            --keep)        KEEP=1; shift ;;
            --no-update)   RUN_UPDATE=0; shift ;;
            --no-npm11)    RUN_NPM11=0; shift ;;
            -h|--help)     usage; exit 0 ;;
            *)             die "Option inconnue : $1 (voir --help)" ;;
        esac
    done
}

# ---------------------------------------------------------------------------
#  Contrôles : chacun imprime OK ou ÉCHEC et alimente le bilan final
# ---------------------------------------------------------------------------
ok_check() {
    local name="$1" detail="${2:-}"
    printf '  %s[ OK ]%s %s' "$C_GREEN" "$C_OFF" "$name"
    [[ -n "$detail" ]] && printf ' — %s' "$detail"
    printf '\n'
    PASS_COUNT=$((PASS_COUNT + 1))
}

ko_check() {
    local name="$1" detail="${2:-}"
    printf '  %s[ÉCHEC]%s %s' "$C_RED" "$C_OFF" "$name"
    [[ -n "$detail" ]] && printf ' — %s' "$detail"
    printf '\n'
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_CHECKS+=("$name")
}

# check_remote <nom> <regex attendue> <commande…> : exécute la commande DANS le
# conteneur et vérifie le code de sortie ET l'expression régulière sur la sortie.
check_remote() {
    local name="$1" regex="$2"; shift 2
    local out="" rc=0
    out="$(docker exec "$CONTAINER" "$@" 2>&1)" || rc=$?
    if (( rc == 0 )) && printf '%s' "$out" | grep -Eq -- "$regex"; then
        ok_check "$name" "$(printf '%s' "$out" | tr -d '\r' | head -n 1 | cut -c1-140)"
    else
        ko_check "$name" "code=${rc} ; sortie=$(printf '%s' "$out" | tr -d '\r' | head -n 2 | tr '\n' ' ' | cut -c1-200)"
    fi
}

# check_log <nom> <motif littéral> <fichier>
check_log() {
    local name="$1" pattern="$2" file="$3"
    if [[ -s "$file" ]] && grep -qF -- "$pattern" "$file"; then
        ok_check "$name"
    else
        ko_check "$name" "motif absent de $(basename "$file") : « ${pattern} »"
    fi
}

# check_absent <nom> <motif littéral> <fichier>
check_absent() {
    local name="$1" pattern="$2" file="$3"
    if [[ -s "$file" ]] && grep -qF -- "$pattern" "$file"; then
        ko_check "$name" "motif indésirable présent : « ${pattern} »"
    else
        ok_check "$name"
    fi
}

check_exit() { # <nom> <code> <attendu>
    local name="$1" rc="$2" expected="$3"
    if [[ "$rc" == "$expected" ]]; then
        ok_check "$name" "code de sortie ${rc}"
    else
        ko_check "$name" "code de sortie ${rc} (attendu ${expected})"
    fi
}

check_eq() { # <nom> <valeur> <attendue>
    local name="$1" value="$2" expected="$3"
    if [[ "$value" == "$expected" ]]; then
        ok_check "$name" "$value"
    else
        ko_check "$name" "« ${value} » (attendu « ${expected} »)"
    fi
}

# ---------------------------------------------------------------------------
#  Journaux : le mot de passe est systématiquement masqué
# ---------------------------------------------------------------------------
scrub_log() { # <fichier brut> <fichier propre>
    local raw="$1" clean="$2"
    sed -e 's/\x1b\[[0-9;]*[A-Za-z]//g' -e 's/\r/\n/g' "$raw" \
        | grep -vE '^[[:space:]]*$' \
        | sed -e "s|${PASSWORD}|[mot de passe masqué]|g" \
        > "$clean"
    rm -f -- "$raw"
}

show_log_extract() { # <fichier> [nb lignes]
    local file="$1" lines="${2:-45}"
    grep -E '^\[|^=== |Frontend|Node\.js|esbuild|sqlite3|npm |Reverse proxy|Service actif' "$file" \
        | grep -vE '^\[INFO\] déjà installé' \
        | tail -n "$lines" || true
}

# ---------------------------------------------------------------------------
#  1. Vérifications d'environnement hôte
# ---------------------------------------------------------------------------
preflight() {
    log_step "Banc d'essai Ubuntu ${RELEASE} — deploy/setup.sh de bout en bout"
    log_info "Projet           : ${REPO_ROOT}"
    log_info "Domaine de test  : ${DOMAIN} (résolu vers 127.0.0.1 dans le conteneur)"
    log_info "Compte admin     : ${ADMIN_USER} (mot de passe généré, jamais affiché)"

    command -v docker >/dev/null 2>&1 || die "docker est introuvable : ce banc d'essai exige Docker."
    docker info >/dev/null 2>&1 || die "« docker info » ne répond pas : démarrez Docker Desktop puis relancez."
    log_info "Docker           : version serveur $(docker version --format '{{.Server.Version}}' 2>/dev/null || printf 'inconnue')"

    [[ -d "${REPO_ROOT}/backend" && -d "${REPO_ROOT}/frontend" ]] \
        || die "Projet invalide : ${REPO_ROOT} ne contient pas backend/ et frontend/."
    [[ -f "${REPO_ROOT}/deploy/setup.sh" ]] || die "deploy/setup.sh introuvable."

    if [[ -z "$OUT_DIR" ]]; then
        OUT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mao-bench.XXXXXX")"
    else
        mkdir -p "$OUT_DIR"
        OUT_DIR="$(cd -- "$OUT_DIR" && pwd -P)"
    fi
    SETUP_LOG="${OUT_DIR}/setup-run1.log"
    SETUP_LOG2="${OUT_DIR}/setup-run2-npm11.log"
    UPDATE_LOG="${OUT_DIR}/update.log"
    log_info "Journaux         : ${OUT_DIR}"
}

# ---------------------------------------------------------------------------
#  2. Image Ubuntu LTS (24.04 par défaut) + systemd (aucun outil applicatif préinstallé)
# ---------------------------------------------------------------------------
build_image() {
    log_step "Construction de l'image Ubuntu ${RELEASE} (systemd uniquement)"
    local ctx build_ctx
    ctx="$(mktemp -d "${TMPDIR:-/tmp}/mao-img.XXXXXX")"
    cat > "${ctx}/Dockerfile" <<EOF
# Image de test minimale : Ubuntu ${IMAGE_BASE#*:} + systemd, RIEN de plus.
# Ni python3, ni curl, ni node/npm, ni nginx, ni certbot, ni sqlite3 :
# l'étape 5 de setup.sh doit tout installer (démarrage à froid réel).
FROM ${IMAGE_BASE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \\
 && apt-get install -y -qq --no-install-recommends systemd systemd-sysv dbus >/dev/null \\
 && rm -rf /var/lib/apt/lists/*
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
EOF
    build_ctx="$ctx"
    if command -v cygpath >/dev/null 2>&1; then
        build_ctx="$(cygpath -m "$ctx")"
    fi
    if ! docker build -t "$IMAGE_TEST" "$build_ctx" > "${OUT_DIR}/docker-build.log" 2>&1; then
        tail -n 20 "${OUT_DIR}/docker-build.log" >&2
        rm -rf -- "$ctx"
        die "Échec de la construction de l'image de test."
    fi
    rm -rf -- "$ctx"
    log_info "Image de test prête : ${IMAGE_TEST}"
}

# ---------------------------------------------------------------------------
#  3. Conteneur : systemd PID 1, domaine résolu localement
# ---------------------------------------------------------------------------
start_container() {
    log_step "Démarrage du conteneur Ubuntu ${RELEASE} vierge (systemd)"
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$CONTAINER" \
        --privileged --cgroupns=host \
        -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
        --tmpfs /run --tmpfs /tmp \
        --add-host "${DOMAIN}:127.0.0.1" \
        "$IMAGE_TEST" /sbin/init >/dev/null
    log_info "Conteneur démarré : ${CONTAINER}"

    local i state=""
    for ((i = 0; i < 60; i++)); do
        state="$(docker exec "$CONTAINER" systemctl is-system-running 2>/dev/null || true)"
        [[ "$state" == "running" || "$state" == "degraded" ]] && break
        sleep 1
    done
    if [[ "$state" != "running" && "$state" != "degraded" ]]; then
        printf '%s\n' "$state" >&2
        die "systemd n'est pas opérationnel dans le conteneur (état : ${state:-inconnu})."
    fi
    log_info "systemd opérationnel (is-system-running = ${state})."

    local preinstalled=""
    preinstalled="$(docker exec "$CONTAINER" bash -c \
        'for c in python3 curl node npm nginx certbot sqlite3 git; do command -v "$c" 2>/dev/null || true; done' || true)"
    if [[ -n "$preinstalled" ]]; then
        log_info "Outils déjà présents dans l'image de base : $(printf '%s' "$preinstalled" | tr '\n' ' ')"
    else
        log_info "Contrôle préalable : aucun outil applicatif préinstallé — l'étape 5 fait tout."
    fi
}

# ---------------------------------------------------------------------------
#  4. Copie du projet (frontend/dist et node_modules EXCLUS)
# ---------------------------------------------------------------------------
copy_project() {
    log_step "Copie du projet dans /project (frontend/dist et node_modules exclus)"
    docker exec "$CONTAINER" mkdir -p /project
    (
        cd -- "$REPO_ROOT" || exit 1
        tar -cf - \
            --exclude='./.git' \
            --exclude='*/.git' \
            --exclude='*/.venv' \
            --exclude='*/node_modules' \
            --exclude='*/__pycache__' \
            --exclude='*.pyc' \
            --exclude='./frontend/dist' \
            --exclude='*/.env' \
            --exclude='./.pytest_cache' \
            .
    ) | docker exec -i "$CONTAINER" tar -C /project -xf -
    local size files
    size="$(docker exec "$CONTAINER" du -sh /project | awk '{print $1}')"
    files="$(docker exec "$CONTAINER" bash -c 'find /project -type f | wc -l' | tr -d ' ')"
    log_info "Projet copié : ${files} fichiers, ${size}."
    if docker exec "$CONTAINER" test -e /project/frontend/dist/index.html; then
        die "frontend/dist a été copié : le banc d'essai ne prouverait pas la compilation par setup.sh."
    fi
    log_info "Contrôle préalable : aucun frontend/dist dans les sources copiées."
}

# ---------------------------------------------------------------------------
#  5. Exécution de setup.sh (pseudo-terminal, mot de passe sur stdin)
# ---------------------------------------------------------------------------
new_test_password() {
    # 4 familles de caractères, aucune sous-chaîne interdite par la politique du
    # script (mot de passe courant, nom d'utilisateur, libellé du domaine), aucun
    # caractère problématique en JSON ni en SQL. Jamais affiché, jamais archivé.
    PASSWORD="Aa1-$(head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n')"
}

run_setup() { # <fichier de log> <libellé>
    local logfile="$1" label="$2"
    local cmd="bash deploy/setup.sh --domain ${DOMAIN} --no-https --yes --admin-user ${ADMIN_USER}"
    local raw="${logfile}.raw" rc=0
    local -a statuses=()

    log_info "${label}"
    log_detail "docker exec -i -w /project ${CONTAINER} script -q -e -c \"${cmd}\" /dev/null"

    set +e
    {
        sleep "$PASSWORD_DELAY"
        printf '%s\n' "$PASSWORD"
        sleep 2
        printf '%s\n' "$PASSWORD"
    } | timeout "$RUN_TIMEOUT" docker exec -i -w /project "$CONTAINER" \
            script -q -e -c "$cmd" /dev/null > "$raw" 2>&1
    statuses=("${PIPESTATUS[@]}")
    set -e
    rc="${statuses[1]:-1}"

    scrub_log "$raw" "$logfile"
    log_info "Code de sortie de setup.sh : ${rc}"
    show_log_extract "$logfile" 45
    return "$rc"
}

run_container_cmd() { # <fichier de log> <commande dans le conteneur>
    local logfile="$1" cmd="$2"
    local raw="${logfile}.raw" rc=0
    set +e
    docker exec -w /project "$CONTAINER" bash -c "$cmd" > "$raw" 2>&1
    rc=$?
    set -e
    scrub_log "$raw" "$logfile"
    show_log_extract "$logfile" 20
    return "$rc"
}

# ---------------------------------------------------------------------------
#  6. Contrôles : installation du code et du service
# ---------------------------------------------------------------------------
check_installation_layout() {
    log_step "Contrôles — installation du code et du service"
    check_remote "code applicatif installé dans /opt/multi-agent-orchestrator/application" 'main.py' \
        bash -c "test -f ${APP_DIR}/backend/app/main.py && echo main.py"
    check_remote "environnement virtuel Python créé (uvicorn)" 'present' \
        bash -c "test -x ${APP_DIR}/backend/.venv/bin/uvicorn && echo present"
    check_remote "dépendances backend installées dans le venv" 'present' \
        bash -c "${APP_DIR}/backend/.venv/bin/python -c 'import fastapi, uvicorn, sqlalchemy; print(\"present\")'"
    check_remote "utilisateur système dédié non privilégié créé" '^orchestrator$' id -un orchestrator
    check_remote "groupe système dédié créé (Group=orchestrator)" 'orchestrator' getent group orchestrator
    check_remote "arborescence applicative possédée par l'utilisateur de service" '^orchestrator$' \
        stat -c '%U' "$APP_DIR"
    check_remote "répertoire de données persistant créé" 'present' \
        bash -c "test -d /var/lib/multi-agent-orchestrator/data && echo present"
}

check_service_and_http() {
    log_step "Contrôles — service systemd et santé HTTP"
    check_remote "service systemd « orchestrator » actif" '^active$' systemctl is-active orchestrator
    check_remote "service activé au démarrage du serveur" '^enabled$' systemctl is-enabled orchestrator
    check_remote "unité systemd : utilisateur de service non privilégié" '^User=orchestrator$' \
        grep -E '^User=' /etc/systemd/system/orchestrator.service
    check_remote "unité systemd : ExecStart vers l'environnement virtuel" \
        '^ExecStart=/opt/multi-agent-orchestrator/application/backend/.venv/bin/uvicorn app\.main:app --host 127\.0\.0\.1 --port 8000$' \
        grep -E '^ExecStart=' /etc/systemd/system/orchestrator.service
    check_remote "unité systemd : durcissement (ProtectSystem=strict, NoNewPrivileges, PrivateTmp)" '^3$' \
        bash -c "grep -cE '^(ProtectSystem=strict|NoNewPrivileges=true|PrivateTmp=true)$' /etc/systemd/system/orchestrator.service"
    check_remote "backend en écoute sur 127.0.0.1:8000 (connexion TCP réelle)" 'open' \
        bash -c 'exec 3<>/dev/tcp/127.0.0.1/8000 && echo open'
    check_remote "GET /health = 200 (backend direct)" '^200$' \
        curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/health
    check_remote "GET /health renvoie le JSON applicatif" '"status":"ok"' \
        curl -fsS --max-time 5 http://127.0.0.1:8000/health
    check_remote "GET /health via Nginx = 200 (reverse proxy)" '^200$' \
        curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H "Host: ${DOMAIN}" http://127.0.0.1/health
}

check_frontend() {
    log_step "Contrôles — frontend React compilé et servi par Nginx"
    check_remote "frontend compilé par setup.sh (dist/index.html présent)" 'present' \
        bash -c "test -f ${APP_DIR}/frontend/dist/index.html && echo present"
    check_remote "dépendances frontend installées (frontend/node_modules)" 'present' \
        bash -c "test -d ${APP_DIR}/frontend/node_modules && echo present"
    check_remote "page d'accueil servie par Nginx = 200" '^200$' \
        curl -s -o /dev/null -w '%{http_code}' --max-time 8 -H "Host: ${DOMAIN}" http://127.0.0.1/
    check_remote "HTML servi = index.html du frontend compilé" 'id="root"' \
        curl -fsS --max-time 8 -H "Host: ${DOMAIN}" http://127.0.0.1/
    check_remote "en-tête de sécurité X-Content-Type-Options présent" 'nosniff' \
        bash -c "curl -s -D - -o /dev/null -H 'Host: ${DOMAIN}' http://127.0.0.1/ | grep -i 'x-content-type-options'"
    check_remote "repli SPA : route inconnue sert le frontend" 'id="root"' \
        curl -fsS --max-time 8 -H "Host: ${DOMAIN}" http://127.0.0.1/tableau-de-bord
    check_remote "les routes /api restent du JSON (404 backend, pas de repli HTML)" '^404 application/json$' \
        curl -s -o /dev/null -w '%{http_code} %{content_type}' --max-time 8 -H "Host: ${DOMAIN}" \
        http://127.0.0.1/api/v1/inexistant
    check_remote "nginx -t : configuration Nginx valide" 'syntax is ok' nginx -t
    check_remote "site Nginx dédié activé (sites-enabled)" "orchestrator-${DOMAIN}" \
        bash -c 'ls /etc/nginx/sites-enabled/'
    check_remote "configuration Nginx générée sans placeholder résiduel" '^0$' \
        bash -c "grep -c '__[A-Z_]*__' /etc/nginx/sites-available/orchestrator-${DOMAIN} || true"

    # Le bundle JS référencé par la page doit être réellement servi par Nginx.
    local asset="" code=""
    asset="$(docker exec "$CONTAINER" bash -c \
        "grep -oE '/assets/[A-Za-z0-9._-]+\\.js' ${APP_DIR}/frontend/dist/index.html | head -n 1" 2>/dev/null || true)"
    if [[ -n "$asset" ]]; then
        code="$(docker exec "$CONTAINER" curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
            -H "Host: ${DOMAIN}" "http://127.0.0.1${asset}" 2>/dev/null || true)"
        check_eq "bundle JS « ${asset} » servi par Nginx (HTTP 200)" "$code" "200"
    else
        ko_check "bundle JS référencé par index.html et servi par Nginx" "aucune référence /assets/*.js"
    fi
}

check_database_and_admin() {
    log_step "Contrôles — base SQLite, schéma et compte administrateur"
    check_remote "base SQLite créée par les migrations" 'admins' sqlite3 "$DB_PATH" '.tables'
    check_remote "schéma complet (agents, tasks, tokens, events, orchestrator_state)" 'orchestrator_state' \
        sqlite3 "$DB_PATH" '.tables'
    check_remote "table « admins » : exactement 1 compte administrateur" '^1$' \
        sqlite3 "$DB_PATH" 'select count(*) from admins;'
    check_remote "mot de passe administrateur non stocké en clair dans la base" '^0$' \
        bash -c "grep -c -- '${PASSWORD}' ${DB_PATH} || true"
    check_remote "base possédée par l'utilisateur de service" '^orchestrator$' stat -c '%U' "$DB_PATH"
    check_remote "aucune base parasite créée dans l'application (chemin par défaut du backend)" 'absent' \
        bash -c "test -e ${APP_DIR}/backend/data/orchestrator.db && echo present || echo absent"

    # Authentification réelle : le compte créé par setup.sh doit fonctionner, et
    # un mauvais mot de passe doit être refusé.
    local code="" bad_code=""
    code="$(printf '%s' "$PASSWORD" | docker exec -i "$CONTAINER" bash -c '
        read -r pw
        curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST \
          -H "Content-Type: application/json" \
          -d "{\"username\":\"$1\",\"password\":\"$pw\"}" \
          http://127.0.0.1:8000/api/v1/auth/login' _ "$ADMIN_USER" 2>/dev/null || true)"
    check_eq "connexion administrateur réelle (POST /api/v1/auth/login = 200)" "$code" "200"

    bad_code="$(docker exec "$CONTAINER" curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST \
        -H 'Content-Type: application/json' \
        -d "{\"username\":\"${ADMIN_USER}\",\"password\":\"MotDePasseIncorrect-98765\"}" \
        http://127.0.0.1:8000/api/v1/auth/login 2>/dev/null || true)"
    check_eq "mot de passe erroné refusé (HTTP 401)" "$bad_code" "401"

    # Chemin réellement emprunté par le navigateur : Nginx -> backend (le
    # contrôle précédent court-circuite le reverse proxy).
    local nginx_code=""
    nginx_code="$(printf '%s' "$PASSWORD" | docker exec -i "$CONTAINER" bash -c '
        read -r pw
        curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST \
          -H "Content-Type: application/json" -H "Host: '"${DOMAIN}"'" \
          -d "{\"username\":\"$1\",\"password\":\"$pw\"}" \
          http://127.0.0.1/api/v1/auth/login' _ "$ADMIN_USER" 2>/dev/null || true)"
    check_eq "connexion administrateur via Nginx (chemin du navigateur) = 200" "$nginx_code" "200"
}

check_permissions_and_logs() {
    log_step "Contrôles — permissions, journaux, absence d'erreur"
    check_remote "production.env en 0600" '^600$' stat -c '%a' "$ENV_FILE"
    check_remote "production.env possédé par l'utilisateur de service" '^orchestrator$' stat -c '%U' "$ENV_FILE"
    check_remote "aucun mot de passe en clair dans production.env" '^0$' \
        bash -c "grep -c -- '${PASSWORD}' ${ENV_FILE} || true"
    check_remote "journaux applicatifs créés (application.log, audit.log)" 'application.log' \
        bash -c 'ls -1 /var/log/multi-agent-orchestrator/'
    check_remote "journal systemd : aucune exception ni erreur" '^0$' \
        bash -c 'journalctl -u orchestrator --no-pager -n 500 | grep -ciE "traceback|critical|error" || true'
    check_remote "journal systemd : démarrage du service visible" 'SERVICE_STARTED|Service démarré|Application startup complete' \
        bash -c 'journalctl -u orchestrator --no-pager -n 200 | tail -n 40'
    check_remote "journal applicatif : démarrage et état de santé tracés" 'Démarrage du service|État de santé initial' \
        bash -c "grep -E 'Démarrage du service|État de santé initial' /var/log/multi-agent-orchestrator/application.log | tail -n 2"
    check_remote "clé d'enregistrement des agents présente dans le fichier protégé" '^1$' \
        bash -c "grep -c '^ENROLLMENT_KEY=' ${ENV_FILE}"
}

check_setup_log() {
    log_step "Contrôles — journal brut de setup.sh (preuve d'exécution)"
    check_log "les 14 étapes ont été exécutées" "Étape 14/14" "$SETUP_LOG"
    check_log "installation terminée avec succès (code 0)" "Installation terminée avec succès" "$SETUP_LOG"
    check_log "Node.js 20+ installé par le script (dépôt NodeSource)" "Dépôt officiel NodeSource" "$SETUP_LOG"
    check_log "Node.js 20+ confirmé par le script" "Node.js v20" "$SETUP_LOG"
    check_log "paquet sqlite3 installé par l'étape 5" "sqlite3 :" "$SETUP_LOG"
    check_log "module venv opérationnel (python3-venv)" "Module venv opérationnel" "$SETUP_LOG"
    check_log "dépendances backend installées (uvicorn importable)" "uvicorn importable" "$SETUP_LOG"
    check_log "moteur esbuild de Vite vérifié exécutable" "opérationnel" "$SETUP_LOG"
    check_log "frontend compilé et vérifié par le script" "Frontend compilé" "$SETUP_LOG"
    check_log "migration / schéma de base traité" "migration" "$SETUP_LOG"
    check_log "compte administrateur créé et vérifié en base" "Compte administrateur créé et vérifié en base" "$SETUP_LOG"
    check_log "reverse proxy Nginx configuré pour le domaine" "Reverse proxy configuré" "$SETUP_LOG"
    check_log "service systemd démarré et vérifié" "Service actif" "$SETUP_LOG"
    check_log "production.env écrit en 0600" "Configuration écrite" "$SETUP_LOG"
    check_absent "aucune erreur bloquante dans le journal de setup.sh" "Échec pendant" "$SETUP_LOG"
    check_absent "aucun placeholder résiduel dans la configuration Nginx" "__DOMAIN__" "$SETUP_LOG"
    check_absent "le mot de passe administrateur n'apparaît nulle part dans le journal" "$PASSWORD" "$SETUP_LOG"
}

# ---------------------------------------------------------------------------
#  7. Phase 2 : npm 11 (allow-scripts) puis RÉEXÉCUTION de setup.sh
# ---------------------------------------------------------------------------
phase_npm11_idempotence() {
    log_step "Phase 2 — npm 11 (allow-scripts) puis RÉEXÉCUTION de setup.sh"

    local npm_version=""
    npm_version="$(docker exec "$CONTAINER" bash -c \
        'npm install -g npm@11 >/dev/null 2>&1; npm --version' 2>/dev/null || true)"
    if [[ -z "$npm_version" ]]; then
        ko_check "npm 11 installable pour le test (prérequis)" "« npm install -g npm@11 » a échoué"
        return 0
    fi
    log_info "npm mis à niveau pour le test : ${npm_version}"

    local policy=""
    policy="$(docker exec "$CONTAINER" bash -c "npm config ls -l | grep -c '^allow-scripts-pin' || true" 2>/dev/null || true)"
    check_eq "npm 11 applique la politique allow-scripts (prérequis du test)" "$policy" "1"

    local admins_before="" mtime_before=""
    admins_before="$(docker exec "$CONTAINER" sqlite3 "$DB_PATH" 'select count(*) from admins;' 2>/dev/null || true)"
    mtime_before="$(docker exec "$CONTAINER" stat -c '%Y' "${APP_DIR}/frontend/dist/index.html" 2>/dev/null || true)"

    local rc=0
    run_setup "$SETUP_LOG2" "Réexécution de setup.sh (idempotence + npm 11)…" || rc=$?
    check_exit "réexécution de setup.sh réussie (idempotence)" "$rc" "0"
    check_log "réexécution : compte existant non dupliqué (idempotence)" "idempotence" "$SETUP_LOG2"
    check_log "npm 11 : scripts d'installation autorisés pour esbuild" "autorisation limitée à" "$SETUP_LOG2"
    check_log "npm 11 : moteur esbuild opérationnel après réinstallation" "opérationnel" "$SETUP_LOG2"
    check_log "npm 11 : frontend recompilé avec succès" "Frontend compilé" "$SETUP_LOG2"
    check_absent "npm 11 : aucune erreur bloquante" "Échec pendant" "$SETUP_LOG2"

    # Preuve directe : le binaire esbuild installé sous npm 11 s'exécute.
    check_remote "npm 11 : binaire esbuild exécutable dans frontend/node_modules" '^[0-9]+\.[0-9]+' \
        bash -c "cd ${APP_DIR}/frontend && ./node_modules/.bin/esbuild --version"

    # Preuve directe : le frontend a réellement été REcompilé (dist régénéré).
    local mtime_after=""
    mtime_after="$(docker exec "$CONTAINER" stat -c '%Y' "${APP_DIR}/frontend/dist/index.html" 2>/dev/null || true)"
    if [[ -n "$mtime_before" && -n "$mtime_after" && "$mtime_after" != "$mtime_before" ]]; then
        ok_check "npm 11 : frontend/dist/index.html régénéré par la compilation" "${mtime_before} -> ${mtime_after}"
    else
        ko_check "npm 11 : frontend/dist/index.html régénéré par la compilation" \
            "horodatage inchangé (${mtime_before:-absent}/${mtime_after:-absent})"
    fi

    local admins_after=""
    admins_after="$(docker exec "$CONTAINER" sqlite3 "$DB_PATH" 'select count(*) from admins;' 2>/dev/null || true)"
    check_eq "un seul compte administrateur après réexécution" "$admins_after" "1"
    check_eq "compte administrateur identique avant/après réexécution" "${admins_before}/${admins_after}" "1/1"
    check_remote "service toujours actif après réexécution" '^active$' systemctl is-active orchestrator
    check_remote "santé HTTP toujours 200 après réexécution" '^200$' \
        curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/health
    check_remote "frontend toujours servi après réexécution" 'id="root"' \
        curl -fsS --max-time 8 -H "Host: ${DOMAIN}" http://127.0.0.1/
}

# ---------------------------------------------------------------------------
#  8. Phase 3 : mise à jour réelle (deploy/update.sh)
# ---------------------------------------------------------------------------
phase_update() {
    log_step "Phase 3 — mise à jour réelle : bash deploy/update.sh --yes"
    local rc=0
    run_container_cmd "$UPDATE_LOG" 'bash deploy/update.sh --yes' || rc=$?
    check_exit "deploy/update.sh terminé avec succès" "$rc" "0"
    check_log "mise à jour : sauvegarde créée avant modification" "Sauvegarde" "$UPDATE_LOG"
    check_log "mise à jour : santé backend vérifiée" "Santé backend" "$UPDATE_LOG"
    check_log "mise à jour : frontend recompilé" "Frontend compilé" "$UPDATE_LOG"
    check_absent "mise à jour : aucune erreur bloquante" "Échec pendant" "$UPDATE_LOG"
    check_remote "service actif après mise à jour" '^active$' systemctl is-active orchestrator
    check_remote "santé HTTP 200 après mise à jour" '^200$' \
        curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/health
    check_remote "compte administrateur conservé après mise à jour" '^1$' \
        sqlite3 "$DB_PATH" 'select count(*) from admins;'
    check_remote "frontend servi après mise à jour" 'id="root"' \
        curl -fsS --max-time 8 -H "Host: ${DOMAIN}" http://127.0.0.1/
    check_remote "sauvegarde de base conservée dans backups/" 'orchestrator.db|production.env|README' \
        bash -c "find ${BACKUP_DIR} -maxdepth 2 | head -n 5"
}

# ---------------------------------------------------------------------------
#  9. Nettoyage et bilan
# ---------------------------------------------------------------------------
cleanup() {
    if (( KEEP )); then
        log_info "Conteneur conservé (--keep) : docker exec -it ${CONTAINER} bash"
        log_info "Image conservée (--keep) : ${IMAGE_TEST}"
        return 0
    fi
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    log_info "Conteneur de test supprimé (image ${IMAGE_TEST} conservée pour les prochaines exécutions)."
}

summary() {
    log_step "BILAN DU BANC D'ESSAI"
    printf '  Contrôles réussis : %s\n' "$PASS_COUNT"
    printf '  Contrôles échoués : %s\n' "$FAIL_COUNT"
    printf '  Journaux bruts    : %s\n' "$OUT_DIR"
    if (( FAIL_COUNT == 0 )); then
        printf '\n  %sRÉSULTAT : OK — setup.sh rend l’orchestrateur pleinement opérationnel sur un Ubuntu %s vierge.%s\n' \
            "$C_GREEN" "$RELEASE" "$C_OFF"
        return 0
    fi
    printf '\n  %sRÉSULTAT : ÉCHEC%s — contrôles en échec :\n' "$C_RED" "$C_OFF"
    local name
    for name in "${FAILED_CHECKS[@]}"; do
        printf '    - %s\n' "$name"
    done
    return 1
}

main() {
    parse_args "$@"
    preflight
    new_test_password

    build_image
    start_container
    copy_project

    local rc=0
    run_setup "$SETUP_LOG" "Étape 1 — exécution de setup.sh sur un Ubuntu ${RELEASE} vierge…" || rc=$?
    check_exit "setup.sh terminé sans erreur (code de sortie)" "$rc" "0"

    check_installation_layout
    check_service_and_http
    check_frontend
    check_database_and_admin
    check_permissions_and_logs
    check_setup_log

    if (( RUN_NPM11 )); then
        phase_npm11_idempotence
    else
        log_step "Phase 2 — ignorée (--no-npm11)"
    fi

    if (( RUN_UPDATE )); then
        phase_update
    else
        log_step "Phase 3 — ignorée (--no-update)"
    fi

    cleanup
    summary
}

main "$@"
