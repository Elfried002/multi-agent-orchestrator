#!/usr/bin/env bash
# =============================================================================
#  Multi-Agent Orchestrator — deploy/update.sh
#
#  Mise à jour d'une installation existante, sans jamais supprimer les données
#  persistantes. Référence : docs/INSTALLATION.md §8 (les 11 étapes) et §10.
#
#  Le script :
#    1. vérifie l'installation existante ;
#    2. vérifie l'état du service ;
#    3. sauvegarde la base de données (snapshot SQLite cohérent) ;
#    4. sauvegarde la configuration (production.env, install.conf) ;
#    5. récupère la version souhaitée du code ;
#    6. installe les dépendances nécessaires ;
#    7. applique les migrations compatibles ;
#    8. recompile le frontend ;
#    9. redémarre le service ;
#   10. vérifie la santé du système ;
#   11. signale clairement le résultat — et documente le retour arrière.
#
#  Utilisation :
#    sudo bash deploy/update.sh
#    sudo bash deploy/update.sh --branch main --yes
#    sudo bash deploy/update.sh --list-backups
#    sudo bash deploy/update.sh --rollback            # dernière sauvegarde
#    sudo bash deploy/update.sh --rollback 20260924T120000Z
# =============================================================================
set -Eeuo pipefail

SERVICE_NAME="orchestrator"
SERVICE_USER="orchestrator"
SERVICE_GROUP="orchestrator"
PROJECT_SLUG="multi-agent-orchestrator"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8000"

# --- Frontend : Node.js / npm ------------------------------------------------
# Mêmes règles que deploy/setup.sh : Node.js 20+ requis par Vite, et depuis npm 11
# les scripts d'installation (postinstall) sont bloqués par défaut — le moteur
# « esbuild » de Vite doit donc être explicitement autorisé, sinon la compilation
# du frontend échoue silencieusement (docs/INSTALLATION.md §8, points 6 et 8).
MIN_NODE_MAJOR=20
NPM_INSTALL_SCRIPTS_PACKAGE="esbuild"
NPM_ALLOW_SCRIPTS_FILE=""

: "${MAO_ETC_ROOT:=/etc}"
: "${MAO_OPT_ROOT:=/opt}"
: "${MAO_VAR_ROOT:=/var}"
: "${MAO_SYSTEMCTL:=systemctl}"
: "${MAO_SKIP_USER_OPS:=0}"
: "${MAO_SKIP_SYSTEMD:=0}"

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

APP_BASE="${MAO_OPT_ROOT}/${PROJECT_SLUG}"
APP_DIR="${APP_BASE}/application"
BACKEND_DIR="${APP_DIR}/backend"
FRONTEND_DIR="${APP_DIR}/frontend"
FRONTEND_ROOT="${FRONTEND_DIR}/dist"
VENV_DIR="${BACKEND_DIR}/.venv"
VENV_BIN="${VENV_DIR}/bin"
VENV_PY="${VENV_BIN}/python"
CONFIG_DIR="${MAO_ETC_ROOT}/${PROJECT_SLUG}"
ENV_FILE="${CONFIG_DIR}/production.env"
INSTALL_CONF="${CONFIG_DIR}/install.conf"
DATA_DIR="${MAO_VAR_ROOT}/lib/${PROJECT_SLUG}"
DB_PATH="${DATA_DIR}/orchestrator.db"
BACKUP_DIR="${DATA_DIR}/backups"
SYSTEMD_UNIT_PATH="${MAO_ETC_ROOT}/systemd/system/${SERVICE_NAME}.service"

ASSUME_YES=0
SKIP_FRONTEND=0
SOURCE_URL=""
BRANCH=""
ROLLBACK_TARGET=""
LIST_BACKUPS=0
RESTORE_CONFIG=0
BACKUP_DIR_CREATED=""
CURRENT_STEP="initialisation"
UPDATE_OK=0

if [[ -t 1 ]]; then
    C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'
    C_BLUE=$'\033[0;36m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
    C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''; C_OFF=''
fi
log_info()  { printf '%s[INFO]%s %s\n' "$C_BLUE"   "$C_OFF" "$*"; }
log_ok()    { printf '%s[ OK ]%s %s\n' "$C_GREEN"  "$C_OFF" "$*"; }
log_warn()  { printf '%s[WARN]%s %s\n' "$C_YELLOW" "$C_OFF" "$*" >&2; }
log_error() { printf '%s[ERR ]%s %s\n' "$C_RED"    "$C_OFF" "$*" >&2; }
step_header() { printf '\n%s=== %s ===%s\n' "$C_BOLD" "$*" "$C_OFF"; }
die() { log_error "$*"; exit 1; }

redact() { sed -e 's/[A-Za-z0-9_-]\{20,\}/[valeur masquée]/g'; }

