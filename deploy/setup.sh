#!/usr/bin/env bash
# =============================================================================
#  Multi-Agent Orchestrator — deploy/setup.sh
#
#  Installation automatisée, idempotente et NON DESTRUCTIVE sur Ubuntu Server
#  24.04 (22.04 minimum). Documents de référence :
#    - docs/INSTALLATION.md  (§3 à §7, §10, §11, §13)
#    - docs/SECURITY.md      (§3.2, §8, §14, §15)
#    - docs/ARCHITECTURE.md  (§3.3, §12)
#
#  Garanties :
#    * aucune configuration préexistante n'est supprimée ni écrasée sans
#      validation explicite (Nginx, certificats, base, données) ;
#    * aucun secret n'apparaît dans un message, un journal ou une ligne de
#      commande (le mot de passe administrateur est lu en mode masqué et
#      transmis sur stdin) ;
#    * une étape en échec ne laisse pas de configuration partiellement activée ;
#    * la réexécution du script est sûre (idempotence).
#
#  Utilisation :
#    sudo bash deploy/setup.sh
#    sudo bash deploy/setup.sh --domain api.example.com --email admin@example.com
#    sudo bash deploy/setup.sh --help
# =============================================================================
set -Eeuo pipefail

# --- Identité / valeurs par défaut -------------------------------------------
SERVICE_NAME="orchestrator"
SERVICE_USER="orchestrator"
SERVICE_GROUP="orchestrator"
PROJECT_SLUG="multi-agent-orchestrator"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8000"
MIN_UBUNTU_VERSION="22.04"
MIN_FREE_MB="2048"
MAX_PASSWORD_ATTEMPTS=5
PASSWORD_MIN_LENGTH=12

# --- Frontend : Node.js / npm (docs/INSTALLATION.md §5 étape 5, §5 étape 6) ---
# Le frontend React/Vite exige Node.js 20 minimum : l'archive Ubuntu 24.04 ne
# fournit que Node.js 18 (et aucune version sur Ubuntu 22.04). Node.js 20+ est
# donc installé depuis le dépôt officiel NodeSource, qui apporte également npm.
MIN_NODE_MAJOR=20
NODE_MAJOR="${MAO_NODE_MAJOR:-20}"
NODESOURCE_SETUP_URL="https://deb.nodesource.com/setup_${NODE_MAJOR}.x"
#: Paquet dont le script d'installation (postinstall) est indispensable à la
#: compilation du frontend : moteur Go « esbuild » embarqué par Vite.
NPM_INSTALL_SCRIPTS_PACKAGE="esbuild"

# --- Racines des chemins (surchargeables pour les tests ; jamais en prod) ----
: "${MAO_ETC_ROOT:=/etc}"
: "${MAO_OPT_ROOT:=/opt}"
: "${MAO_VAR_ROOT:=/var}"
: "${MAO_SYSTEMCTL:=systemctl}"
: "${MAO_SKIP_USER_OPS:=0}"
: "${MAO_SKIP_SYSTEMD:=0}"

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

# --- Chemins de production (docs/INSTALLATION.md §6) -------------------------
APP_BASE="${MAO_OPT_ROOT}/${PROJECT_SLUG}"
APP_DIR="${APP_BASE}/application"
BACKEND_DIR="${APP_DIR}/backend"
FRONTEND_DIR="${APP_DIR}/frontend"
FRONTEND_ROOT="${FRONTEND_DIR}/dist"
VENV_DIR="${BACKEND_DIR}/.venv"
VENV_BIN="${VENV_DIR}/bin"
VENV_PY="${VENV_BIN}/python"
DOCS_DIR="${APP_DIR}/docs"

CONFIG_DIR="${MAO_ETC_ROOT}/${PROJECT_SLUG}"
ENV_FILE="${CONFIG_DIR}/production.env"
INSTALL_CONF="${CONFIG_DIR}/install.conf"

DATA_DIR="${MAO_VAR_ROOT}/lib/${PROJECT_SLUG}"
DB_PATH="${DATA_DIR}/orchestrator.db"
BACKUP_DIR="${DATA_DIR}/backups"
LOG_DIR="${MAO_VAR_ROOT}/log/${PROJECT_SLUG}"
LOG_FILE="${LOG_DIR}/application.log"
AUDIT_LOG_FILE="${LOG_DIR}/audit.log"
ACME_WEBROOT="${MAO_VAR_ROOT}/www/${PROJECT_SLUG}"

NGINX_AVAILABLE_DIR="${MAO_ETC_ROOT}/nginx/sites-available"
NGINX_ENABLED_DIR="${MAO_ETC_ROOT}/nginx/sites-enabled"
NGINX_SNIPPETS_DIR="${MAO_ETC_ROOT}/nginx/snippets"
NGINX_LOG_DIR="${MAO_VAR_ROOT}/log/nginx"
SYSTEMD_UNIT_PATH="${MAO_ETC_ROOT}/systemd/system/${SERVICE_NAME}.service"
LE_LIVE_DIR="${MAO_ETC_ROOT}/letsencrypt/live"

NGINX_TEMPLATE="${SCRIPT_DIR}/nginx/orchestrator.conf"
UNIT_TEMPLATE="${SCRIPT_DIR}/systemd/orchestrator.service"

# --- Options CLI -------------------------------------------------------------
DOMAIN="${MAO_DOMAIN:-}"
LETSENCRYPT_EMAIL=""
ADMIN_USER=""
SOURCE_URL=""
BRANCH="main"
ASSUME_YES=0
TLS_REQUESTED="yes"
SKIP_FRONTEND=0
RECONFIGURE=0
RENDER_ONLY=""
PRINT_PATHS=0

# --- État d'exécution --------------------------------------------------------
CURRENT_STEP="initialisation"
DOMAIN_LOWER=""
ADMIN_PASSWORD=""
SECRET_KEY_GENERATED=""
ENROLLMENT_KEY_GENERATED=""
TLS_MODE="no"
UNIT_CREATED_THIS_RUN=0
SERVICE_PREVIOUSLY_ENABLED=0
EXISTING_INSTALL=0
ROLLBACK_FILES=()
ROLLBACK_RESTORES=()
NGINX_ACTIVATED=0
PY_BIN=""
NODE_BIN=""
NPM_BIN=""
NPM_ALLOW_SCRIPTS_FILE=""

# =============================================================================
#  Journalisation
# =============================================================================
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