on_error() {
    local rc=$? line="${BASH_LINENO[0]:-?}"
    log_error "Échec pendant « ${CURRENT_STEP} » (ligne ${line}, code ${rc})."
    if [[ -n "$BACKUP_DIR_CREATED" ]]; then
        log_warn "Sauvegarde disponible pour retour arrière : ${BACKUP_DIR_CREATED}"
        log_warn "  sudo bash deploy/update.sh --rollback $(basename "$BACKUP_DIR_CREATED")"
    fi
    if (( ! UPDATE_OK )); then
        log_error "La mise à jour ne s'est pas terminée. Les données persistantes n'ont pas été supprimées."
        log_error "Restaurez la sauvegarde ci-dessus si le service ne redémarre pas."
    fi
    exit "$rc"
}
trap on_error ERR

usage() {
    cat <<'EOF'
Multi-Agent Orchestrator — mise à jour et retour arrière

Usage :
  sudo bash deploy/update.sh [options]

Options :
  --branch <nom>            Branche à déployer.
  --source-url <url>        Dépôt Git source (sinon : valeur de l'installation).
  --skip-frontend           Ne pas recompiler le frontend.
  --yes                     Accepter automatiquement les confirmations non destructrices.
  --list-backups            Lister les sauvegardes disponibles puis quitter.
  --rollback [<horodatage>] Revenir à une sauvegarde (la plus récente par défaut).
                            La base actuelle est conservée sous un nom horodaté.
  --with-config             Avec --rollback : restaurer aussi production.env.
  -h, --help                Afficher cette aide.

Les données persistantes (base SQLite, data/, journaux) ne sont jamais supprimées.
EOF
}

need_value() { (( $# >= 2 )) || die "L'option « $1 » attend une valeur."; }
have_cmd() { command -v "$1" >/dev/null 2>&1; }

sysd() {
    if (( MAO_SKIP_SYSTEMD )); then
        log_warn "[systemd ignoré] ${MAO_SYSTEMCTL} $*"
        return 0
    fi
    "$MAO_SYSTEMCTL" "$@"
}

service_active() {
    if (( MAO_SKIP_SYSTEMD )); then return 1; fi
    "$MAO_SYSTEMCTL" is-active --quiet "$SERVICE_NAME" >/dev/null 2>&1
}

confirm() {
    local question="$1" default="${2:-n}" answer
    if (( ASSUME_YES )); then
        log_info "Confirmation automatique (--yes) : ${question}"
        return 0
    fi
    if [[ ! -t 0 ]]; then
        log_warn "Entrée non interactive et --yes absent : « ${question} » refusé."
        return 1
    fi
    if [[ "$default" == "y" ]]; then
        read -r -p "${question} [O/n] " answer || return 1
        answer="${answer:-o}"
    else
        read -r -p "${question} [o/N] " answer || return 1
        answer="${answer:-n}"
    fi
    case "${answer,,}" in o|oui|y|yes) return 0 ;; *) return 1 ;; esac
}

run_as_service_user() {
    if [[ "$(id -un)" == "$SERVICE_USER" ]] || (( MAO_SKIP_USER_OPS )); then
        "$@"
        return $?
    fi
    local path_env="${VENV_BIN}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    if have_cmd runuser; then
        runuser -u "$SERVICE_USER" -- env "HOME=${DATA_DIR}" "PATH=${path_env}" "$@"
    else
        sudo -u "$SERVICE_USER" -H env "HOME=${DATA_DIR}" "PATH=${path_env}" "$@"
    fi
}

run_as_service_user_in_dir() { # $1 = répertoire de travail, reste : commande
    local dir="$1"; shift
    run_as_service_user bash -c 'cd -- "$1" || exit 1; shift; exec "$@"' bash "$dir" "$@"
}

node_major_version() {
    have_cmd node || return 1
    local version
    version="$(node --version 2>/dev/null)" || return 1
    version="${version#v}"
    printf '%s' "${version%%.*}"
}

# --- npm >= 11 : autorisation ciblée des scripts d'installation --------------
# Le fichier est créé hors du dépôt et hors du répertoire applicatif : aucun
# fichier du dépôt n'est modifié et l'exception est limitée à « esbuild ».
npm_restricts_install_scripts() {
    have_cmd npm || return 1
    npm config ls -l 2>/dev/null | grep -q '^allow-scripts-pin'
}

prepare_npm_install_scripts() {
    NPM_ALLOW_SCRIPTS_FILE=""
    npm_restricts_install_scripts || return 0
    install -d -m 0750 "$CONFIG_DIR"
    local f="${CONFIG_DIR}/npm-frontend.npmrc"
    {
        printf "# Multi-Agent Orchestrator — npm >= 11 : scripts d'installation.\n"
        printf "# Autorisation limitée à %s (moteur de Vite), sans quoi la compilation\n" "$NPM_INSTALL_SCRIPTS_PACKAGE"
        printf '# du frontend échoue (docs/INSTALLATION.md §8, points 6 et 8).\n'
        printf 'allow-scripts=%s\n' "$NPM_INSTALL_SCRIPTS_PACKAGE"
    } > "$f"
    chmod 0640 "$f"
    if [[ "$(id -un)" != "$SERVICE_USER" ]] && (( ! MAO_SKIP_USER_OPS )); then
        chown "root:${SERVICE_GROUP}" "$f" 2>/dev/null || true
    fi
    NPM_ALLOW_SCRIPTS_FILE="$f"
    log_warn "npm refuse les scripts d'installation par défaut : autorisation limitée à « ${NPM_INSTALL_SCRIPTS_PACKAGE} » via ${f}."
}

npm_frontend() { # $1 = sous-commande npm, reste : arguments npm
    local sub="$1"; shift
    local args=(--no-audit --no-fund)
    if [[ -n "$NPM_ALLOW_SCRIPTS_FILE" ]]; then
        args+=(--userconfig "$NPM_ALLOW_SCRIPTS_FILE")
    fi
    run_as_service_user_in_dir "$FRONTEND_DIR" "$(command -v npm)" "${args[@]}" "$sub" "$@"
}

ensure_esbuild_usable() {
    local node_bin="" out="" rc=0
    node_bin="$(command -v node || true)"
    [[ -n "$node_bin" ]] || return 1
    out="$(run_as_service_user_in_dir "$FRONTEND_DIR" "$node_bin" --input-type=commonjs \
            -e "process.stdout.write(String(require('esbuild').version))" 2>&1)" || rc=$?
    if (( rc == 0 )) && [[ -n "$out" ]]; then
        log_ok "Moteur de compilation ${NPM_INSTALL_SCRIPTS_PACKAGE} opérationnel (v${out})."
        return 0
    fi
    log_warn "Moteur ${NPM_INSTALL_SCRIPTS_PACKAGE} inutilisable : $(printf '%s' "$out" | tail -n 1)"
    return 1
}

load_install_conf() {
    if [[ -f "$INSTALL_CONF" ]]; then
        set -a
        # shellcheck disable=SC1090
        . "$INSTALL_CONF" || die "Fichier de métadonnées illisible : ${INSTALL_CONF}"
        set +a
        SERVICE_USER="${MAO_SERVICE_USER:-$SERVICE_USER}"
        SERVICE_GROUP="$SERVICE_USER"
        BRANCH="${BRANCH:-${MAO_BRANCH:-main}}"
        if [[ -z "$SOURCE_URL" ]]; then
            case "${MAO_SOURCE:-}" in
                local:*) SOURCE_URL="" ;;
                "") SOURCE_URL="" ;;
                *) SOURCE_URL="$MAO_SOURCE" ;;
            esac
        fi
    fi
    BRANCH="${BRANCH:-main}"
}

# =============================================================================
#  Points 1 et 2 — Vérification de l'installation et de l'état du service
# =============================================================================
preflight() {
    step_header "Points 1-2/11 — Vérification de l'installation et du service"
    CURRENT_STEP="vérification de l'installation"

    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        die "Ce script doit être exécuté en root : sudo bash deploy/update.sh"
    fi

    local missing=()
    [[ -d "$APP_DIR" ]]               || missing+=("répertoire applicatif ${APP_DIR}")
    [[ -x "$VENV_PY" ]]               || missing+=("environnement virtuel ${VENV_PY}")
    [[ -f "$ENV_FILE" ]]              || missing+=("configuration ${ENV_FILE}")
    [[ -f "${BACKEND_DIR}/app/main.py" ]] || missing+=("backend ${BACKEND_DIR}/app/main.py")
    [[ -f "$SYSTEMD_UNIT_PATH" ]]     || missing+=("service systemd ${SYSTEMD_UNIT_PATH}")
    if (( ${#missing[@]} > 0 )); then
        log_error "Installation introuvable ou incomplète :"
        local m
        for m in "${missing[@]}"; do log_error "  - ${m}"; done
        die "Lancez d'abord l'installation : sudo bash deploy/setup.sh"
    fi
    log_ok "Installation détectée : ${APP_DIR}"
    log_ok "Configuration détectée : ${ENV_FILE}"
    log_ok "Service détecté : ${SYSTEMD_UNIT_PATH}"

    if (( ! MAO_SKIP_SYSTEMD )); then
        local state enabled
        state="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo inconnu)"
        enabled="$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || echo inconnu)"
        log_info "État du service : ${state} / ${enabled}"
        if [[ "$state" != "active" ]]; then
            log_warn "Le service n'est pas actif : la mise à jour continuera mais la santé sera vérifiée à la fin."
            confirm "Poursuivre la mise à jour malgré tout ?" n || die "Mise à jour annulée."
        fi
    fi

    if [[ -f "$DB_PATH" ]]; then
        log_ok "Base de données présente : ${DB_PATH}"
    else
        log_warn "Aucune base de données à ${DB_PATH} : elle sera créée au premier démarrage."
    fi
}

# =============================================================================
#  Points 3 et 4 — Sauvegarde de la base et de la configuration
# =============================================================================
backup_database() { # $1 = fichier de destination
    local dest="$1"
    [[ -f "$DB_PATH" ]] || { log_warn "Aucune base à sauvegarder (${DB_PATH} absent)."; return 0; }
    if have_cmd sqlite3; then
        sqlite3 "$DB_PATH" ".backup '${dest}'" || die "Échec de la sauvegarde SQLite (sqlite3)."
    else
        python_venv - "$DB_PATH" "$dest" <<'PY' || die "Échec de la sauvegarde SQLite (API backup Python)."
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
source = sqlite3.connect(src)
target = sqlite3.connect(dst)
with target:
    source.backup(target)
target.close()
source.close()
PY
    fi
    log_ok "Base sauvegardée : ${dest} ($(du -h "$dest" 2>/dev/null | cut -f1))"
}

python_venv() {
    if [[ -x "$VENV_PY" ]]; then
        "$VENV_PY" "$@"
    elif have_cmd python3; then
        python3 "$@"
    else
        die "Python introuvable : impossible d'exécuter l'opération."
    fi
}

create_backup_set() {
    step_header "Points 3-4/11 — Sauvegarde de la base et de la configuration"
    CURRENT_STEP="sauvegarde"

    local stamp
    stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
    BACKUP_DIR_CREATED="${BACKUP_DIR}/${stamp}"
    install -d -m 0700 "$BACKUP_DIR_CREATED"
    log_info "Sauvegarde : ${BACKUP_DIR_CREATED}"

    backup_database "${BACKUP_DIR_CREATED}/orchestrator.db"
    if [[ -f "${BACKUP_DIR_CREATED}/orchestrator.db" ]]; then
        if have_cmd sha256sum; then
            sha256sum "${BACKUP_DIR_CREATED}/orchestrator.db" > "${BACKUP_DIR_CREATED}/orchestrator.db.sha256"
            log_ok "Empreinte SHA-256 de la sauvegarde enregistrée."
        fi
    fi

    if [[ -f "$ENV_FILE" ]]; then
        install -m 600 "$ENV_FILE" "${BACKUP_DIR_CREATED}/production.env" \
            || die "Impossible de sauvegarder la configuration."
        log_ok "Configuration sauvegardée (permissions 0600)."
    fi
    if [[ -f "$INSTALL_CONF" ]]; then
        install -m 600 "$INSTALL_CONF" "${BACKUP_DIR_CREATED}/install.conf" || true
    fi

    if [[ -d "${APP_DIR}/.git" ]]; then
        git -C "$APP_DIR" rev-parse HEAD > "${BACKUP_DIR_CREATED}/code-revision.txt" 2>/dev/null \
            && log_ok "Révision de code enregistrée pour le retour arrière : $(cat "${BACKUP_DIR_CREATED}/code-revision.txt")"
        git -C "$APP_DIR" rev-parse --abbrev-ref HEAD >> "${BACKUP_DIR_CREATED}/code-revision.txt" 2>/dev/null || true
    fi

    printf '# Sauvegarde créée par deploy/update.sh le %s (UTC).\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        > "${BACKUP_DIR_CREATED}/README.txt"
    printf 'Restauration : sudo bash deploy/update.sh --rollback %s\n' "$stamp" \
        >> "${BACKUP_DIR_CREATED}/README.txt"

    local count
    count="$(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d | wc -l)"
    log_ok "Sauvegardes conservées : ${count} (aucune n'est supprimée automatiquement)."
}

# =============================================================================
#  Point 5 — Récupération du code
# =============================================================================
copy_source_tree() {
    local src="$REPO_ROOT" tmp_src="" excludes=()
    excludes+=(--exclude '.git/' --exclude '.venv/' --exclude 'node_modules/'
               --exclude '__pycache__/' --exclude '*.pyc' --exclude '.env'
               --exclude '.github/' --exclude '*.log')
    if (( ! SKIP_FRONTEND )); then
        excludes+=(--exclude 'frontend/dist/')
    fi

    if [[ -n "$SOURCE_URL" ]]; then
        if [[ -d "${APP_DIR}/.git" ]]; then
            log_info "Récupération de la branche ${BRANCH} depuis ${SOURCE_URL}"
            git -C "$APP_DIR" fetch --prune origin || die "git fetch a échoué."
            git -C "$APP_DIR" checkout "$BRANCH" || die "Branche ${BRANCH} introuvable."
            git -C "$APP_DIR" reset --hard "origin/${BRANCH}" || die "git reset a échoué."
            return 0
        fi
        tmp_src="$(mktemp -d "${TMPDIR:-/tmp}/mao-update.XXXXXX")"
        case "$tmp_src" in
            "${TMPDIR:-/tmp}"/mao-update.*|/tmp/mao-update.*) : ;;
            *) die "Répertoire temporaire inattendu : ${tmp_src}" ;;
        esac
        git clone --depth 1 --branch "$BRANCH" "$SOURCE_URL" "${tmp_src}/repo" \
            || die "git clone a échoué."
        src="${tmp_src}/repo"
    else
        log_info "Source locale : ${REPO_ROOT}"
        [[ -d "${src}/backend" && -d "${src}/frontend" ]] \
            || die "Source de code invalide : ${src} (lancez update.sh depuis le dépôt)."
        if [[ "$(cd "$src" && pwd -P)" == "$(cd "$APP_DIR" && pwd -P)" ]]; then
            die "La source et la destination sont identiques : lancez update.sh depuis le dépôt, pas depuis ${APP_DIR}."
        fi
    fi

    have_cmd rsync || die "rsync est requis pour la mise à jour (installez-le : apt-get install rsync)."
    if ! rsync -a "${excludes[@]}" "${src}/" "${APP_DIR}/"; then
        [[ -n "$tmp_src" ]] && rm -rf -- "$tmp_src"
        die "Échec de la copie du code."
    fi
    [[ -n "$tmp_src" ]] && rm -rf -- "$tmp_src"
    log_ok "Code mis à jour dans ${APP_DIR} (données et venv conservés)."
}

# =============================================================================
#  Point 6 — Dépendances
# =============================================================================
install_dependencies() {
    local req="${BACKEND_DIR}/requirements.txt"
    if [[ -s "$req" ]]; then
        log_info "Installation des dépendances backend…"
        run_as_service_user "$VENV_PY" -m pip install --no-input --quiet -r "$req" \
            || die "Échec de l'installation des dépendances backend."
        log_ok "Dépendances backend à jour."
    else
        log_warn "backend/requirements.txt absent ou vide : dépendances backend inchangées."
    fi

    if (( SKIP_FRONTEND )); then
        log_warn "--skip-frontend : dépendances et compilation frontend ignorées."
        return 0
    fi
    local pkgjson="${FRONTEND_DIR}/package.json"
    if [[ -s "$pkgjson" ]] && have_cmd npm; then
        local major=""
        major="$(node_major_version || true)"
        if [[ -z "$major" ]] || (( major < MIN_NODE_MAJOR )); then
            die "Node.js v${major:-inconnu} détecté : Node.js ${MIN_NODE_MAJOR}+ est requis par Vite. Installez-le (dépôt NodeSource) ou relancez setup.sh."
        fi
        prepare_npm_install_scripts
        log_info "Installation des dépendances frontend…"
        local out="" rc=0 installed=0
        if [[ -f "${FRONTEND_DIR}/package-lock.json" ]]; then
            out="$(npm_frontend ci 2>&1)" || rc=$?
            if (( rc == 0 )); then
                installed=1
            else
                log_warn "« npm ci » a échoué (code ${rc}) : nouvelle tentative avec « npm install »."
                printf '%s\n' "$out" | tail -n 10 >&2
            fi
        fi
        if (( ! installed )); then
            rc=0
            out="$(npm_frontend install 2>&1)" || rc=$?
            if (( rc != 0 )); then
                printf '%s\n' "$out" | tail -n 40 >&2
                die "Échec de l'installation des dépendances frontend (code ${rc}, voir la sortie npm ci-dessus)."
            fi
        fi
        if ! ensure_esbuild_usable; then
            log_info "Reconstruction explicite du paquet ${NPM_INSTALL_SCRIPTS_PACKAGE}…"
            rc=0
            out="$(npm_frontend rebuild "$NPM_INSTALL_SCRIPTS_PACKAGE" 2>&1)" || rc=$?
            printf '%s\n' "$out" | tail -n 10 >&2
            ensure_esbuild_usable \
                || die "Le moteur ${NPM_INSTALL_SCRIPTS_PACKAGE} reste inutilisable : vérifiez l'accès réseau à registry.npmjs.org puis relancez update.sh."
        fi
        log_ok "Dépendances frontend à jour."
    else
        log_warn "Frontend absent ou npm indisponible : étape ignorée."
    fi
}

# =============================================================================
#  Point 7 — Migrations
# =============================================================================
apply_migrations() {
    local migrator="${BACKEND_DIR}/scripts/migrate.py"
    if [[ -f "$migrator" ]]; then
        log_info "Migrations : scripts/migrate.py"
        # DATABASE_PATH est transmis explicitement : sans lui, le backend
        # retomberait sur son chemin par défaut (backend/data/) au lieu de la
        # base de production (docs/INSTALLATION.md §6).
        run_as_service_user env "DATABASE_PATH=${DB_PATH}" "LOG_DIR=${LOG_DIR}" \
            "$VENV_PY" "$migrator" || die "Échec des migrations."
        log_ok "Migrations appliquées (scripts/migrate.py)."
        return 0
    fi
    if [[ -f "${BACKEND_DIR}/alembic.ini" ]]; then
        log_info "Migrations : alembic upgrade head"
        run_as_service_user env "DATABASE_PATH=${DB_PATH}" "${VENV_BIN}/alembic" upgrade head \
            || die "Échec des migrations Alembic."
        log_ok "Migrations appliquées (Alembic)."
        return 0
    fi
    log_info "Aucun moteur de migration détecté : le backend applique son schéma au démarrage (compatible, aucune donnée supprimée)."
}

# =============================================================================
#  Point 8 — Compilation du frontend
# =============================================================================
build_frontend() {
    if (( SKIP_FRONTEND )); then
        log_warn "--skip-frontend : compilation ignorée."
        return 0
    fi
    local pkgjson="${FRONTEND_DIR}/package.json"
    if [[ ! -s "$pkgjson" ]]; then
        log_warn "frontend/package.json absent ou vide : compilation ignorée."
        return 0
    fi
    have_cmd npm || die "npm est introuvable : impossible de compiler le frontend (utilisez --skip-frontend)."
    local major=""
    major="$(node_major_version || true)"
    if [[ -z "$major" ]] || (( major < MIN_NODE_MAJOR )); then
        die "Node.js v${major:-inconnu} détecté : Node.js ${MIN_NODE_MAJOR}+ est requis par Vite (voir deploy/setup.sh, étape 5)."
    fi
    prepare_npm_install_scripts
    if grep -q '"build"[[:space:]]*:' "$pkgjson"; then
        log_info "Compilation du frontend (npm run build)…"
        local rc=0 out=""
        out="$(npm_frontend run build 2>&1)" || rc=$?
        if (( rc != 0 )); then
            printf '%s\n' "$out" | tail -n 40 >&2
            die "La compilation du frontend a échoué (code ${rc}, voir la sortie ci-dessus)."
        fi
    else
        log_warn "Aucun script « build » dans package.json."
    fi
    [[ -f "${FRONTEND_ROOT}/index.html" ]] || die "Compilation incomplète : ${FRONTEND_ROOT}/index.html absent."
    log_ok "Frontend compilé : ${FRONTEND_ROOT}"
}

# =============================================================================
#  Points 9 et 10 — Redémarrage et vérification de santé
# =============================================================================
restart_service() {
    if (( MAO_SKIP_SYSTEMD )); then
        log_warn "MAO_SKIP_SYSTEMD=1 : redémarrage ignoré (mode test)."
        return 0
    fi
    sysd daemon-reload || log_warn "daemon-reload a échoué (non bloquant)."
    log_info "Redémarrage du service…"
    sysd restart "$SERVICE_NAME" || {
        log_error "Le service n'a pas redémarré. Journal :"
        journalctl -u "$SERVICE_NAME" -n 50 --no-pager 2>&1 | redact >&2 || true
        die "Mise à jour interrompue : restaurez la sauvegarde (--rollback) si nécessaire."
    }
    log_ok "Service redémarré."
}

wait_for_health() { # $1 = délai max en secondes
    local timeout="$1" i=0
    have_cmd curl || { log_warn "curl absent : vérification de santé ignorée."; return 0; }
    while (( i < timeout )); do
        if curl -fsS --max-time 3 "http://${BACKEND_HOST}:${BACKEND_PORT}/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    return 1
}

verify_health() {
    step_header "Points 9-10/11 — Redémarrage et vérification de santé"
    CURRENT_STEP="vérification de santé"
    restart_service

    if wait_for_health 30; then
        log_ok "Santé backend : http://${BACKEND_HOST}:${BACKEND_PORT}/health répond correctement."
    else
        log_error "Le backend ne répond pas sur http://${BACKEND_HOST}:${BACKEND_PORT}/health après 30 s."
        if (( ! MAO_SKIP_SYSTEMD )); then
            journalctl -u "$SERVICE_NAME" -n 40 --no-pager 2>&1 | redact >&2 || true
        fi
        die "Mise à jour en échec : retour arrière disponible avec « sudo bash deploy/update.sh --rollback $(basename "${BACKUP_DIR_CREATED:-}") »."
    fi

    if [[ -f "$DB_PATH" ]]; then
        local report
        report="$(python_venv - "$DB_PATH" <<'PY' || true
import sqlite3, sys
try:
    con = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
    n = 0
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        low = name.lower()
        if ("admin" in low or "user" in low):
            cols = [c[1] for c in con.execute('PRAGMA table_info("%s")' % name).fetchall()]
            if "username" in cols:
                n = max(n, con.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0])
    print(n)
except Exception:
    print(0)
PY
)"
        if [[ "${report:-0}" -ge 1 ]]; then
            log_ok "Base de données intacte : ${report} compte(s) administrateur."
        else
            log_warn "Aucun compte administrateur détecté : vérifiez la base (les données n'ont pas été supprimées)."
        fi
    fi
}