# =============================================================================
#  Gestion des erreurs : jamais de configuration partiellement activée
# =============================================================================
rollback_partial_state() {
    local f pair
    if (( ${#ROLLBACK_RESTORES[@]} > 0 )); then
        for pair in "${ROLLBACK_RESTORES[@]}"; do
            local backup="${pair%%|*}" original="${pair##*|}"
            if [[ -f "$backup" ]]; then
                if cp -a -- "$backup" "$original" 2>/dev/null; then
                    log_warn "Configuration préexistante restaurée : ${original}"
                else
                    log_warn "Restauration manuelle nécessaire : copiez ${backup} vers ${original}"
                fi
            fi
        done
    fi
    if (( ${#ROLLBACK_FILES[@]} > 0 )); then
        for f in "${ROLLBACK_FILES[@]}"; do
            local restored=0
            for pair in "${ROLLBACK_RESTORES[@]:-}"; do
                [[ "${pair##*|}" == "$f" ]] && restored=1
            done
            if (( restored )); then continue; fi
            if [[ -e "$f" || -L "$f" ]]; then
                log_warn "Nettoyage d'un fichier non activé : $f"
                rm -f -- "$f"
            fi
        done
        log_info "Les autres configurations Nginx du serveur n'ont pas été modifiées."
    fi
    if (( UNIT_CREATED_THIS_RUN )) && (( NGINX_ACTIVATED )); then
        log_warn "Unité systemd installée mais service non activé — elle est conservée pour diagnostic :"
        log_warn "  sudo systemctl status ${SERVICE_NAME} ; sudo journalctl -u ${SERVICE_NAME} -n 50"
    fi
}

on_error() {
    local rc=$? line="${BASH_LINENO[0]:-?}"
    log_error "Échec pendant « ${CURRENT_STEP} » (ligne ${line}, code ${rc})."
    rollback_partial_state
    log_error "Le script s'arrête sans laisser de configuration partiellement activée."
    log_error "Corrigez la cause puis relancez : bash deploy/setup.sh (le script est idempotent)."
    exit "$rc"
}
trap on_error ERR

register_rollback_file() { ROLLBACK_FILES+=("$1"); }
register_rollback_restore() { ROLLBACK_RESTORES+=("$1|$2"); }

# =============================================================================
#  Aide
# =============================================================================
usage() {
    cat <<'EOF'
Multi-Agent Orchestrator — installation automatisée (Ubuntu Server 22.04+)

Usage :
  sudo bash deploy/setup.sh [options]

Options :
  --domain <fqdn>        Domaine public (ex. api.example.com). Demandé si absent.
  --email <adresse>      Adresse e-mail Let's Encrypt. Demandée si absente.
  --admin-user <nom>     Nom du compte administrateur (défaut : admin).
  --source-url <url>     Dépôt Git à déployer. Par défaut : le dépôt courant.
  --branch <nom>         Branche à déployer (défaut : main).
  --no-https             Ne pas demander de certificat TLS (HTTP seul, non
                         recommandé — docs/SECURITY.md §8).
  --skip-frontend        Ne pas compiler le frontend (utilise un dist existant).
  --reconfigure          Régénérer production.env (sauvegarde horodatée créée).
  --yes                  Accepter automatiquement les confirmations non
                         destructrices (le mot de passe reste saisi à la main).
  --render-only <dir>    Rendre le gabarit Nginx et l'unité systemd dans <dir>
                         sans rien installer (utilisé par la CI et les tests).
  --print-paths          Afficher les chemins résolus puis quitter.
  -h, --help             Afficher cette aide.

Le mot de passe administrateur n'est JAMAIS accepté en option ni par variable
d'environnement : il est saisi en mode masqué et transmis sur stdin
(docs/SECURITY.md §15, docs/INSTALLATION.md §11).

Variables d'environnement (tests et environnements isolés uniquement) :
  MAO_ETC_ROOT, MAO_OPT_ROOT, MAO_VAR_ROOT   racines des chemins
  MAO_SYSTEMCTL                              commande systemctl (stub possible)
  MAO_SKIP_USER_OPS=1                        ne pas créer/gérer l'utilisateur
  MAO_SKIP_SYSTEMD=1                         ignorer les appels systemd
EOF
}

# =============================================================================
#  Utilitaires
# =============================================================================
need_value() { (( $# >= 2 )) || die "L'option « $1 » attend une valeur."; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

version_at_least() { # $1 version courante, $2 version minimale
    [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

sysd() { # enveloppe systemctl (stub possible, ignorable en test)
    if (( MAO_SKIP_SYSTEMD )); then
        log_warn "[systemd ignoré] ${MAO_SYSTEMCTL} $*"
        return 0
    fi
    "$MAO_SYSTEMCTL" "$@"
}

sysd_quiet_active() {
    if (( MAO_SKIP_SYSTEMD )); then return 1; fi
    "$MAO_SYSTEMCTL" is-active --quiet "$SERVICE_NAME" >/dev/null 2>&1
}

make_dir() { # $1 mode, $2 owner (user:group), reste : chemins
    local mode="$1" owner="$2"; shift 2
    if (( MAO_SKIP_USER_OPS )); then
        install -d -m "$mode" "$@"
    else
        install -d -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$@"
    fi
}

chown_to() { # $1 owner (user:group), reste : chemins
    local owner="$1"; shift
    if (( MAO_SKIP_USER_OPS )); then
        return 0
    fi
    chown "$owner" "$@"
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

node_major_version() { # version majeure de Node.js, ou échec si absente
    have_cmd node || return 1
    local version
    version="$(node --version 2>/dev/null)" || return 1
    version="${version#v}"
    printf '%s' "${version%%.*}"
}

# --- npm : politique « allow-scripts » (npm >= 11) ---------------------------
# Depuis npm 11, les scripts d'installation (postinstall) des dépendances ne sont
# plus exécutés par défaut : sans autorisation explicite, l'installation du moteur
# « esbuild » utilisé par Vite reste incomplète et « npm run build » échoue.
# L'autorisation est portée par un fichier .npmrc DÉDIÉ, situé hors du dépôt et
# hors du répertoire applicatif : aucun fichier du dépôt n'est modifié et
# l'exception est strictement limitée au paquet « esbuild ».
npm_restricts_install_scripts() {
    have_cmd npm || return 1
    npm config ls -l 2>/dev/null | grep -q '^allow-scripts-pin'
}

prepare_npm_install_scripts() {
    NPM_ALLOW_SCRIPTS_FILE=""
    npm_restricts_install_scripts || return 0
    make_dir 0750 root:"${SERVICE_GROUP}" "$CONFIG_DIR"
    local f="${CONFIG_DIR}/npm-frontend.npmrc"
    {
        printf "# Multi-Agent Orchestrator — npm >= 11 : scripts d'installation.\n"
        printf "# Autorisation limitée à %s (moteur de Vite), sans quoi la compilation\n" "$NPM_INSTALL_SCRIPTS_PACKAGE"
        printf '# du frontend échoue (docs/INSTALLATION.md §5, étape 6).\n'
        printf 'allow-scripts=%s\n' "$NPM_INSTALL_SCRIPTS_PACKAGE"
    } > "$f"
    chmod 0640 "$f"
    chown_to "root:${SERVICE_GROUP}" "$f"
    NPM_ALLOW_SCRIPTS_FILE="$f"
    log_warn "npm refuse les scripts d'installation par défaut : autorisation limitée à « ${NPM_INSTALL_SCRIPTS_PACKAGE} » via ${f}."
}

confirm() { # $1 question, $2 défaut (y|n)
    local question="$1" default="${2:-n}" answer
    if (( ASSUME_YES )); then
        log_info "Confirmation automatique (--yes) : ${question}"
        return 0
    fi
    if [[ ! -t 0 ]]; then
        log_warn "Entrée non interactive et --yes absent : « ${question} » refusé par défaut."
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

redact() { # masque les jetons longs dans un flux de sortie
    sed -e 's/[A-Za-z0-9_-]\{20,\}/[valeur masquée]/g'
}

env_value() { # $1 fichier, $2 clé -> valeur (sans jamais l'afficher ailleurs)
    local file="$1" key="$2"
    [[ -f "$file" ]] || return 1
    sed -n "s/^${key}=//p" "$file" | head -n1
}

env_has_key() {
    local file="$1" key="$2"
    [[ -f "$file" ]] || return 1
    grep -qE "^${key}=" "$file"
}

python_available() {
    if [[ -x "$VENV_PY" ]]; then PY_BIN="$VENV_PY"
    elif have_cmd python3; then PY_BIN="$(command -v python3)"
    else return 1; fi
    return 0
}

gen_secret() { # $1 = nombre d'octets d'entropie
    python_available || die "Python est requis pour générer les secrets."
    "$PY_BIN" -c 'import secrets,sys; print(secrets.token_urlsafe(int(sys.argv[1])))' "$1"
}

validate_domain() {
    local d="$1"
    (( ${#d} <= 253 )) || return 1
    [[ "$d" == *.* ]] || return 1
    [[ "$d" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]
}

validate_email() {
    [[ "$1" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$ ]]
}

validate_admin_user() {
    [[ "$1" =~ ^[a-z0-9]([a-z0-9._-]{1,30})[a-z0-9]$ ]]
}

# --- Politique de robustesse du mot de passe ---------------------------------
password_policy_error() { # affiche la raison de rejet, ou rien si conforme
    local pw="$1" classes=0 low
    printf '%s' "$pw" | grep -q '[[:upper:]]' && classes=$((classes + 1))
    printf '%s' "$pw" | grep -q '[[:lower:]]' && classes=$((classes + 1))
    printf '%s' "$pw" | grep -q '[0-9]'        && classes=$((classes + 1))
    printf '%s' "$pw" | grep -q '[^A-Za-z0-9]' && classes=$((classes + 1))
    (( ${#pw} >= PASSWORD_MIN_LENGTH )) || { echo "au moins ${PASSWORD_MIN_LENGTH} caractères attendus"; return 0; }
    (( ${#pw} <= 200 )) || { echo "au plus 200 caractères"; return 0; }
    (( classes >= 3 )) || { echo "au moins 3 familles parmi majuscules, minuscules, chiffres, symboles"; return 0; }
    low="$(printf '%s' "$pw" | tr '[:upper:]' '[:lower:]')"
    case "$low" in
        *password*|*motdepasse*|*mot-de-passe*|*123456*|*azerty*|*qwerty*|*orchestrator*|*letmein*|*changeme*)
            echo "mot de passe trop courant"; return 0 ;;
    esac
    if [[ -n "${ADMIN_USER:-}" ]] && [[ "$low" == *"$(printf '%s' "$ADMIN_USER" | tr '[:upper:]' '[:lower:]')"* ]]; then
        echo "le mot de passe ne doit pas contenir le nom d'utilisateur"; return 0
    fi
    if [[ -n "${DOMAIN_LOWER:-}" ]]; then
        local label="${DOMAIN_LOWER%%.*}"
        if (( ${#label} >= 3 )) && [[ "$low" == *"$label"* ]]; then
            echo "le mot de passe ne doit pas contenir le domaine"; return 0
        fi
    fi
    return 1
}

# --- Lecture masquée du mot de passe -----------------------------------------
read_admin_password() {
    local attempt=1 p1 p2 reason
    while (( attempt <= MAX_PASSWORD_ATTEMPTS )); do
        if [[ ! -t 0 ]]; then
            die "Le mot de passe doit être saisi dans un terminal interactif (mode masqué)."
        fi
        printf 'Mot de passe administrateur (saisie masquée) : '
        read -r -s p1 || die "Saisie interrompue."
        printf '\nConfirmation du mot de passe : '
        read -r -s p2 || die "Saisie interrompue."
        printf '\n'
        if [[ -z "$p1" ]]; then
            log_warn "Mot de passe vide."
        elif [[ "$p1" != "$p2" ]]; then
            log_warn "Les deux saisies diffèrent."
        else
            # « || true » est indispensable : password_policy_error renvoie 1
            # lorsque le mot de passe est conforme (aucune raison de rejet), et
            # sans cela l'affectation déclencherait errexit/ERR et interromprait
            # l'installation sur un mot de passe valide.
            reason="$(password_policy_error "$p1")" || true
            if [[ -z "$reason" ]]; then
                ADMIN_PASSWORD="$p1"
                unset p1 p2
                log_ok "Mot de passe accepté (politique : ${PASSWORD_MIN_LENGTH} caractères minimum, 3 familles)."
                return 0
            fi
            log_warn "Mot de passe refusé : ${reason}."
        fi
        attempt=$((attempt + 1))
    done
    unset p1 p2
    die "Mot de passe non conforme après ${MAX_PASSWORD_ATTEMPTS} tentatives."
}

# --- Base de données (lecture seule, en Python : sqlite3 CLI peut être absent)
admin_exists() {
    [[ -f "$DB_PATH" ]] || return 1
    python_available || return 1
    "$PY_BIN" - "$DB_PATH" "$ADMIN_USER" <<'PY'
import sqlite3, sys
db, user = sys.argv[1], sys.argv[2]
try:
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
except Exception:
    sys.exit(2)
for (name,) in rows:
    low = name.lower()
    if "admin" not in low and "user" not in low:
        continue
    try:
        cols = [c[1] for c in con.execute('PRAGMA table_info("%s")' % name).fetchall()]
    except Exception:
        continue
    if "username" not in cols:
        continue
    try:
        n = con.execute('SELECT COUNT(*) FROM "%s" WHERE username = ?' % name, (user,)).fetchone()[0]
    except Exception:
        continue
    if n:
        sys.exit(0)
sys.exit(1)
PY
}

db_report() { # affiche : admin_count=<n> state=<valeur|absent>
    if [[ ! -f "$DB_PATH" ]] || ! python_available; then
        printf 'admin_count=0 state=absent\n'
        return 0
    fi
    "$PY_BIN" - "$DB_PATH" <<'PY'
import sqlite3, sys
db = sys.argv[1]
count, state = 0, "absent"
try:
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
except Exception:
    print("admin_count=0 state=illisible")
    raise SystemExit(0)
for (name,) in rows:
    low = name.lower()
    try:
        cols = [c[1] for c in con.execute('PRAGMA table_info("%s")' % name).fetchall()]
    except Exception:
        continue
    if ("admin" in low or "user" in low) and "username" in cols:
        try:
            count = max(count, con.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0])
        except Exception:
            pass
    if "state" in low or "orchestrator" in low:
        for col in ("desired_state", "state", "status", "value"):
            if col in cols:
                try:
                    row = con.execute('SELECT "%s" FROM "%s" LIMIT 1' % (col, name)).fetchone()
                except Exception:
                    continue
                if row:
                    state = str(row[0])
                    break
        if state != "absent":
            break
print("admin_count=%d state=%s" % (count, state))
PY
}

# =============================================================================
#  Rendu des gabarits
# =============================================================================
NGINX_SITE_NAME=""
NGINX_SNIPPET_NAME=""
NGINX_SITE_ACCESS_LOG=""
NGINX_SITE_ERROR_LOG=""
SNIPPET_FILE=""
BACKEND_UPSTREAM=""

set_derived_names() {
    NGINX_SITE_NAME="orchestrator-${DOMAIN_LOWER}"
    NGINX_SNIPPET_NAME="orchestrator-${DOMAIN_LOWER}.conf"
    NGINX_SITE_ACCESS_LOG="${NGINX_LOG_DIR}/orchestrator-${DOMAIN_LOWER}.access.log"
    NGINX_SITE_ERROR_LOG="${NGINX_LOG_DIR}/orchestrator-${DOMAIN_LOWER}.error.log"
    SNIPPET_FILE="${NGINX_SNIPPETS_DIR}/${NGINX_SNIPPET_NAME}"
    BACKEND_UPSTREAM="http://${BACKEND_HOST}:${BACKEND_PORT}"
}

render_nginx() { # $1 = gabarit, $2 = région (snippet|site), $3 = tls (yes|no)
    awk -v region="$2" -v tls="$3" \
        -v domain="${DOMAIN_LOWER:-example.com}" \
        -v snippet_path="$SNIPPET_FILE" \
        -v frontend_root="$FRONTEND_ROOT" \
        -v acme_webroot="$ACME_WEBROOT" \
        -v backend_upstream="$BACKEND_UPSTREAM" \
        -v access_log="$NGINX_SITE_ACCESS_LOG" \
        -v error_log="$NGINX_SITE_ERROR_LOG" '
        BEGIN { region_ok = 0; tls_hide = 0 }
        /^# >>> SNIPPET >>>$/ { region_ok = (region == "snippet"); next }
        /^# <<< SNIPPET <<<$/ { region_ok = 1; next }
        /^# >>> SITE >>>$/    { region_ok = (region == "site"); next }
        /^# <<< SITE <<<$/    { region_ok = 1; next }
        /^# >>> TLSON >>>$/   { if (tls != "yes") tls_hide = 1; next }
        /^# <<< TLSON <<<$/   { tls_hide = 0; next }
        /^# >>> TLSOFF >>>$/  { if (tls == "yes") tls_hide = 1; next }
        /^# <<< TLSOFF <<<$/  { tls_hide = 0; next }
        {
            if (region_ok && !tls_hide) {
                gsub(/__DOMAIN__/, domain)
                gsub(/__SNIPPET_PATH__/, snippet_path)
                gsub(/__FRONTEND_ROOT__/, frontend_root)
                gsub(/__ACME_WEBROOT__/, acme_webroot)
                gsub(/__BACKEND_UPSTREAM__/, backend_upstream)
                gsub(/__NGINX_ACCESS_LOG__/, access_log)
                gsub(/__NGINX_ERROR_LOG__/, error_log)
                print
            }
        }
    ' "$1"
}

render_unit() { # $1 = gabarit, $2 = fichier de sortie
    sed \
        -e "s|__SERVICE_USER__|${SERVICE_USER}|g" \
        -e "s|__SERVICE_GROUP__|${SERVICE_GROUP}|g" \
        -e "s|__BACKEND_DIR__|${BACKEND_DIR}|g" \
        -e "s|__VENV_BIN__|${VENV_BIN}|g" \
        -e "s|__ENV_FILE__|${ENV_FILE}|g" \
        -e "s|__DOCS_DIR__|${DOCS_DIR}|g" \
        -e "s|__DATA_DIR__|${DATA_DIR}|g" \
        -e "s|__LOG_DIR__|${LOG_DIR}|g" \
        "$1" > "$2"
}

verify_no_placeholder() { # $1 fichier produit
    local leftovers
    leftovers="$(grep -o '__[A-Z_]\{2,\}__' "$1" 2>/dev/null | sort -u | tr '\n' ' ')" || true
    if [[ -n "${leftovers// /}" ]]; then
        die "Placeholder(s) non substitué(s) dans $1 : ${leftovers}"
    fi
}

verify_structure() { # contrôle statique du fichier Nginx produit
    local file="$1" open close servers
    open="$(tr -cd '{' < "$file" | wc -c)"
    close="$(tr -cd '}' < "$file" | wc -c)"
    if [[ "$open" != "$close" ]]; then
        die "Accolades déséquilibrées dans ${file} : ${open} ouvrantes / ${close} fermantes."
    fi
    servers="$(grep -c '^server {' "$file" || true)"
    if [[ "$TLS_MODE" == "yes" && "$servers" != "2" ]]; then
        die "${file} : 2 blocs server{} attendus en mode TLS, ${servers} trouvé(s)."
    fi
    if [[ "$TLS_MODE" == "no" && "$servers" != "1" ]]; then
        die "${file} : 1 bloc server{} attendu en mode HTTP seul, ${servers} trouvé(s)."
    fi
    if ! grep -q "^[[:space:]]*server_name ${DOMAIN_LOWER};" "$file"; then
        die "${file} : directive server_name ${DOMAIN_LOWER} absente."
    fi
    log_ok "Contrôle statique du fichier Nginx réussi ($(basename "$file") : ${open} accolades, ${servers} bloc(s) server)."
}

write_nginx_artifacts() { # $1 = répertoire de destination, $2 = tls (yes|no)
    local out="$1" tls="$2"
    install -d -m 0755 "$out"
    render_nginx "$NGINX_TEMPLATE" snippet "$tls" > "${out}/nginx-snippet.conf"
    render_nginx "$NGINX_TEMPLATE" site "$tls" > "${out}/nginx-site.conf"
    verify_no_placeholder "${out}/nginx-site.conf"
    verify_no_placeholder "${out}/nginx-snippet.conf"
    TLS_MODE="$tls"
    verify_structure "${out}/nginx-site.conf"
}

write_unit_artifact() { # $1 = fichier de sortie
    render_unit "$UNIT_TEMPLATE" "$1"
    verify_no_placeholder "$1"
    if ! grep -q '^\[Unit\]' "$1" || ! grep -q '^\[Service\]' "$1" || ! grep -q '^\[Install\]' "$1"; then
        die "Unité systemd invalide : sections [Unit]/[Service]/[Install] incomplètes."
    fi
    if ! grep -q "^ExecStart=${VENV_BIN}/uvicorn app.main:app --host ${BACKEND_HOST} --port ${BACKEND_PORT}$" "$1"; then
        die "Unité systemd invalide : ExecStart inattendu."
    fi
    if ! grep -q "^EnvironmentFile=${ENV_FILE}$" "$1"; then
        die "Unité systemd invalide : EnvironmentFile inattendu."
    fi
    if ! grep -q "^User=${SERVICE_USER}$" "$1"; then
        die "Unité systemd invalide : utilisateur de service inattendu."
    fi
    if ! grep -q '^Restart=on-failure$' "$1" || ! grep -q '^WantedBy=multi-user.target$' "$1"; then
        die "Unité systemd invalide : politique de redémarrage ou cible d'activation absente."
    fi
}

# =============================================================================
#  Étape 1 — Privilèges, système, outils requis
# =============================================================================
step1_prerequisites() {
    step_header "Étape 1/14 — Privilèges, version du système et outils requis"
    CURRENT_STEP="étape 1 (prérequis)"

    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        die "Ce script doit être exécuté avec les privilèges administrateur : sudo bash deploy/setup.sh"
    fi
    log_ok "Privilèges root confirmés."

    if [[ ! -r /etc/os-release ]]; then
        die "/etc/os-release illisible : système non pris en charge (Ubuntu Server requis)."
    fi
    # shellcheck disable=SC1091
    . /etc/os-release
    local distro="${ID:-inconnu}" version="${VERSION_ID:-0}"
    log_info "Système détecté : ${PRETTY_NAME:-${distro} ${version}}"
    if [[ "$distro" != "ubuntu" ]]; then
        die "Distribution « ${distro} » non prise en charge : Ubuntu Server ${MIN_UBUNTU_VERSION}+ requis."
    fi
    if ! version_at_least "$version" "$MIN_UBUNTU_VERSION"; then
        die "Ubuntu ${version} détecté : version minimale requise ${MIN_UBUNTU_VERSION}."
    fi
    log_ok "Distribution et version compatibles."

    local cmd missing=()
    for cmd in bash sed awk grep sort date id mktemp install tr wc chmod chown dpkg apt-get; do
        have_cmd "$cmd" || missing+=("$cmd")
    done
    if (( ${#missing[@]} > 0 )); then
        die "Outils de base manquants : ${missing[*]}. Installez-les (paquet coreutils/dpkg) avant de relancer."
    fi
    if ! have_cmd systemctl && (( ! MAO_SKIP_SYSTEMD )); then
        die "systemctl est introuvable : systemd est requis (docs/INSTALLATION.md §7)."
    fi
    log_ok "Outils de base présents."

    local free_mb
    if [[ -d "$MAO_OPT_ROOT" ]]; then
        free_mb="$(df -Pm "$MAO_OPT_ROOT" | awk 'NR==2 {print $4}')"
    else
        free_mb="$(df -Pm / | awk 'NR==2 {print $4}')"
    fi
    if [[ -n "$free_mb" ]] && (( free_mb < MIN_FREE_MB )); then
        die "Espace disque insuffisant : ${free_mb} Mo libres, ${MIN_FREE_MB} Mo requis."
    fi
    log_ok "Espace disque disponible : ${free_mb:-inconnu} Mo."

    if [[ ! -f "$NGINX_TEMPLATE" ]]; then
        die "Gabarit Nginx introuvable : ${NGINX_TEMPLATE}"
    fi
    if [[ ! -f "$UNIT_TEMPLATE" ]]; then
        die "Gabarit systemd introuvable : ${UNIT_TEMPLATE}"
    fi
    log_ok "Gabarits de déploiement présents."
}

# =============================================================================
#  Étape 2 — Collecte et validation des paramètres
# =============================================================================
step2_collect_parameters() {
    step_header "Étape 2/14 — Collecte des paramètres d'installation"
    CURRENT_STEP="étape 2 (paramètres)"

    if [[ -z "$DOMAIN" ]]; then
        if [[ -t 0 ]]; then
            read -r -p "Entrez votre domaine ou sous-domaine : " DOMAIN || die "Saisie interrompue."
        else
            die "Domaine requis : relancez avec --domain api.exemple.com"
        fi
    fi
    DOMAIN_LOWER="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    validate_domain "$DOMAIN_LOWER" || die "Domaine invalide : « ${DOMAIN} » (attendu : api.exemple.com)."
    log_ok "Domaine : ${DOMAIN_LOWER}"

    if [[ -z "$LETSENCRYPT_EMAIL" ]] && [[ "$TLS_REQUESTED" == "yes" ]]; then
        if [[ -t 0 ]]; then
            read -r -p "Entrez votre adresse e-mail pour Let's Encrypt : " LETSENCRYPT_EMAIL || die "Saisie interrompue."
        fi
    fi
    if [[ "$TLS_REQUESTED" == "yes" ]]; then
        [[ -n "$LETSENCRYPT_EMAIL" ]] || die "Adresse e-mail requise pour Let's Encrypt : utilisez --email, ou --no-https."
        validate_email "$LETSENCRYPT_EMAIL" || die "Adresse e-mail invalide : « ${LETSENCRYPT_EMAIL} »."
        log_ok "Adresse Let's Encrypt : ${LETSENCRYPT_EMAIL}"
    elif [[ -n "$LETSENCRYPT_EMAIL" ]]; then
        log_info "Adresse Let's Encrypt enregistrée (TLS désactivé pour l'instant)."
    else
        # L'adresse e-mail ne sert qu'à l'émission du certificat (docs/INSTALLATION.md
        # §4.2) : avec --no-https, aucun échange n'est demandé, ce qui rend
        # « --no-https --yes » réellement non interactif.
        log_info "TLS désactivé : adresse Let's Encrypt non requise à cette étape."
    fi

    if [[ -z "$ADMIN_USER" ]]; then
        if [[ -t 0 ]]; then
            read -r -p "Nom d'utilisateur administrateur [admin] : " ADMIN_USER || die "Saisie interrompue."
        fi
        ADMIN_USER="${ADMIN_USER:-admin}"
    fi
    ADMIN_USER="$(printf '%s' "$ADMIN_USER" | tr '[:upper:]' '[:lower:]')"
    validate_admin_user "$ADMIN_USER" || die "Nom d'utilisateur invalide : « ${ADMIN_USER} » (3 à 32 caractères alphanumériques, . _ -)."
    log_ok "Compte administrateur : ${ADMIN_USER}"

    log_info "Le mot de passe est saisi en mode masqué et transmis sur stdin : il n'apparaît ni dans l'historique du shell, ni dans les arguments de processus."
    read_admin_password

    printf '\n%sRécapitulatif (aucun secret affiché)%s\n' "$C_BOLD" "$C_OFF"
    printf '  Domaine              : %s\n' "$DOMAIN_LOWER"
    printf '  TLS                  : %s\n' "$([[ "$TLS_REQUESTED" == "yes" ]] && echo "activé via Let's Encrypt (${LETSENCRYPT_EMAIL})" || echo "désactivé")"
    printf '  Compte administrateur: %s (mot de passe masqué)\n' "$ADMIN_USER"
    printf '  Source du code       : %s\n\n' "${SOURCE_URL:-dépôt local ${REPO_ROOT}}"
    if ! confirm "Confirmer et poursuivre l'installation ?" y; then
        die "Installation annulée par l'administrateur."
    fi
}

# =============================================================================
#  Étape 3 — Vérification réseau
# =============================================================================
resolve_domain() {
    local d="$1" ip=""
    if have_cmd getent; then
        ip="$(getent ahostsv4 "$d" 2>/dev/null | awk 'NR==1 {print $1}')" || ip=""
    fi
    if [[ -z "$ip" ]] && have_cmd dig; then
        ip="$(dig +short A "$d" 2>/dev/null | awk 'NR==1 {print $1}')" || ip=""
    fi
    if [[ -z "$ip" ]] && have_cmd python3; then
        ip="$(python3 -c 'import socket,sys; sys.stdout.write(socket.gethostbyname(sys.argv[1]))' "$d" 2>/dev/null)" || ip=""
    fi
    printf '%s' "$ip"
}

detect_public_ip() {
    local ip=""
    if have_cmd curl; then
        ip="$(curl -fsS --max-time 8 https://api.ipify.org 2>/dev/null)" || ip=""
        if [[ -z "$ip" ]]; then
            ip="$(curl -fsS --max-time 8 https://ifconfig.me/ip 2>/dev/null)" || ip=""
        fi
    elif have_cmd wget; then
        ip="$(wget -qO- --timeout=8 https://api.ipify.org 2>/dev/null)" || ip=""
    fi
    printf '%s' "$ip"
}

# --- Connectivité Internet sans outil externe --------------------------------
# L'étape 3 précède l'installation des paquets (étape 5) : curl et wget peuvent
# donc être absents sur un serveur neuf. La connectivité sortante est alors
# vérifiée en TCP par bash lui-même (/dev/tcp), avec un délai borné, ce qui
# évite un échec trompeur. curl/wget/python3 ne servent que de complément.
probe_tcp() { # $1 = hôte, $2 = port — connexion TCP bornée (5 s), sans outil externe
    local host="$1" port="$2"
    if have_cmd timeout; then
        timeout 5 bash -c "exec 3<>/dev/tcp/${host}/${port}" >/dev/null 2>&1
    else
        (exec 3<>"/dev/tcp/${host}/${port}") >/dev/null 2>&1
    fi
}

internet_reachable() { # connectivité sortante réelle (dépôts APT inclus)
    local host rc=1
    for host in archive.ubuntu.com:80 security.ubuntu.com:80 deb.nodesource.com:443 pypi.org:443; do
        if probe_tcp "${host%%:*}" "${host##*:}"; then rc=0; break; fi
    done
    return "$rc"
}

port_listening() { # $1 = port
    local port="$1" out=""
    if have_cmd ss; then
        out="$(ss -ltn 2>/dev/null | awk -v p="$port" '{n=split($4,a,":"); if (a[n]==p) print}')" || out=""
    elif have_cmd netstat; then
        out="$(netstat -ltn 2>/dev/null | awk -v p="$port" '{n=split($4,a,":"); if (a[n]==p) print}')" || out=""
    fi
    [[ -n "$out" ]]
}

port_owner() { # $1 = port -> nom du programme (vide si inconnu)
    local port="$1" line=""
    if have_cmd ss; then
        line="$(ss -ltnp 2>/dev/null | awk -v p="$port" '{n=split($4,a,":"); if (a[n]==p) print}')" || line=""
    fi
    if [[ -z "$line" ]]; then
        printf ''
        return 0
    fi
    printf '%s' "$line" | sed -n 's/.*users:(("\([^"]*\)".*/\1/p' | head -n1
}

step3_network_check() {
    step_header "Étape 3/14 — Vérification réseau (DNS, connectivité, ports)"
    CURRENT_STEP="étape 3 (réseau)"

    local resolved_ip=""
    resolved_ip="$(resolve_domain "$DOMAIN_LOWER")"
    if [[ -z "$resolved_ip" ]]; then
        log_error "Le domaine ${DOMAIN_LOWER} ne se résout pas (DNS absent ou non propagé)."
        log_error "Let's Encrypt ne pourra pas valider le domaine. Corrigez le DNS puis relancez."
        die "Vérification DNS bloquante (docs/INSTALLATION.md §3)."
    fi
    log_ok "DNS : ${DOMAIN_LOWER} -> ${resolved_ip}"

    local public_ip=""
    public_ip="$(detect_public_ip)"
    if [[ -z "$public_ip" ]]; then
        # curl/wget ne sont installés qu'à l'étape 5 : sur un serveur neuf, la
        # connectivité est vérifiée en TCP par bash, jamais déduite d'un échec.
        if ! internet_reachable; then
            log_error "Aucun accès réseau sortant : dépôts APT (archive.ubuntu.com:80), deb.nodesource.com:443 et pypi.org:443 injoignables."
            die "Connectivité réseau bloquante : l'installation des paquets (étape 5) et la compilation du frontend en dépendent. Vérifiez le réseau, le DNS, et un éventuel proxy (APT_PROXY_*/http_proxy)."
        fi
        log_ok "Connectivité sortante confirmée (sonde TCP interne : dépôts APT / NodeSource / PyPI)."
        if have_cmd curl || have_cmd wget; then
            log_warn "Le service d'écho d'adresse IP (api.ipify.org, ifconfig.me) est injoignable : la cohérence DNS n'a pas pu être vérifiée. La connectivité sortante, elle, est confirmée."
        else
            log_warn "curl/wget ne sont pas encore installés (étape 5) : l'adresse publique n'a pas pu être déterminée. La connectivité sortante est confirmée et sera revérifiée après l'étape 5."
        fi
    else
        log_ok "Adresse publique vue depuis Internet : ${public_ip}"
        if [[ "$public_ip" != "$resolved_ip" ]]; then
            log_warn "Incohérence possible : ${DOMAIN_LOWER} résout vers ${resolved_ip} alors que l'adresse publique est ${public_ip}."
            log_warn "Si un NAT, un pare-feu ou un CDN est en place, cet avertissement est normal."
            # Bloquant uniquement si un certificat doit être obtenu : Let's Encrypt
            # exige que le domaine résolve vers cette machine (docs/SECURITY.md §8).
            if [[ "$TLS_REQUESTED" == "yes" ]]; then
                confirm "Poursuivre malgré cette incohérence DNS ?" y || die "Installation interrompue : corrigez le DNS."
            else
                log_info "HTTPS désactivé (--no-https) : l'incohérence DNS n'empêche pas la poursuite."
            fi
        fi
    fi

    local p owner
    for p in 80 443; do
        if port_listening "$p"; then
            owner="$(port_owner "$p")"
            if [[ "$owner" == "nginx" ]]; then
                log_ok "Port ${p} déjà utilisé par Nginx : réutilisation de l'installation existante."
            else
                log_warn "Le port ${p} est déjà utilisé par « ${owner:-processus inconnu} »."
                confirm "Poursuivre malgré le conflit sur le port ${p} ?" n || die "Port ${p} occupé : libérez-le puis relancez."
            fi
        else
            log_ok "Port ${p} libre."
        fi
    done

    if port_listening "$BACKEND_PORT"; then
        if [[ "$(port_owner "$BACKEND_PORT")" == "uvicorn" ]]; then
            log_warn "Le port ${BACKEND_PORT} est déjà utilisé par une instance Uvicorn (installation précédente) : elle sera redémarrée."
        else
            die "Le port ${BACKEND_PORT} est occupé par « $(port_owner "$BACKEND_PORT") » : le backend ne pourrait pas démarrer."
        fi
    else
        log_ok "Port ${BACKEND_PORT} libre (backend sur ${BACKEND_HOST}:${BACKEND_PORT})."
    fi
}

# =============================================================================
#  Étape 4 — Détection de l'infrastructure existante
# =============================================================================
step4_detect_existing() {
    step_header "Étape 4/14 — Détection de l'infrastructure existante"
    CURRENT_STEP="étape 4 (détection)"

    local found=0
    if [[ -d "$APP_DIR" ]]; then
        log_info "Installation précédente détectée : ${APP_DIR}"
        EXISTING_INSTALL=1
        found=1
    fi
    if [[ -f "$ENV_FILE" ]]; then
        log_info "Configuration existante : ${ENV_FILE} (préservée)"
        found=1
    fi
    if [[ -f "$SYSTEMD_UNIT_PATH" ]]; then
        local state="inconnue"
        if have_cmd systemctl && (( ! MAO_SKIP_SYSTEMD )); then
            state="$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
            if sysd_quiet_active; then state="${state}/active"; else state="${state}/inactive"; fi
        fi
        log_info "Service systemd existant : ${SYSTEMD_UNIT_PATH} (état : ${state:-inconnu})"
        SERVICE_PREVIOUSLY_ENABLED=1
        found=1
    fi
    if [[ -e "${NGINX_AVAILABLE_DIR}/orchestrator-${DOMAIN_LOWER}" || -e "${NGINX_ENABLED_DIR}/orchestrator-${DOMAIN_LOWER}" ]]; then
        log_info "Configuration Nginx existante pour ce domaine : orchestrator-${DOMAIN_LOWER}"
        found=1
    fi
    if [[ -f "${LE_LIVE_DIR}/${DOMAIN_LOWER}/fullchain.pem" ]]; then
        log_info "Certificat Let's Encrypt existant pour ${DOMAIN_LOWER} : réutilisation si valide (aucune suppression)."
        found=1
    else
        log_info "Aucun certificat Let's Encrypt pour ${DOMAIN_LOWER} : émission à prévoir si TLS demandé."
    fi
    if [[ -f "$DB_PATH" ]]; then
        log_info "Base de données existante : ${DB_PATH} ($(db_report)) — conservée, aucune réinitialisation."
        found=1
    fi
    if [[ -d "$DATA_DIR" ]]; then
        log_info "Répertoire de données existant : ${DATA_DIR} (conservé)"
        found=1
    fi

    local f
    for f in "$NGINX_ENABLED_DIR"/*; do
        [[ -e "$f" ]] || continue
        [[ "$(basename "$f")" == "orchestrator-${DOMAIN_LOWER}" ]] && continue
        log_info "Site Nginx préexistant préservé : $(basename "$f")"
    done

    if (( found )); then
        log_warn "Des éléments préexistants ont été trouvés. Aucun ne sera supprimé ni écrasé sans validation."
        if (( ! RECONFIGURE )); then
            confirm "Réutiliser l'installation existante (ne pas régénérer les identifiants) ?" y \
                || die "Installation interrompue. Pour régénérer la configuration : --reconfigure."
        fi
    else
        log_ok "Aucune installation préalable détectée : installation neuve."
    fi
}

# =============================================================================
#  Étape 5 — Installation des dépendances manquantes uniquement
# =============================================================================
apt_install() { # $1... = paquets
    local pkgs=("$@")
    log_info "Installation des paquets manquants : ${pkgs[*]}"
    if ! DEBIAN_FRONTEND=noninteractive apt-get install -y \
            -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
            -o Acquire::Retries=3 "${pkgs[@]}"; then
        die "Échec de l'installation des paquets : ${pkgs[*]}. Corrigez puis relancez."
    fi
}

# --- Node.js 20+ : indispensable à la compilation du frontend ----------------
# docs/INSTALLATION.md §5 (étape 5 : « installer uniquement les dépendances
# nécessaires ») et §5 étape 6 (« installer et compiler le frontend »). Les
# dépôts Ubuntu ne fournissent pas Node.js 20 sur 22.04/24.04 : le dépôt officiel
# NodeSource est utilisé, avec contrôle du script téléchargé avant exécution.
ensure_nodejs() {
    local current=""
    if have_cmd node; then
        current="$(node_major_version || true)"
    fi
    if [[ -n "$current" ]] && (( current >= MIN_NODE_MAJOR )) && have_cmd npm; then
        log_ok "Node.js $(node --version) et npm $(npm --version) déjà présents (>= ${MIN_NODE_MAJOR}) : aucune installation."
        return 0
    fi

    if [[ -n "$current" ]]; then
        log_warn "Node.js v${current} détecté : Node.js ${MIN_NODE_MAJOR}+ est requis par Vite (compilation du frontend)."
    else
        log_info "Node.js absent : installation depuis le dépôt officiel NodeSource (${NODE_MAJOR}.x)."
    fi
    have_cmd curl || die "curl est requis pour ajouter le dépôt NodeSource (dépendance manquante à l'étape 5)."

    local setup_script
    setup_script="$(mktemp "${TMPDIR:-/tmp}/mao-nodesource.XXXXXX.sh")"
    case "$setup_script" in
        "${TMPDIR:-/tmp}"/mao-nodesource.*|/tmp/mao-nodesource.*) : ;;
        *) die "Fichier temporaire inattendu : ${setup_script}" ;;
    esac

    log_info "Téléchargement du script de dépôt NodeSource : ${NODESOURCE_SETUP_URL}"
    if ! curl -fsSL --retry 3 --max-time 60 "$NODESOURCE_SETUP_URL" -o "$setup_script"; then
        rm -f -- "$setup_script"
        die "Téléchargement impossible (${NODESOURCE_SETUP_URL}) : vérifiez la connectivité Internet puis relancez setup.sh."
    fi
    # Le script téléchargé est contrôlé avant exécution (docs/INSTALLATION.md §11).
    if [[ ! -s "$setup_script" ]] || ! grep -q 'deb\.nodesource\.com' "$setup_script"; then
        rm -f -- "$setup_script"
        die "Script NodeSource vide ou inattendu : exécution refusée par sécurité."
    fi
    log_ok "Script NodeSource téléchargé et contrôlé ($(wc -c < "$setup_script" | tr -d ' ') octets)."

    local out="" rc=0
    out="$(DEBIAN_FRONTEND=noninteractive bash "$setup_script" 2>&1)" || rc=$?
    rm -f -- "$setup_script"
    if (( rc != 0 )); then
        log_error "Ajout du dépôt NodeSource en échec (code ${rc}) :"
        printf '%s\n' "$out" | tail -n 20 >&2
        die "Dépôt NodeSource non configuré : installez Node.js ${NODE_MAJOR}.x manuellement, puis relancez setup.sh."
    fi
    log_ok "Dépôt officiel NodeSource ${NODE_MAJOR}.x configuré."

    apt_install nodejs

    have_cmd node || die "node reste introuvable après l'installation de Node.js ${NODE_MAJOR}.x."
    have_cmd npm || die "npm reste introuvable après l'installation de Node.js ${NODE_MAJOR}.x."
    current="$(node_major_version || true)"
    if [[ -z "$current" ]] || (( current < MIN_NODE_MAJOR )); then
        die "Node.js v${current:-inconnu} installé : Node.js ${MIN_NODE_MAJOR}+ est requis (Vite). Vérifiez le dépôt NodeSource puis relancez."
    fi
    log_ok "Node.js $(node --version) et npm $(npm --version) installés (>= ${MIN_NODE_MAJOR})."
}

step5_install_dependencies() {
    step_header "Étape 5/14 — Installation des dépendances manquantes uniquement"
    CURRENT_STEP="étape 5 (dépendances)"

    local pkg
    for pkg in bash coreutils dpkg; do
        dpkg -s "$pkg" >/dev/null 2>&1 && log_ok "déjà installé : ${pkg}"
    done

    log_info "Mise à jour de l'index des paquets (apt-get update)…"
    if ! DEBIAN_FRONTEND=noninteractive apt-get update -o Acquire::Retries=3 >/dev/null; then
        log_warn "apt-get update a signalé une erreur : on tente l'installation avec l'index existant."
    fi

    local missing=()
    local pkg_specs=(
        "python3:python3"
        "python3-venv:python3 -m venv (environnement virtuel)"
        "git:git"
        "curl:curl"
        "rsync:rsync (installation du code, étape 6)"
        "openssl:openssl (contrôle des certificats)"
        "ca-certificates:dépôt HTTPS NodeSource"
        "gnupg:gpg (dépôt HTTPS NodeSource)"
        "nginx:nginx"
        "certbot:certbot"
        "python3-certbot-nginx:python3-certbot-nginx"
        "sqlite3:sqlite3 (contrôle direct de la base)"
    )
    local spec
    for spec in "${pkg_specs[@]}"; do
        pkg="${spec%%:*}"
        if dpkg -s "$pkg" >/dev/null 2>&1; then
            log_ok "déjà installé : ${pkg}"
        else
            log_info "manquant : ${pkg} (${spec##*:})"
            missing+=("$pkg")
        fi
    done

    # Node.js n'est nécessaire que si le frontend doit être compilé.
    if (( ${#missing[@]} > 0 )); then
        apt_install "${missing[@]}"
    else
        log_ok "Toutes les dépendances requises sont déjà présentes : aucune installation système."
    fi

    have_cmd python3 || die "python3 reste introuvable après installation."
    PY_BIN="$(command -v python3)"
    log_ok "Python : $("$PY_BIN" --version 2>&1)"

    # python3-venv : sans le module « venv »/« ensurepip », l'étape 6 ne peut pas
    # créer l'environnement virtuel (paquet distinct depuis Debian/Ubuntu).
    if ! "$PY_BIN" -c 'import venv, ensurepip' >/dev/null 2>&1; then
        die "Le module Python « venv »/« ensurepip » est indisponible : installez python3-venv (apt-get install python3-venv) puis relancez setup.sh."
    fi
    log_ok "Module venv opérationnel (${PY_BIN} -m venv)."

    if have_cmd nginx; then
        log_ok "Nginx : $(nginx -v 2>&1 | sed 's/^nginx version: //')"
    else
        die "nginx reste introuvable après installation : le reverse proxy (étape 9) ne pourrait pas être configuré."
    fi

    if have_cmd sqlite3; then
        log_ok "sqlite3 : $(sqlite3 --version 2>/dev/null | awk '{print $1}')"
    else
        log_warn "sqlite3 (CLI) absent : la base reste utilisable par le backend (module sqlite3 de Python)."
    fi

    # Contrôle de connectivité définitif : curl est désormais installé (l'étape 3
    # ne pouvait s'appuyer que sur une sonde TCP en l'absence d'outil HTTP).
    if have_cmd curl; then
        local public_ip=""
        public_ip="$(detect_public_ip)"
        if [[ -n "$public_ip" ]]; then
            log_ok "Connectivité Internet confirmée après installation des outils (adresse publique : ${public_ip})."
        else
            log_warn "api.ipify.org n'a pas répondu : vérifiez la connectivité sortante (l'émission du certificat TLS en dépendra)."
        fi
    fi

    if (( SKIP_FRONTEND )); then
        log_warn "--skip-frontend : Node.js et npm ne sont pas requis (dist préexistant conservé)."
    else
        ensure_nodejs
    fi
}

# =============================================================================
#  Étape 6 — Installation du code (dépôt, venv, dépendances, frontend)
# =============================================================================
ensure_service_user() {
    if (( MAO_SKIP_USER_OPS )); then
        log_warn "MAO_SKIP_USER_OPS=1 : création de l'utilisateur système ignorée (mode test)."
        return 0
    fi
    # Groupe dédié : il est référencé par l'unité systemd (Group=orchestrator).
    # Il est créé explicitement lorsque la politique « useradd » du système ne
    # crée pas de groupe homonyme (USERGROUPS_ENAB=no).
    if ! getent group "$SERVICE_GROUP" >/dev/null 2>&1; then
        log_info "Création du groupe système non privilégié : ${SERVICE_GROUP}"
        groupadd --system "$SERVICE_GROUP" || die "Échec de la création du groupe système ${SERVICE_GROUP}."
        log_ok "Groupe ${SERVICE_GROUP} créé."
    fi
    if id -u "$SERVICE_USER" >/dev/null 2>&1; then
        log_ok "Utilisateur système dédié déjà présent : ${SERVICE_USER}"
    else
        log_info "Création de l'utilisateur système non privilégié : ${SERVICE_USER}"
        local nologin="/usr/sbin/nologin"
        [[ -x "$nologin" ]] || nologin="/bin/false"
        # -g explicite : le groupe dédié vient d'être créé (ou existait déjà) ;
        # sans cette option, useradd tenterait de créer un groupe homonyme et
        # échouerait lorsque USERGROUPS_ENAB=yes.
        if ! useradd --system --create-home --home-dir "$DATA_DIR" --shell "$nologin" \
                --gid "$SERVICE_GROUP" \
                --comment "Multi-Agent Orchestrator service account" "$SERVICE_USER"; then
            die "Échec de la création de l'utilisateur système ${SERVICE_USER}."
        fi
        log_ok "Utilisateur ${SERVICE_USER} créé (aucun privilège administrateur)."
    fi
    make_dir 0750 "${SERVICE_USER}:${SERVICE_GROUP}" "$DATA_DIR" "$LOG_DIR"
}

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
            log_info "Dépôt existant : récupération de la branche ${BRANCH} depuis ${SOURCE_URL}"
            git -C "$APP_DIR" fetch --prune origin || die "git fetch a échoué (${SOURCE_URL})."
            git -C "$APP_DIR" checkout "$BRANCH" || die "Branche ${BRANCH} introuvable."
            git -C "$APP_DIR" reset --hard "origin/${BRANCH}" || die "git reset a échoué."
            return 0
        fi
        tmp_src="$(mktemp -d "${TMPDIR:-/tmp}/mao-setup.XXXXXX")"
        case "$tmp_src" in
            "${TMPDIR:-/tmp}"/mao-setup.*|/tmp/mao-setup.*) : ;;
            *) die "Répertoire temporaire inattendu : ${tmp_src}" ;;
        esac
        log_info "Clonage de ${SOURCE_URL} (branche ${BRANCH})"
        git clone --depth 1 --branch "$BRANCH" "$SOURCE_URL" "${tmp_src}/repo" \
            || die "git clone a échoué (${SOURCE_URL})."
        src="${tmp_src}/repo"
    fi

    [[ -d "${src}/backend" && -d "${src}/frontend" ]] \
        || die "Source de code invalide : ${src} ne contient pas backend/ et frontend/."

    if [[ "$(cd "$src" && pwd -P)" == "$(cd "$APP_DIR" 2>/dev/null && pwd -P)" ]]; then
        die "La source et la destination sont identiques (${src}) : lancez setup.sh depuis le dépôt, pas depuis ${APP_DIR}."
    fi

    log_info "Copie du code vers ${APP_DIR}"
    if ! rsync -a "${excludes[@]}" "${src}/" "${APP_DIR}/"; then
        die "Échec de la copie du code (rsync)."
    fi
    if [[ -n "$tmp_src" ]]; then
        rm -rf -- "$tmp_src"
    fi
    log_ok "Code installé dans ${APP_DIR}"
}

# --- Frontend : dépendances et compilation ------------------------------------
# Le moteur « esbuild » de Vite est un binaire : sans son script d'installation
# (bloqué par défaut depuis npm 11), « npm run build » échoue. On vérifie donc
# explicitement que le moteur est exécutable avant de compiler.
ensure_esbuild_usable() {
    local out="" rc=0
    out="$(run_as_service_user_in_dir "$FRONTEND_DIR" "$NODE_BIN" --input-type=commonjs \
            -e "process.stdout.write(String(require('esbuild').version))" 2>&1)" || rc=$?
    if (( rc == 0 )) && [[ -n "$out" ]]; then
        log_ok "Moteur de compilation ${NPM_INSTALL_SCRIPTS_PACKAGE} opérationnel (v${out})."
        return 0
    fi
    log_warn "Moteur ${NPM_INSTALL_SCRIPTS_PACKAGE} inutilisable : $(printf '%s' "$out" | tail -n 1)"
    return 1
}

npm_frontend() { # $1 = sous-commande npm, reste : arguments npm
    local sub="$1"; shift
    local args=(--no-audit --no-fund)
    if [[ -n "$NPM_ALLOW_SCRIPTS_FILE" ]]; then
        args+=(--userconfig "$NPM_ALLOW_SCRIPTS_FILE")
    fi
    run_as_service_user_in_dir "$FRONTEND_DIR" "$NPM_BIN" "${args[@]}" "$sub" "$@"
}

build_frontend() {
    local pkgjson="${FRONTEND_DIR}/package.json"
    if [[ ! -s "$pkgjson" ]]; then
        die "frontend/package.json absent ou vide : impossible de compiler le frontend (utilisez --skip-frontend avec un dist préexistant)."
    fi
    NPM_BIN="$(command -v npm || true)"
    NODE_BIN="$(command -v node || true)"
    if [[ -z "$NPM_BIN" || -z "$NODE_BIN" ]]; then
        die "node/npm introuvables : impossible de compiler le frontend (relancez setup.sh pour installer Node.js ${MIN_NODE_MAJOR}+, étape 5)."
    fi
    local major=""
    major="$(node_major_version || true)"
    if [[ -z "$major" ]] || (( major < MIN_NODE_MAJOR )); then
        die "Node.js v${major:-inconnu} détecté : Node.js ${MIN_NODE_MAJOR}+ est requis par Vite. Installez-le (étape 5) puis relancez setup.sh."
    fi
    prepare_npm_install_scripts

    # --- Dépendances : « npm ci » puis repli sur « npm install » --------------
    local out="" rc=0 installed=0
    if [[ -f "${FRONTEND_DIR}/package-lock.json" ]]; then
        log_info "Installation des dépendances frontend (npm ci, Node.js $(node --version))…"
        out="$(npm_frontend ci 2>&1)" || rc=$?
        if (( rc == 0 )); then
            installed=1
        else
            log_warn "« npm ci » a échoué (code ${rc}) : nouvelle tentative avec « npm install »."
            printf '%s\n' "$out" | tail -n 10 >&2
        fi
    fi
    if (( ! installed )); then
        log_info "Installation des dépendances frontend (npm install)…"
        rc=0
        out="$(npm_frontend install 2>&1)" || rc=$?
        if (( rc != 0 )); then
            printf '%s\n' "$out" | tail -n 40 >&2
            die "Échec de l'installation des dépendances frontend (code ${rc}, voir la sortie npm ci-dessus) : corrigez la cause puis relancez setup.sh."
        fi
    fi
    if printf '%s' "$out" | grep -qi 'allow-scripts'; then
        log_warn "npm a signalé des scripts d'installation non autorisés (politique allow-scripts) — reliquat contrôlé ci-dessous."
    fi

    # --- Moteur de compilation : contrôle, puis reconstruction si nécessaire ---
    if ! ensure_esbuild_usable; then
        log_info "Reconstruction explicite du paquet ${NPM_INSTALL_SCRIPTS_PACKAGE}…"
        out="$(npm_frontend rebuild "$NPM_INSTALL_SCRIPTS_PACKAGE" 2>&1)" || rc=$?
        printf '%s\n' "$out" | tail -n 10 >&2
        ensure_esbuild_usable \
            || die "Le moteur ${NPM_INSTALL_SCRIPTS_PACKAGE} reste inutilisable : vérifiez l'accès réseau à registry.npmjs.org puis relancez setup.sh."
    fi

    # --- Compilation ----------------------------------------------------------
    if grep -q '"build"[[:space:]]*:' "$pkgjson"; then
        log_info "Compilation du frontend (npm run build)…"
        rc=0
        out="$(npm_frontend run build 2>&1)" || rc=$?
        if (( rc != 0 )); then
            printf '%s\n' "$out" | tail -n 40 >&2
            die "La compilation du frontend a échoué (code ${rc}, voir la sortie ci-dessus) : le service n'est pas démarré avec un frontend incomplet."
        fi
    else
        log_warn "Aucun script « build » déclaré dans package.json : compilation ignorée."
    fi
    if [[ ! -f "${FRONTEND_ROOT}/index.html" ]]; then
        die "Compilation frontend incomplète : ${FRONTEND_ROOT}/index.html est absent."
    fi
    log_ok "Frontend compilé et vérifié : ${FRONTEND_ROOT} ($(find "$FRONTEND_ROOT" -type f | wc -l | tr -d ' ') fichiers)."
}

step6_install_code() {
    step_header "Étape 6/14 — Installation du code, environnement virtuel, dépendances, frontend"
    CURRENT_STEP="étape 6 (code)"

    make_dir 0755 root:root "$APP_BASE" 2>/dev/null || true
    install -d -m 0755 "$APP_DIR"
    ensure_service_user
    copy_source_tree
    chown_to "${SERVICE_USER}:${SERVICE_GROUP}" -R "$APP_DIR"

    if [[ ! -f "${BACKEND_DIR}/app/main.py" ]]; then
        die "Application backend introuvable : ${BACKEND_DIR}/app/main.py est absent (dépôt incomplet)."
    fi
    log_ok "Point d'entrée backend présent : backend/app/main.py"

    if [[ ! -x "$VENV_PY" ]]; then
        log_info "Création de l'environnement virtuel Python : ${VENV_DIR}"
        run_as_service_user "$PY_BIN" -m venv "$VENV_DIR" || die "Échec de la création du venv."
    else
        log_ok "Environnement virtuel existant réutilisé : ${VENV_DIR}"
    fi
    run_as_service_user "$VENV_PY" -m pip install --upgrade --quiet pip setuptools wheel \
        || log_warn "Mise à jour de pip ignorée (échec réseau ?)."

    local req="${BACKEND_DIR}/requirements.txt"
    if [[ ! -f "$req" ]]; then
        log_warn "backend/requirements.txt absent : dépendances backend non installées automatiquement."
    elif [[ ! -s "$req" ]]; then
        log_warn "backend/requirements.txt vide : aucune dépendance backend déclarée à installer."
    else
        log_info "Installation des dépendances backend (pip -r requirements.txt)…"
        run_as_service_user "$VENV_PY" -m pip install --no-input --quiet -r "$req" \
            || die "Échec de l'installation des dépendances backend."
    fi
    if ! run_as_service_user "$VENV_PY" -c 'import uvicorn' >/dev/null 2>&1; then
        log_warn "uvicorn n'est pas importable dans le venv : le service ne pourra pas démarrer tant que backend/requirements.txt ne déclare pas uvicorn."
    else
        log_ok "Dépendances backend opérationnelles (uvicorn importable)."
    fi

    if (( SKIP_FRONTEND )); then
        log_warn "--skip-frontend : compilation ignorée."
        [[ -f "${FRONTEND_ROOT}/index.html" ]] || die "--skip-frontend demandé mais aucun dist présent dans ${FRONTEND_ROOT}."
        log_ok "Frontend préexistant conservé : ${FRONTEND_ROOT}"
    else
        build_frontend
    fi
    chown_to "${SERVICE_USER}:${SERVICE_GROUP}" -R "$APP_DIR"
}

# =============================================================================
#  Étape 7 — Configuration (production.env, chemins persistants, permissions)
# =============================================================================
step7_configure() {
    step_header "Étape 7/14 — Configuration de production et chemins persistants"
    CURRENT_STEP="étape 7 (configuration)"

    make_dir 0750 root:root "$CONFIG_DIR"
    make_dir 0750 "${SERVICE_USER}:${SERVICE_GROUP}" "$DATA_DIR" "$DATA_DIR/data" "$LOG_DIR" "$BACKUP_DIR"
    chown_to "root:${SERVICE_GROUP}" "$CONFIG_DIR"

    local preserved=""
    if [[ -f "$ENV_FILE" ]] && (( ! RECONFIGURE )); then
        preserved="yes"
        log_info "production.env existant conservé : ${ENV_FILE}"
    fi
    if [[ -f "$ENV_FILE" ]]; then
        local backup
        backup="${ENV_FILE}.bak.$(date -u '+%Y%m%dT%H%M%SZ')"
        install -m 600 "$ENV_FILE" "$backup" || die "Impossible de sauvegarder ${ENV_FILE}."
        chown_to "root:${SERVICE_GROUP}" "$backup"
        log_ok "Sauvegarde horodatée de la configuration : ${backup} (aucune donnée supprimée)"
    fi

    local secret_key enrollment_key allowed_origins database_path
    if [[ -n "$preserved" ]]; then
        secret_key="$(env_value "$ENV_FILE" SECRET_KEY || true)"
        enrollment_key="$(env_value "$ENV_FILE" ENROLLMENT_KEY || true)"
        log_info "Identifiants existants conservés (valeurs non affichées)."
    fi
    if [[ -z "${secret_key:-}" ]]; then
        secret_key="$(gen_secret 64)"
        SECRET_KEY_GENERATED="yes"
    fi
    if [[ -z "${enrollment_key:-}" ]]; then
        enrollment_key="$(gen_secret 48)"
        ENROLLMENT_KEY_GENERATED="yes"
    fi
    allowed_origins="https://${DOMAIN_LOWER}"
    database_path="$DB_PATH"

    # --- Écriture atomique, permissions 0600, aucun affichage de secret -------
    local tmp_env
    tmp_env="$(mktemp "${CONFIG_DIR}/.production.env.XXXXXX")"
    case "$tmp_env" in
        "${CONFIG_DIR}"/.production.env.*) : ;;
        *) die "Fichier temporaire inattendu : ${tmp_env}" ;;
    esac
    (
        umask 077
        {
            printf '# Multi-Agent Orchestrator — configuration de production\n'
            printf '# Généré par deploy/setup.sh le %s (UTC). Ne pas commiter.\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
            printf '# Permissions attendues : 0600, propriétaire %s.\n' "$SERVICE_USER"
            printf '# Noms de variables imposés par backend/app/core/config.py.\n'
            printf 'ORCHESTRATOR_ENV=production\n'
            printf 'DATABASE_PATH=%s\n' "$database_path"
            printf 'SECRET_KEY=%s\n' "$secret_key"
            printf 'ADMIN_SESSION_TTL_HOURS=12\n'
            printf 'AGENT_OFFLINE_THRESHOLD_SECONDS=60\n'
            printf 'ENROLLMENT_KEY=%s\n' "$enrollment_key"
            printf 'LOG_LEVEL=INFO\n'
            printf 'LOG_DIR=%s\n' "$LOG_DIR"
            printf 'ALLOWED_ORIGINS=%s\n' "$allowed_origins"
            printf 'TRUSTED_PROXIES=127.0.0.1\n'
            printf 'ENABLE_DOCS=false\n'
        } > "$tmp_env"
    )
    chmod 600 "$tmp_env"
    chown_to "${SERVICE_USER}:${SERVICE_GROUP}" "$tmp_env"
    mv -f -- "$tmp_env" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    chown_to "${SERVICE_USER}:${SERVICE_GROUP}" "$ENV_FILE"
    unset secret_key enrollment_key

    if [[ "$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo '???')" != "600" ]]; then
        log_warn "Permissions de ${ENV_FILE} non conformes (attendu 600) : vérifiez manuellement."
    else
        log_ok "Configuration écrite : ${ENV_FILE} (0600, ${SERVICE_USER}:${SERVICE_GROUP})"
    fi
    [[ -n "$SECRET_KEY_GENERATED" ]] && log_ok "SECRET_KEY généré (secrets.token_urlsafe, valeur non affichée)."
    [[ -n "$ENROLLMENT_KEY_GENERATED" ]] && log_ok "Clé d'enregistrement générée (valeur non affichée ; consultation via le tableau de bord)."

    # --- Métadonnées d'installation (aucun secret) ---------------------------
    {
        printf "# Métadonnées d'installation du Multi-Agent Orchestrator (aucun secret).\n"
        printf 'MAO_DOMAIN=%s\n' "$DOMAIN_LOWER"
        printf 'MAO_EMAIL=%s\n' "$LETSENCRYPT_EMAIL"
        printf 'MAO_ADMIN_USER=%s\n' "$ADMIN_USER"
        printf 'MAO_APP_DIR=%s\n' "$APP_DIR"
        printf 'MAO_VENV=%s\n' "$VENV_DIR"
        printf 'MAO_ENV_FILE=%s\n' "$ENV_FILE"
        printf 'MAO_DATA_DIR=%s\n' "$DATA_DIR"
        printf 'MAO_LOG_DIR=%s\n' "$LOG_DIR"
        printf 'MAO_SERVICE_USER=%s\n' "$SERVICE_USER"
        printf 'MAO_TLS=%s\n' "$TLS_REQUESTED"
        printf 'MAO_INSTALL_DATE=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        printf 'MAO_SOURCE=%s\n' "${SOURCE_URL:-local:${REPO_ROOT}}"
        printf 'MAO_BRANCH=%s\n' "$BRANCH"
    } > "${INSTALL_CONF}.tmp"
    chmod 0640 "${INSTALL_CONF}.tmp"
    chown_to "root:${SERVICE_GROUP}" "${INSTALL_CONF}.tmp"
    mv -f -- "${INSTALL_CONF}.tmp" "$INSTALL_CONF"
    log_ok "Métadonnées d'installation : ${INSTALL_CONF}"

    touch "$LOG_FILE" "$AUDIT_LOG_FILE"
    chmod 0640 "$LOG_FILE" "$AUDIT_LOG_FILE"
    chown_to "${SERVICE_USER}:adm" "$LOG_FILE" "$AUDIT_LOG_FILE"
    log_ok "Journaux applicatifs : ${LOG_FILE} et ${AUDIT_LOG_FILE}"

    install -d -m 0755 "$ACME_WEBROOT/.well-known/acme-challenge"
    log_ok "Racine ACME (certbot --webroot) : ${ACME_WEBROOT}"
}

# =============================================================================
#  Étape 8 — Initialisation de la base, du compte admin et de la clé d'enreg.
# =============================================================================
run_migrations() {
    local migrator="${BACKEND_DIR}/scripts/migrate.py"
    if [[ -f "$migrator" ]]; then
        log_info "Migrations : exécution de scripts/migrate.py"
        # DATABASE_PATH est transmis explicitement : sans lui, le backend
        # retomberait sur son chemin par défaut (backend/data/) au lieu de la
        # base de production (docs/INSTALLATION.md §6).
        run_as_service_user env "DATABASE_PATH=${DB_PATH}" "LOG_DIR=${LOG_DIR}" \
            "$VENV_PY" "$migrator" || die "Échec des migrations de base de données."
        return 0
    fi
    if [[ -f "${BACKEND_DIR}/alembic.ini" ]]; then
        log_info "Migrations : alembic upgrade head"
        run_as_service_user env "DATABASE_PATH=${DB_PATH}" "${VENV_BIN}/alembic" upgrade head \
            || die "Échec des migrations Alembic."
        return 0
    fi
    log_info "Aucun script de migration détecté : le schéma est initialisé par le backend (database/) au démarrage."
}

create_admin_account() {
    local script="${BACKEND_DIR}/scripts/create_admin.py"
    if [[ ! -f "$script" ]]; then
        die "Script d'initialisation du compte administrateur introuvable : ${script}"
    fi
    if admin_exists; then
        log_ok "Compte administrateur « ${ADMIN_USER} » déjà présent : création ignorée (idempotence)."
        return 0
    fi
    log_info "Création du compte administrateur « ${ADMIN_USER} » (mot de passe transmis sur stdin)."
    # « || rc=$? » plutôt que « set +e » : le piège ERR du script reste inactif,
    # l'échec est capturé puis analysé (au lieu d'interrompre l'installation).
    # Le chemin de la base de production est transmis EXPLICITEMENT
    # (--database + DATABASE_PATH) : sans lui, le script d'initialisation
    # écrirait dans backend/data/orchestrator.db au lieu de /var/lib/…
    # (docs/INSTALLATION.md §6 et §5 étape 8).
    local rc=0 out=""
    out="$(printf '%s\n' "$ADMIN_PASSWORD" | run_as_service_user env \
            "DATABASE_PATH=${DB_PATH}" "LOG_DIR=${LOG_DIR}" \
            "$VENV_PY" "$script" \
            --username "$ADMIN_USER" --password-stdin --database "$DB_PATH" 2>&1)" || rc=$?
    if (( rc != 0 )); then
        if printf '%s' "$out" | grep -qiE 'exist|déjà|duplicate|unique'; then
            log_ok "Le compte administrateur existait déjà (idempotence)."
            return 0
        fi
        log_error "Échec de la création du compte administrateur (code ${rc})."
        printf '%s\n' "$out" | redact | tail -n 20 >&2
        return 1
    fi
    if admin_exists; then
        log_ok "Compte administrateur créé et vérifié en base."
    else
        log_warn "Le compte administrateur n'est pas encore visible en base : il sera revérifié après le démarrage du service."
    fi
}

step8_init_database() {
    step_header "Étape 8/14 — Initialisation de la base, du compte administrateur et de la clé d'enregistrement"
    CURRENT_STEP="étape 8 (base de données)"

    run_migrations
    if ! create_admin_account; then
        log_warn "La création du compte est reportée après le démarrage du service (étape 13)."
    fi

    if env_has_key "$ENV_FILE" ENROLLMENT_KEY; then
        log_ok "Clé d'enregistrement présente dans le fichier protégé (valeur non affichée)."
        log_info "Consultation par l'administrateur : tableau de bord (GET /api/v1/settings/enrollment-key) ou sudo grep '^ENROLLMENT_KEY=' ${ENV_FILE}"
    else
        die "Clé d'enregistrement absente de ${ENV_FILE} : relancez setup.sh."
    fi

    local report
    report="$(db_report)"
    log_info "État de la base : ${report}"
    if [[ "$report" == *"state=absent"* ]]; then
        log_info "L'état logique de l'orchestrateur sera initialisé par le backend au démarrage (docs/ARCHITECTURE.md §8)."
    fi
}

# =============================================================================
#  Étape 9 — Configuration Nginx (préservation des autres sites)
# =============================================================================
nginx_test() {
    if ! have_cmd nginx; then
        log_warn "nginx introuvable : contrôle de configuration impossible sur cette machine."
        return 1
    fi
    nginx -t 2>&1
}

reload_nginx() {
    if (( MAO_SKIP_SYSTEMD )); then
        log_warn "[systemd ignoré] rechargement Nginx non effectué."
        return 0
    fi
    if systemctl is-active --quiet nginx 2>/dev/null; then
        systemctl reload nginx || die "Échec du rechargement de Nginx."
        log_ok "Nginx rechargé (les autres sites restent inchangés)."
    else
        systemctl enable --now nginx >/dev/null 2>&1 || die "Échec du démarrage de Nginx."
        log_ok "Nginx démarré et activé au démarrage."
    fi
}

step9_configure_nginx() {
    step_header "Étape 9/14 — Configuration Nginx dédiée (préserve tous les autres sites)"
    CURRENT_STEP="étape 9 (Nginx)"

    set_derived_names
    make_dir 0755 root:root "$NGINX_AVAILABLE_DIR" "$NGINX_ENABLED_DIR" "$NGINX_SNIPPETS_DIR" || true
    install -d -m 0755 "$NGINX_LOG_DIR"

    local stage
    stage="$(mktemp -d "${TMPDIR:-/tmp}/mao-nginx.XXXXXX")"
    case "$stage" in
        "${TMPDIR:-/tmp}"/mao-nginx.*|/tmp/mao-nginx.*) : ;;
        *) die "Répertoire temporaire inattendu : ${stage}" ;;
    esac

    # Phase A : configuration HTTP seule, afin que le challenge ACME soit
    # joignable avant l'émission du certificat (pas de dépendance circulaire).
    write_nginx_artifacts "$stage" no

    local site_target="${NGINX_AVAILABLE_DIR}/${NGINX_SITE_NAME}"
    local link_target="${NGINX_ENABLED_DIR}/${NGINX_SITE_NAME}"
    local target backup

    # Toute configuration préexistante est sauvegardée horodatée avant d'être
    # remplacée, et restaurée automatiquement si l'étape échoue ensuite.
    for target in "$site_target" "$SNIPPET_FILE"; do
        if [[ -f "$target" ]]; then
            backup="${target}.bak.$(date -u '+%Y%m%dT%H%M%SZ')"
            cp -a -- "$target" "$backup" || die "Impossible de sauvegarder ${target}."
            register_rollback_restore "$backup" "$target"
            log_ok "Configuration Nginx existante sauvegardée : ${backup}"
        fi
    done

    install -m 0644 "${stage}/nginx-site.conf" "$site_target"
    install -m 0644 "${stage}/nginx-snippet.conf" "$SNIPPET_FILE"
    register_rollback_file "$site_target"
    register_rollback_file "$SNIPPET_FILE"

    if [[ ! -L "$link_target" ]]; then
        ln -s "$site_target" "$link_target"
        register_rollback_file "$link_target"
        log_ok "Site activé : ${link_target} -> ${site_target}"
    else
        log_ok "Lien symbolique existant conservé : ${link_target}"
    fi

    if ! nginx_test; then
        log_error "nginx -t a refusé la configuration générée : aucun rechargement, aucun autre site touché."
        rollback_partial_state
        ROLLBACK_FILES=()
        rm -rf -- "$stage"
        die "Configuration Nginx invalide : corrigez le gabarit puis relancez."
    fi
    reload_nginx
    NGINX_ACTIVATED=1
    # Configuration validée par « nginx -t » puis rechargée : elle est donc
    # pleinement activée et ne sera plus retirée automatiquement en cas d'échec
    # d'une étape ultérieure (le service, lui, reste à démarrer).
    ROLLBACK_FILES=()
    ROLLBACK_RESTORES=()
    rm -rf -- "$stage"
    log_ok "Reverse proxy configuré pour ${DOMAIN_LOWER} vers ${BACKEND_UPSTREAM}."
}

# =============================================================================
#  Étape 10 — Certificat TLS (réutilisation, jamais de suppression)
# =============================================================================
cert_reusable() { # $1 = domaine
    local d="$1"
    local full="${LE_LIVE_DIR}/${d}/fullchain.pem"
    local key="${LE_LIVE_DIR}/${d}/privkey.pem"
    [[ -f "$full" && -f "$key" ]] || return 1
    have_cmd openssl || return 1
    openssl x509 -in "$full" -noout -checkend 604800 >/dev/null 2>&1 || return 1
    openssl x509 -in "$full" -noout -checkhost "$d" >/dev/null 2>&1 || return 1
    return 0
}

step10_configure_tls() {
    step_header "Étape 10/14 — Certificat TLS (réutilisation d'un certificat valide)"
    CURRENT_STEP="étape 10 (TLS)"

    if [[ "$TLS_REQUESTED" != "yes" ]]; then
        TLS_MODE="no"
        log_warn "TLS désactivé (--no-https) : le service est exposé en HTTP. Relancez setup.sh sans cette option dès que le DNS est prêt (docs/SECURITY.md §8)."
        return 0
    fi

    if cert_reusable "$DOMAIN_LOWER"; then
        log_ok "Certificat Let's Encrypt existant et valide pour ${DOMAIN_LOWER} : réutilisation (aucune émission, aucune suppression)."
    else
        if [[ -f "${LE_LIVE_DIR}/${DOMAIN_LOWER}/fullchain.pem" ]]; then
            log_warn "Un certificat existe pour ${DOMAIN_LOWER} mais il est expiré ou ne couvre pas ce domaine : une nouvelle émission sera demandée."
        fi
        have_cmd certbot || die "certbot introuvable : impossible d'émettre le certificat (relancez l'étape 5)."
        log_info "Émission du certificat Let's Encrypt pour ${DOMAIN_LOWER} (webroot ${ACME_WEBROOT})…"
        local out="" rc=0
        out="$(certbot certonly --webroot -w "$ACME_WEBROOT" -d "$DOMAIN_LOWER" \
                --non-interactive --agree-tos -m "$LETSENCRYPT_EMAIL" \
                --deploy-hook 'systemctl reload nginx' --keep-until-expiring 2>&1)" || rc=$?
        if (( rc != 0 )); then
            log_error "Échec de l'émission du certificat (code ${rc}). Sortie certbot :"
            printf '%s\n' "$out" | sed -n '1,40p' >&2
            log_error "Vérifiez le DNS (${DOMAIN_LOWER} doit résoudre vers ce serveur), le port 80 et les éventuelles limites Let's Encrypt."
            if (( ! EXISTING_INSTALL )); then
                log_warn "Le service reste disponible en HTTP. Relancez setup.sh une fois le problème corrigé : aucun certificat existant n'est supprimé."
                TLS_MODE="no"
                return 0
            fi
            die "Certificat non émis : l'installation existante reste inchangée."
        fi
        log_ok "Certificat émis pour ${DOMAIN_LOWER}."
    fi

    TLS_MODE="yes"

    # Phase B : activation du bloc HTTPS + redirection HTTP → HTTPS.
    local stage
    stage="$(mktemp -d "${TMPDIR:-/tmp}/mao-nginx-tls.XXXXXX")"
    case "$stage" in
        "${TMPDIR:-/tmp}"/mao-nginx-tls.*|/tmp/mao-nginx-tls.*) : ;;
        *) die "Répertoire temporaire inattendu : ${stage}" ;;
    esac
    write_nginx_artifacts "$stage" yes

    local site_target="${NGINX_AVAILABLE_DIR}/${NGINX_SITE_NAME}"
    install -m 0644 "${stage}/nginx-site.conf" "${site_target}.https.tmp"
    if ! nginx_test; then
        log_warn "La configuration HTTPS générée est refusée par nginx -t : retour à la configuration HTTP seule (aucune coupure)."
        rm -f -- "${site_target}.https.tmp"
        rm -rf -- "$stage"
        TLS_MODE="no"
        return 0
    fi
    mv -f -- "${site_target}.https.tmp" "$site_target"
    if ! nginx_test; then
        die "La configuration HTTPS activée est invalide : restaurez ${site_target} depuis la sauvegarde horodatée."
    fi
    reload_nginx
    rm -rf -- "$stage"
    log_ok "HTTPS actif : redirection HTTP → HTTPS et terminaison TLS pour ${DOMAIN_LOWER}."

    if have_cmd systemctl && (( ! MAO_SKIP_SYSTEMD )) && systemctl list-unit-files certbot.timer >/dev/null 2>&1; then
        if ! systemctl is-enabled --quiet certbot.timer 2>/dev/null; then
            if systemctl enable --now certbot.timer >/dev/null 2>&1; then
                log_ok "Renouvellement automatique activé (certbot.timer)."
            else
                log_warn "certbot.timer n'a pas pu être activé : vérifiez le renouvellement manuellement."
            fi
        else
            log_ok "Renouvellement automatique déjà actif (certbot.timer)."
        fi
    fi
}

# =============================================================================
#  Étape 11 — Unité systemd
# =============================================================================
step11_create_systemd_unit() {
    step_header "Étape 11/14 — Création du service systemd durci"
    CURRENT_STEP="étape 11 (systemd)"

    local stage
    stage="$(mktemp -d "${TMPDIR:-/tmp}/mao-unit.XXXXXX")"
    case "$stage" in
        "${TMPDIR:-/tmp}"/mao-unit.*|/tmp/mao-unit.*) : ;;
        *) die "Répertoire temporaire inattendu : ${stage}" ;;
    esac
    write_unit_artifact "${stage}/orchestrator.service"

    if [[ -f "$SYSTEMD_UNIT_PATH" ]]; then
        local backup
        backup="${SYSTEMD_UNIT_PATH}.bak.$(date -u '+%Y%m%dT%H%M%SZ')"
        cp -a -- "$SYSTEMD_UNIT_PATH" "$backup" || die "Impossible de sauvegarder l'unité existante."
        log_ok "Unité existante sauvegardée : ${backup}"
    fi

    install -m 0644 -o root -g root "${stage}/orchestrator.service" "$SYSTEMD_UNIT_PATH" 2>/dev/null \
        || install -m 0644 "${stage}/orchestrator.service" "$SYSTEMD_UNIT_PATH"
    UNIT_CREATED_THIS_RUN=1
    log_ok "Unité installée : ${SYSTEMD_UNIT_PATH}"

    if have_cmd systemd-analyze; then
        log_info "Vérification : systemd-analyze verify ${SYSTEMD_UNIT_PATH}"
        local vout="" vrc=0
        vout="$(systemd-analyze verify "$SYSTEMD_UNIT_PATH" 2>&1)" || vrc=$?
        if (( vrc != 0 )); then
            log_warn "systemd-analyze signale des remarques (souvent liées à des chemins pas encore créés) :"
            printf '%s\n' "$vout" | sed -n '1,20p' >&2
        else
            log_ok "Unité validée par systemd-analyze."
        fi
    else
        log_warn "systemd-analyze absent : validation limitée aux contrôles internes (sections, ExecStart, utilisateur, EnvironmentFile, Restart)."
    fi
    rm -rf -- "$stage"

    sysd daemon-reload || die "systemctl daemon-reload a échoué."
    log_ok "Configuration systemd rechargée."
}

# =============================================================================
#  Étape 12 — Activation et démarrage du service
# =============================================================================
wait_for_service() { # $1 = délai max en secondes
    local timeout="$1" i=0
    if (( MAO_SKIP_SYSTEMD )); then return 0; fi
    while (( i < timeout )); do
        if sysd_quiet_active; then return 0; fi
        sleep 1
        i=$((i + 1))
    done
    return 1
}

step12_start_service() {
    step_header "Étape 12/14 — Activation et démarrage du service"
    CURRENT_STEP="étape 12 (démarrage)"

    if (( MAO_SKIP_SYSTEMD )); then
        log_warn "MAO_SKIP_SYSTEMD=1 : activation/démarrage ignorés (mode test)."
        return 0
    fi

    sysd enable "$SERVICE_NAME" >/dev/null 2>&1 || die "systemctl enable ${SERVICE_NAME} a échoué."
    log_ok "Service activé au démarrage du serveur (WantedBy=multi-user.target)."

    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        log_info "Démarrage du service…"
    else
        log_info "Redémarrage du service (prise en compte de la nouvelle configuration)…"
    fi
    if ! sysd restart "$SERVICE_NAME"; then
        log_error "systemctl restart ${SERVICE_NAME} a échoué."
        printf '%s\n' "--- journal (50 dernières lignes) ---" >&2
        journalctl -u "$SERVICE_NAME" -n 50 --no-pager 2>&1 | redact >&2 || true
        if (( UNIT_CREATED_THIS_RUN )) && (( ! SERVICE_PREVIOUSLY_ENABLED )); then
            log_warn "Service nouvellement créé et non fonctionnel : désactivation pour ne pas laisser de configuration partiellement activée."
            systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true
        fi
        die "Le service n'a pas démarré : corrigez la cause ci-dessus puis relancez setup.sh."
    fi

    if ! wait_for_service 20; then
        log_error "Le service n'est pas actif après 20 secondes."
        journalctl -u "$SERVICE_NAME" -n 50 --no-pager 2>&1 | redact >&2 || true
        die "Service inactif : consultez « journalctl -u ${SERVICE_NAME} -n 50 »."
    fi
    log_ok "Service actif : $(systemctl is-active "$SERVICE_NAME") / $(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || echo 'enabled?')"
}

# =============================================================================
#  Étape 13 — Vérifications finales
# =============================================================================
VERIFICATION_FAILURES=()

# Attend que le backend réponde réellement en HTTP : systemd « active » ne
# garantit pas que le port écoute déjà (uvicorn initialise la base et le cycle
# de vie applicatif après le démarrage du processus).
wait_for_backend_http() { # $1 = délai maximal en secondes (défaut 30)
    local deadline=$(( SECONDS + ${1:-30} ))
    while (( SECONDS < deadline )); do
        if curl -fsS --max-time 3 "http://${BACKEND_HOST}:${BACKEND_PORT}/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

check() { # $1 description, $2 résultat (0 ok), $3 caractère critique (1/0)
    local desc="$1" rc="$2" critical="${3:-1}"
    # Garde-fou : un résultat non numérique (guillemets mal placés autour d'une
    # expression arithmétique, par exemple) provoquerait un « unbound variable »
    # fatal sous set -u. On le traite comme un échec explicite et actionnable.
    if [[ ! "$rc" =~ ^-?[0-9]+$ ]]; then
        log_error "Vérification « ${desc} » : résultat non numérique (« ${rc} ») — erreur d'appel interne à corriger."
        rc=1
    fi
    if (( rc == 0 )); then
        log_ok "Vérification : ${desc}"
        return 0
    fi
    if (( critical )); then
        log_error "Vérification ÉCHOUÉE : ${desc}"
        VERIFICATION_FAILURES+=("${desc}")
    else
        log_warn "Vérification non concluante : ${desc}"
    fi
    # Toujours 0 : toutes les vérifications sont exécutées et l'échec est
    # consolidé en fin d'étape (message actionnable, code de sortie non nul).
    return 0
}

step13_verify() {
    step_header "Étape 13/14 — Vérifications finales"
    CURRENT_STEP="étape 13 (vérifications)"

    set_derived_names

    # 1. Service systemd
    if (( ! MAO_SKIP_SYSTEMD )); then
        check "service systemd ${SERVICE_NAME} actif" "$(sysd_quiet_active && echo 0 || echo 1)" 1
        check "service activé au démarrage" "$(systemctl is-enabled --quiet "$SERVICE_NAME" && echo 0 || echo 1)" 1
    fi

    # 2. Santé du backend en local
    if have_cmd curl; then
        # « active » ne signifie pas « prêt à répondre » : uvicorn ouvre le port
        # après l'initialisation de la base et du cycle de vie applicatif.
        # Attente bornée avant de conclure à un échec (30 s).
        wait_for_backend_http 30 \
            || log_warn "Le backend n'a pas répondu en HTTP dans les 30 s ; le diagnostic reste effectué ci-dessous."
        local body=""
        body="$(curl -fsS --max-time 5 "http://${BACKEND_HOST}:${BACKEND_PORT}/health" 2>/dev/null)" || body=""
        check "santé backend http://${BACKEND_HOST}:${BACKEND_PORT}/health (${body:-aucune réponse})" \
            "$([[ -n "$body" ]] && echo 0 || echo 1)" 1

        # 3. Frontend servi par Nginx + en-têtes
        local headers="" code=""
        if [[ "$TLS_MODE" == "yes" ]]; then
            headers="$(curl -kfsS --max-time 8 --resolve "${DOMAIN_LOWER}:443:127.0.0.1" \
                        -D - -o /dev/null "https://${DOMAIN_LOWER}/" 2>/dev/null)" || headers=""
        else
            headers="$(curl -fsS --max-time 8 --resolve "${DOMAIN_LOWER}:80:127.0.0.1" \
                        -D - -o /dev/null "http://${DOMAIN_LOWER}/" 2>/dev/null)" || headers=""
        fi
        code="$(printf '%s' "$headers" | sed -n 's/^HTTP\/[0-9.]* \([0-9]\{3\}\).*/\1/p' | head -n1)"
        check "frontend servi par Nginx (HTTP ${code:-aucun})" \
            "$([[ "$code" == "200" ]] && echo 0 || echo 1)" 1
        check "en-tête de sécurité X-Content-Type-Options présent" \
            "$(printf '%s' "$headers" | grep -qi 'X-Content-Type-Options' && echo 0 || echo 1)" 0

        # 4. Accessibilité HTTPS depuis l'extérieur (NAT/loopback : non bloquant)
        if [[ "$TLS_MODE" == "yes" ]]; then
            check "HTTPS externe https://${DOMAIN_LOWER}/health" \
                "$(curl -fsS --max-time 10 "https://${DOMAIN_LOWER}/health" >/dev/null 2>&1 && echo 0 || echo 1)" 0
            check "certificat TLS valide (> 7 jours)" "$(cert_reusable "$DOMAIN_LOWER" && echo 0 || echo 1)" 0
        else
            log_warn "HTTPS inactif : le service n'est accessible qu'en HTTP (docs/SECURITY.md §8)."
        fi
    else
        log_warn "curl absent : vérifications HTTP non effectuées."
    fi

    # 5. Base de données et compte administrateur
    check "base de données accessible : ${DB_PATH}" "$([[ -r "$DB_PATH" ]] && echo 0 || echo 1)" 1
    local report admin_count
    report="$(db_report)"
    admin_count="$(printf '%s' "$report" | sed -n 's/.*admin_count=\([0-9]*\).*/\1/p')"
    if [[ -z "$admin_count" ]]; then admin_count=0; fi
    check "compte administrateur « ${ADMIN_USER} » présent (${report})" \
        "$( (( admin_count >= 1 )) && echo 0 || echo 1 )" 1
    if [[ "$admin_count" -lt 1 ]]; then
        log_warn "Nouvelle tentative de création du compte administrateur (le schéma vient peut-être d'être créé au démarrage)."
        if create_admin_account; then
            report="$(db_report)"
            admin_count="$(printf '%s' "$report" | sed -n 's/.*admin_count=\([0-9]*\).*/\1/p')"
            [[ -n "$admin_count" ]] || admin_count=0
            check "compte administrateur présent après nouvelle tentative (${report})" \
                "$( (( admin_count >= 1 )) && echo 0 || echo 1 )" 1
        fi
    fi

    # 6. État logique persistant
    if [[ "$report" == *"state=absent"* || "$report" == *"state=inconnu"* || "$report" == *"state=illisible"* ]]; then
        log_warn "État logique ONLINE/OFFLINE non détecté en base : le backend l'initialise à son premier démarrage (vérifiez GET /api/v1/settings/orchestrator)."
    else
        log_ok "État logique persistant détecté : ${report##* }"
    fi

    # 7. Chemins de journaux et permissions sensibles
    check "journaux applicatifs présents (${LOG_DIR})" "$([[ -d "$LOG_DIR" ]] && echo 0 || echo 1)" 0
    if [[ -f "$ENV_FILE" ]]; then
        local mode
        mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo '???')"
        check "permissions de production.env = 600 (constaté : ${mode})" "$([[ "$mode" == "600" ]] && echo 0 || echo 1)" 1
    fi

    if (( ${#VERIFICATION_FAILURES[@]} > 0 )); then
        log_error "Vérifications critiques en échec :"
        local f
        for f in "${VERIFICATION_FAILURES[@]}"; do log_error "  - ${f}"; done
        die "Installation incomplète : corrigez les points ci-dessus puis relancez setup.sh (idempotent)."
    fi
}

# =============================================================================
#  Étape 14 — Résumé final (aucun secret)
# =============================================================================
step14_summary() {
    step_header "Étape 14/14 — Résumé de l'installation"
    set_derived_names

    local scheme="http"
    [[ "$TLS_MODE" == "yes" ]] && scheme="https"
    local service_state="inconnu"
    if (( ! MAO_SKIP_SYSTEMD )); then
        service_state="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo inactif) / $(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || echo non-activé)"
    fi

    cat <<EOF

${C_BOLD}Multi-Agent Orchestrator — installation terminée${C_OFF}
  Domaine              : ${DOMAIN_LOWER}
  Tableau de bord      : ${scheme}://${DOMAIN_LOWER}/
  Connexion            : ${scheme}://${DOMAIN_LOWER}/login (compte : ${ADMIN_USER})
  API (santé)          : ${scheme}://${DOMAIN_LOWER}/health  •  ${scheme}://${DOMAIN_LOWER}/api/v1/
  État du service      : ${service_state}
  Application          : ${APP_DIR}
  Environnement virtuel: ${VENV_DIR}
  Configuration        : ${ENV_FILE} (0600)
  Base de données      : ${DB_PATH}
  Journaux             : ${LOG_DIR}/application.log et ${LOG_DIR}/audit.log
  Journaux systemd     : journalctl -u ${SERVICE_NAME}
  Site Nginx           : ${NGINX_AVAILABLE_DIR}/${NGINX_SITE_NAME} (+ snippet ${SNIPPET_FILE})
  Sauvegardes          : ${BACKUP_DIR}

Vérification du service :
  sudo systemctl status ${SERVICE_NAME}
  sudo journalctl -u ${SERVICE_NAME} -f
  curl -s ${scheme}://${DOMAIN_LOWER}/health

Gestion du compte administrateur et de la clé d'enregistrement :
  La clé d'enregistrement et les secrets sont dans ${ENV_FILE} (root uniquement).
  Consultation recommandée : tableau de bord, section Sécurité.
  Rotation de la clé : POST /api/v1/settings/enrollment-key/rotate

Opérations suivantes :
  Mise à jour    : sudo bash deploy/update.sh
  Désinstallation: sudo bash deploy/uninstall.sh   (les données sont conservées par défaut)
  Aide           : bash deploy/README.md

Aucun secret n'a été affiché : le mot de passe administrateur n'est stocké qu'en
empreinte par le backend, et les secrets ne figurent que dans ${ENV_FILE}.
EOF
}

# =============================================================================
#  Mode « rendu seul » (CI et tests, ne touche pas au système)
# =============================================================================
do_render_only() {
    local out="$RENDER_ONLY"
    if [[ -z "$DOMAIN_LOWER" ]]; then
        if [[ -n "$DOMAIN" ]]; then
            DOMAIN_LOWER="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
            validate_domain "$DOMAIN_LOWER" || die "Domaine invalide : « ${DOMAIN} »."
        else
            DOMAIN_LOWER="example.com"
            log_warn "Aucun domaine fourni (--domain) : rendu avec le domaine de démonstration ${DOMAIN_LOWER}."
        fi
    fi
    set_derived_names
    install -d -m 0755 "$out"
    write_nginx_artifacts "$out" "$TLS_REQUESTED"
    write_unit_artifact "${out}/orchestrator.service"

    # Variante HTTP seule (utilisée par la CI et les tests)
    local httpdir="${out}/no-tls"
    write_nginx_artifacts "$httpdir" no

    printf 'Rendu terminé (aucune modification du système) :\n'
    printf '  Nginx (site, TLS %s) : %s/nginx-site.conf\n' "$TLS_REQUESTED" "$out"
    printf '  Nginx (snippet)      : %s/nginx-snippet.conf\n' "$out"
    printf '  Nginx (site, HTTP)   : %s/nginx-site.conf\n' "$httpdir"
    printf '  systemd              : %s/orchestrator.service\n' "$out"
}

print_paths() {
    printf 'APP_DIR=%s\nBACKEND_DIR=%s\nVENV_DIR=%s\nVENV_PY=%s\n' "$APP_DIR" "$BACKEND_DIR" "$VENV_DIR" "$VENV_PY"
    printf 'FRONTEND_ROOT=%s\nCONFIG_DIR=%s\nENV_FILE=%s\nINSTALL_CONF=%s\n' "$FRONTEND_ROOT" "$CONFIG_DIR" "$ENV_FILE" "$INSTALL_CONF"
    printf 'DATA_DIR=%s\nDB_PATH=%s\nLOG_DIR=%s\nACME_WEBROOT=%s\n' "$DATA_DIR" "$DB_PATH" "$LOG_DIR" "$ACME_WEBROOT"
    printf 'NGINX_AVAILABLE_DIR=%s\nNGINX_ENABLED_DIR=%s\nNGINX_SNIPPETS_DIR=%s\n' \
        "$NGINX_AVAILABLE_DIR" "$NGINX_ENABLED_DIR" "$NGINX_SNIPPETS_DIR"
    printf 'SYSTEMD_UNIT_PATH=%s\nSERVICE_NAME=%s\nSERVICE_USER=%s\n' "$SYSTEMD_UNIT_PATH" "$SERVICE_NAME" "$SERVICE_USER"
}

# =============================================================================
#  Analyse des arguments et point d'entrée
# =============================================================================
parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            --domain)       need_value "$@"; DOMAIN="$2"; shift 2 ;;
            --domain=*)     DOMAIN="${1#*=}"; shift ;;
            --email)        need_value "$@"; LETSENCRYPT_EMAIL="$2"; shift 2 ;;
            --email=*)      LETSENCRYPT_EMAIL="${1#*=}"; shift ;;
            --admin-user)   need_value "$@"; ADMIN_USER="$2"; shift 2 ;;
            --admin-user=*) ADMIN_USER="${1#*=}"; shift ;;
            --source-url)   need_value "$@"; SOURCE_URL="$2"; shift 2 ;;
            --source-url=*) SOURCE_URL="${1#*=}"; shift ;;
            --branch)       need_value "$@"; BRANCH="$2"; shift 2 ;;
            --branch=*)     BRANCH="${1#*=}"; shift ;;
            --no-https)     TLS_REQUESTED="no"; shift ;;
            --skip-frontend) SKIP_FRONTEND=1; shift ;;
            --reconfigure)  RECONFIGURE=1; shift ;;
            --yes|-y)       ASSUME_YES=1; shift ;;
            --render-only)  need_value "$@"; RENDER_ONLY="$2"; shift 2 ;;
            --render-only=*) RENDER_ONLY="${1#*=}"; shift ;;
            --print-paths)  PRINT_PATHS=1; shift ;;
            -h|--help)      usage; exit 0 ;;
            *) die "Option inconnue : $1 (voir --help)" ;;
        esac
    done
}

main() {
    parse_args "$@"

    if (( PRINT_PATHS )); then
        print_paths
        exit 0
    fi

    if [[ -n "$RENDER_ONLY" ]]; then
        do_render_only
        exit 0
    fi

    printf '%sMulti-Agent Orchestrator — installation sur Ubuntu Server%s\n' "$C_BOLD" "$C_OFF"
    printf 'Système de fichiers cible : %s, %s, %s\n' "$MAO_OPT_ROOT" "$MAO_ETC_ROOT" "$MAO_VAR_ROOT"

    step1_prerequisites
    step2_collect_parameters
    step3_network_check
    step4_detect_existing
    step5_install_dependencies
    step6_install_code
    step7_configure
    step8_init_database
    step9_configure_nginx
    step10_configure_tls
    step11_create_systemd_unit
    step12_start_service
    step13_verify
    step14_summary

    unset ADMIN_PASSWORD
    CURRENT_STEP="terminé"
    log_ok "Installation terminée avec succès."
}

# Exécution normale. MAO_LIB_ONLY=1 permet à un test de charger les fonctions
# (rollback, rendu) sans lancer l'installation : réservé aux tests automatisés.
if [[ "${MAO_LIB_ONLY:-0}" != "1" ]]; then
    main "$@"
fi