# =============================================================================
#  Point 11 — Rapport final
# =============================================================================
final_report() {
    step_header "Point 11/11 — Résultat de la mise à jour"
    UPDATE_OK=1
    local scheme="http"
    [[ -f "${MAO_ETC_ROOT}/letsencrypt/live/${MAO_DOMAIN:-}/fullchain.pem" ]] && scheme="https"
    local state="inconnu"
    (( ! MAO_SKIP_SYSTEMD )) && state="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo inactif)"

    cat <<EOF

${C_BOLD}Mise à jour terminée${C_OFF}
  Service              : ${state}
  Application          : ${APP_DIR}
  Sauvegarde du jour   : ${BACKUP_DIR_CREATED}
  Base de données      : ${DB_PATH} (conservée)
  Configuration        : ${ENV_FILE} (conservée)
  Site                 : ${scheme}://${MAO_DOMAIN:-<domaine>}/

Vérifications :
  sudo systemctl status ${SERVICE_NAME}
  sudo journalctl -u ${SERVICE_NAME} -n 50
  curl -s ${scheme}://${MAO_DOMAIN:-localhost}/health

Retour arrière :
  sudo bash deploy/update.sh --list-backups
  sudo bash deploy/update.sh --rollback $(basename "$BACKUP_DIR_CREATED")
     -> la base actuelle est d'abord conservée sous orchestrator.db.pre-rollback.<horodatage>
  Sauvegardes conservées dans : ${BACKUP_DIR}
EOF
}

# =============================================================================
#  Retour arrière
# =============================================================================
list_backups() {
    if [[ ! -d "$BACKUP_DIR" ]]; then
        log_warn "Aucune sauvegarde : ${BACKUP_DIR} est absent."
        return 0
    fi
    log_info "Sauvegardes disponibles dans ${BACKUP_DIR} :"
    local d
    for d in "$BACKUP_DIR"/*; do
        [[ -d "$d" ]] || continue
        printf '  %s' "$(basename "$d")"
        [[ -f "$d/orchestrator.db" ]] && printf '  [base %s]' "$(du -h "$d/orchestrator.db" | cut -f1)"
        [[ -f "$d/production.env" ]] && printf '  [config]'
        printf '\n'
    done
}

do_rollback() {
    step_header "Retour arrière"
    CURRENT_STEP="retour arrière"
    local target="$ROLLBACK_TARGET" dir stamp

    [[ -d "$BACKUP_DIR" ]] || die "Aucune sauvegarde disponible (${BACKUP_DIR} absent)."
    if [[ -z "$target" || "$target" == "latest" ]]; then
        stamp="$(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d -printf '%f\n' 2>/dev/null | sort | tail -n1)"
        [[ -n "$stamp" ]] || die "Aucune sauvegarde à restaurer."
        target="$stamp"
    fi
    dir="${BACKUP_DIR}/${target}"
    [[ -d "$dir" ]] || die "Sauvegarde introuvable : ${dir}"
    log_info "Sauvegarde sélectionnée : ${dir}"

    if [[ ! -f "${dir}/orchestrator.db" ]]; then
        log_warn "Cette sauvegarde ne contient pas de base de données : seule la configuration pourra être restaurée."
    fi
    if ! confirm "Restaurer cette sauvegarde ? Le service sera arrêté puis redémarré." n; then
        die "Retour arrière annulé."
    fi

    if service_active; then
        sysd stop "$SERVICE_NAME" || die "Impossible d'arrêter le service."
        log_ok "Service arrêté."
    fi

    if [[ -n "$BACKUP_DIR_CREATED" ]]; then
        log_info "Sauvegarde préalable effectuée : ${BACKUP_DIR_CREATED}"
    fi

    if [[ -f "$DB_PATH" ]]; then
        local safety
        safety="${DB_PATH}.pre-rollback.$(date -u '+%Y%m%dT%H%M%SZ')"
        cp -a -- "$DB_PATH" "$safety" || die "Impossible de conserver la base actuelle (opération annulée)."
        log_ok "Base actuelle conservée : ${safety}"
    fi

    if [[ -f "${dir}/orchestrator.db" ]]; then
        if [[ -f "${dir}/orchestrator.db.sha256" ]] && have_cmd sha256sum; then
            log_info "Vérification de l'empreinte de la sauvegarde…"
            if (cd "$dir" && sha256sum -c orchestrator.db.sha256 >/dev/null 2>&1); then
                log_ok "Empreinte de la sauvegarde valide."
            else
                log_warn "L'empreinte de la sauvegarde ne correspond pas : restauration poursuivie mais à vérifier."
            fi
        fi
        cp -a -- "${dir}/orchestrator.db" "$DB_PATH" || die "Restauration de la base impossible."
        chown_to_owner "$DB_PATH"
        log_ok "Base de données restaurée."
    fi

    if (( RESTORE_CONFIG )) && [[ -f "${dir}/production.env" ]]; then
        install -m 600 "${dir}/production.env" "$ENV_FILE"
        chown_to_owner "$ENV_FILE"
        log_ok "Configuration restaurée (production.env)."
    fi

    if [[ -f "${dir}/code-revision.txt" && -d "${APP_DIR}/.git" ]]; then
        local rev
        rev="$(head -n1 "${dir}/code-revision.txt")"
        if [[ -n "$rev" ]] && git -C "$APP_DIR" cat-file -e "$rev" 2>/dev/null; then
            git -C "$APP_DIR" checkout --detach "$rev" || log_warn "Retour au code ${rev} impossible."
            log_ok "Code restauré à la révision ${rev}."
        else
            log_warn "Révision ${rev:-inconnue} indisponible localement : code inchangé."
        fi
    fi

    UPDATE_OK=0
    restart_service
    if wait_for_health 30; then
        UPDATE_OK=1
        log_ok "Retour arrière terminé : le service répond correctement."
    else
        UPDATE_OK=0
        die "Le service ne répond pas après le retour arrière : consultez « journalctl -u ${SERVICE_NAME} -n 50 »."
    fi
    printf '\nRappel : la sauvegarde %s est conservee (aucune suppression effectuee).\n' "$target"
}

chown_to_owner() {
    local path="$1" owner="${SERVICE_USER}:${SERVICE_GROUP}"
    if (( MAO_SKIP_USER_OPS )); then return 0; fi
    chown "$owner" "$path" 2>/dev/null || log_warn "chown ${owner} ${path} a échoué (à vérifier)."
}

# =============================================================================
#  Programme principal
# =============================================================================
parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            --branch)       need_value "$@"; BRANCH="$2"; shift 2 ;;
            --branch=*)     BRANCH="${1#*=}"; shift ;;
            --source-url)   need_value "$@"; SOURCE_URL="$2"; shift 2 ;;
            --source-url=*) SOURCE_URL="${1#*=}"; shift ;;
            --skip-frontend) SKIP_FRONTEND=1; shift ;;
            --yes|-y)       ASSUME_YES=1; shift ;;
            --list-backups) LIST_BACKUPS=1; shift ;;
            --with-config)  RESTORE_CONFIG=1; shift ;;
            --rollback)     ROLLBACK_TARGET="latest"
                            if (( $# >= 2 )) && [[ "$2" != -* ]]; then ROLLBACK_TARGET="$2"; shift; fi
                            shift ;;
            --rollback=*)   ROLLBACK_TARGET="${1#*=}"; shift ;;
            -h|--help)      usage; exit 0 ;;
            *) die "Option inconnue : $1 (voir --help)" ;;
        esac
    done
}

main() {
    parse_args "$@"
    load_install_conf

    if (( LIST_BACKUPS )); then
        list_backups
        exit 0
    fi

    if [[ -n "$ROLLBACK_TARGET" ]]; then
        do_rollback
        exit 0
    fi

    printf '%sMulti-Agent Orchestrator — mise à jour%s\n' "$C_BOLD" "$C_OFF"
    preflight
    create_backup_set

    if ! confirm "Appliquer la mise à jour maintenant (arrêt bref du service) ?" y; then
        log_warn "Mise à jour annulée. Sauvegarde conservée : ${BACKUP_DIR_CREATED}"
        exit 0
    fi

    step_header "Points 5-8/11 — Code, dépendances, migrations, frontend"
    CURRENT_STEP="arrêt du service"
    if service_active; then
        sysd stop "$SERVICE_NAME" || die "Impossible d'arrêter le service pour appliquer la mise à jour."
        log_ok "Service arrêté proprement (les données persistantes sont intactes)."
    fi

    CURRENT_STEP="récupération du code"
    copy_source_tree
    CURRENT_STEP="dépendances"
    install_dependencies
    CURRENT_STEP="migrations"
    apply_migrations
    CURRENT_STEP="compilation du frontend"
    ( cd "$APP_DIR" && build_frontend )

    chown_to_owner "$APP_DIR"

    verify_health
    final_report
}

main "$@"
