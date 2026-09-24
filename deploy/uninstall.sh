#!/usr/bin/env bash
# =============================================================================
#  Multi-Agent Orchestrator — deploy/uninstall.sh
#
#  Désinstallation contrôlée. Référence : docs/INSTALLATION.md §9 et §10.
#
#  Principes :
#    * confirmation explicite obligatoire (saisie du domaine, ou --yes) ;
#    * par défaut les DONNÉES sont CONSERVÉES (base SQLite, data/, journaux) ;
#      leur suppression exige une SECONDE confirmation distincte ;
#    * aucune infrastructure étrangère n'est touchée : autres sites Nginx,
#      certificats TLS préexistants, paquet Nginx, bases de données étrangères,
#      utilisateurs et services sans rapport avec l'orchestrateur ;
#    * le script refuse de supprimer un chemin qui ne contient pas le nom du
#      projet (garde-fou contre les erreurs de configuration).
#
#  Utilisation :
#    sudo bash deploy/uninstall.sh                       # application seule
#    sudo bash deploy/uninstall.sh --purge-config        # + /etc/multi-agent-orchestrator
#    sudo bash deploy/uninstall.sh --purge-data --purge-logs
#    sudo bash deploy/uninstall.sh --purge-user
#
#  Environnements isolés (tests) :
#    MAO_OPT_ROOT / MAO_ETC_ROOT / MAO_VAR_ROOT / MAO_SYSTEMCTL / MAO_SKIP_USER_OPS
# =============================================================================
set -Eeuo pipefail

SERVICE_NAME="orchestrator"
SERVICE_USER="orchestrator"
SERVICE_GROUP="orchestrator"
PROJECT_SLUG="multi-agent-orchestrator"

: "${MAO_ETC_ROOT:=/etc}"
: "${MAO_OPT_ROOT:=/opt}"
: "${MAO_VAR_ROOT:=/var}"
: "${MAO_SYSTEMCTL:=systemctl}"
: "${MAO_SKIP_USER_OPS:=0}"
: "${MAO_SKIP_SYSTEMD:=0}"
: "${MAO_NGINX_TEST:=nginx}"
# Réservé aux tests automatisés : autorise une exécution hors root lorsqu'une
# arborescence isolée est utilisée. Refusé si les racines système par défaut
# sont conservées (voir main()).
: "${MAO_ALLOW_NON_ROOT:=0}"

APP_BASE="${MAO_OPT_ROOT}/${PROJECT_SLUG}"
APP_DIR="${APP_BASE}/application"
CONFIG_DIR="${MAO_ETC_ROOT}/${PROJECT_SLUG}"
ENV_FILE="${CONFIG_DIR}/production.env"
INSTALL_CONF="${CONFIG_DIR}/install.conf"
DATA_DIR="${MAO_VAR_ROOT}/lib/${PROJECT_SLUG}"
DB_PATH="${DATA_DIR}/orchestrator.db"
LOG_DIR="${MAO_VAR_ROOT}/log/${PROJECT_SLUG}"
ACME_WEBROOT="${MAO_VAR_ROOT}/www/${PROJECT_SLUG}"
NGINX_AVAILABLE_DIR="${MAO_ETC_ROOT}/nginx/sites-available"
NGINX_ENABLED_DIR="${MAO_ETC_ROOT}/nginx/sites-enabled"
NGINX_SNIPPETS_DIR="${MAO_ETC_ROOT}/nginx/snippets"
SYSTEMD_DIR="${MAO_ETC_ROOT}/systemd/system"
SYSTEMD_UNIT_PATH="${SYSTEMD_DIR}/${SERVICE_NAME}.service"
LE_LIVE_DIR="${MAO_ETC_ROOT}/letsencrypt/live"

ASSUME_YES=0
PURGE_CONFIG=0
PURGE_DATA=0
PURGE_LOGS=0
PURGE_USER=0
SKIP_NGINX=0
CONFIRM_DATA_LOSS=0
DRY_RUN=0

SERVICE_USER_FROM_CONF=""
MAO_DOMAIN_CONF=""
CURRENT_STEP="initialisation"
REMOVED_ANYTHING=0

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
on_error() {
    local rc=$? line="${BASH_LINENO[0]:-?}"
    log_error "Échec pendant « ${CURRENT_STEP} » (ligne ${line}, code ${rc})."
    log_error "Aucune suppression supplémentaire n'a été effectuée."
    exit "$rc"
}
trap on_error ERR

usage() {
    cat <<'EOF'
Multi-Agent Orchestrator — désinstallation contrôlée

Usage :
  sudo bash deploy/uninstall.sh [options]

Par défaut :
  * arrêt et désactivation du service orchestrator.service ;
  * suppression des fichiers applicatifs /opt/multi-agent-orchestrator ;
  * suppression de la configuration Nginx propre a l'orchestrateur
    (sites-available/orchestrator-<domaine>, lien sites-enabled, snippet) ;
  * CONSERVATION des données (/var/lib/multi-agent-orchestrator),
    des journaux, de /etc/multi-agent-orchestrator et des certificats TLS.

Options :
  --purge-config   Supprimer /etc/multi-agent-orchestrator (secrets compris).
  --purge-data     Supprimer la base de données et le répertoire de données.
  --purge-logs     Supprimer les journaux applicatifs.
  --purge-user     Supprimer l'utilisateur système dédié (sans supprimer ses données).
  --confirm-data-loss
                   Seconde confirmation exigée pour --purge-data/--purge-logs
                   (automatisation uniquement : irréversible).
  --skip-nginx     Ne toucher à aucune configuration Nginx.
  --dry-run        Afficher ce qui serait fait sans rien modifier.
  --yes            Accepter la première confirmation (le mot de passe n'est pas concerné).
  -h, --help       Afficher cette aide.

Environnements isolés (tests automatisés uniquement) :
  MAO_ETC_ROOT / MAO_OPT_ROOT / MAO_VAR_ROOT  racines redirigées
  MAO_SYSTEMCTL        commande systemctl à utiliser (stub possible)
  MAO_SKIP_USER_OPS=1  ne pas gérer l'utilisateur système
  MAO_ALLOW_NON_ROOT=1 exécution hors root (refusée si les racines par défaut
                       /etc, /opt, /var sont conservées)
EOF
}

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

confirm_typed() { # $1 = texte exact, $2 = question, $3 = "--destructive" pour ignorer --yes
    local expected="$1" question="$2" mode="${3:-}" answer=""
    if [[ "$mode" != "--destructive" ]] && (( ASSUME_YES )); then
        log_info "Confirmation automatique (--yes) : ${question}"
        return 0
    fi
    if [[ ! -t 0 ]]; then
        if [[ "$mode" == "--destructive" ]]; then
            log_warn "Entrée non interactive : confirmation destructive refusée (automatisation : --confirm-data-loss)."
        else
            log_warn "Entrée non interactive et --yes absent : action refusée."
        fi
        return 1
    fi
    printf '%s\n' "$question"
    printf 'Pour confirmer, saisissez exactement « %s » : ' "$expected"
    read -r answer || return 1
    [[ "$answer" == "$expected" ]]
}

chown_to_owner() {
    local path="$1" owner="${SERVICE_USER}:${SERVICE_GROUP}"
    if (( MAO_SKIP_USER_OPS )); then return 0; fi
    chown "$owner" "$path" 2>/dev/null || true
}

# --- Garde-fous ---------------------------------------------------------------
assert_safe_project_path() { # $1 = chemin
    local p="$1" dir base
    case "$p" in
        ""|"/"|"/opt"|"/etc"|"/var"|"/usr"|"/home"|"/root"|"/bin"|"/sbin"|"/lib"|"/var/lib"|"/var/log"|".")
            die "Refus de traiter un chemin système : « ${p} »" ;;
    esac
    # Cas 1 : le chemin appartient au répertoire du projet (chemin de prod).
    if [[ "$p" == *"${PROJECT_SLUG}"* ]]; then
        return 0
    fi
    # Cas 2 : fichier du namespace « orchestrator » dans un répertoire que ce
    # projet gère explicitement (unité systemd, sites et snippets Nginx).
    dir="$(dirname -- "$p")"
    base="$(basename -- "$p")"
    case "$dir" in
        "$SYSTEMD_DIR"|"$NGINX_AVAILABLE_DIR"|"$NGINX_ENABLED_DIR"|"$NGINX_SNIPPETS_DIR")
            case "$base" in
                orchestrator|orchestrator.*|orchestrator-*) return 0 ;;
            esac
            ;;
    esac
    die "Refus de traiter « ${p} » : hors du périmètre du projet (garde-fou)."
}

remove_path() { # $1 = chemin, $2 = description ; sûr vis-à-vis de --dry-run
    local p="$1" desc="$2"
    assert_safe_project_path "$p"
    if [[ ! -e "$p" && ! -L "$p" ]]; then
        log_info "Déjà absent : ${desc} (${p})"
        return 0
    fi
    if (( DRY_RUN )); then
        log_info "[simulation] suppression de ${desc} : ${p}"
        return 0
    fi
    rm -rf -- "$p"
    REMOVED_ANYTHING=1
    log_ok "Supprimé : ${desc} (${p})"
}

remove_file() { # $1 = chemin, $2 = description
    local p="$1" desc="$2"
    assert_safe_project_path "$p"
    if [[ ! -e "$p" && ! -L "$p" ]]; then
        log_info "Déjà absent : ${desc} (${p})"
        return 0
    fi
    if (( DRY_RUN )); then
        log_info "[simulation] suppression de ${desc} : ${p}"
        return 0
    fi
    rm -f -- "$p"
    REMOVED_ANYTHING=1
    log_ok "Supprimé : ${desc} (${p})"
}

load_install_conf() {
    if [[ -f "$INSTALL_CONF" ]]; then
        # shellcheck disable=SC1090
        . "$INSTALL_CONF" || true
        SERVICE_USER_FROM_CONF="${MAO_SERVICE_USER:-}"
        MAO_DOMAIN_CONF="${MAO_DOMAIN:-}"
    fi
}

# =============================================================================
#  1. Inventaire de l'installation
# =============================================================================
inventory() {
    step_header "Inventaire de l'installation détectée"
    local found=0

    if [[ -d "$APP_DIR" ]]; then
        log_info "Application          : ${APP_DIR}"
        found=1
    else
        log_info "Application          : absente (${APP_DIR})"
    fi
    if [[ -f "$SYSTEMD_UNIT_PATH" ]]; then
        log_info "Service systemd      : ${SYSTEMD_UNIT_PATH} (actif : $(service_active && echo oui || echo non))"
        found=1
    fi
    if [[ -f "$ENV_FILE" ]]; then
        log_info "Configuration        : ${ENV_FILE} (contient les secrets — conservée par défaut)"
        found=1
    fi
    if [[ -f "$DB_PATH" ]]; then
        log_info "Base de données      : ${DB_PATH} (conservée par défaut)"
        found=1
    fi
    if [[ -d "$DATA_DIR" ]]; then
        log_info "Données              : ${DATA_DIR}"
        found=1
    fi
    if [[ -d "$LOG_DIR" ]]; then
        log_info "Journaux             : ${LOG_DIR} (conservés par défaut)"
        found=1
    fi

    local site
    if [[ -n "$MAO_DOMAIN_CONF" ]]; then
        site="orchestrator-${MAO_DOMAIN_CONF}"
        if [[ -e "${NGINX_AVAILABLE_DIR}/${site}" || -L "${NGINX_ENABLED_DIR}/${site}" ]]; then
            log_info "Site Nginx           : ${site}"
            found=1
        fi
    fi
    local f
    for f in "$NGINX_AVAILABLE_DIR"/orchestrator-* "$NGINX_SNIPPETS_DIR"/orchestrator-*; do
        [[ -e "$f" ]] || continue
        log_info "Fichier Nginx        : $(basename "$f")"
        found=1
    done

    if [[ -d "${LE_LIVE_DIR}/${MAO_DOMAIN_CONF:-__aucun__}" ]]; then
        log_info "Certificat TLS       : ${LE_LIVE_DIR}/${MAO_DOMAIN_CONF} (JAMAIS supprimé par ce script)"
    fi

    # Inventaire des autres sites Nginx (jamais modifiés)
    local others=0
    for f in "$NGINX_ENABLED_DIR"/*; do
        [[ -e "$f" || -L "$f" ]] || continue
        [[ "$(basename "$f")" == orchestrator-* ]] && continue
        others=$((others + 1))
    done
    if (( others > 0 )); then
        log_info "Autres sites Nginx   : ${others} (préservés, Nginx ne sera pas désinstallé)"
    fi

    if (( ! found )); then
        log_warn "Aucune installation du Multi-Agent Orchestrator détectée dans ${MAO_OPT_ROOT} / ${MAO_ETC_ROOT} / ${MAO_VAR_ROOT}."
        if (( ! ASSUME_YES )); then
            die "Rien à désinstaller (utilisez --yes pour forcer une exécution de nettoyage)."
        fi
    fi
}

# =============================================================================
#  2. Confirmation explicite
# =============================================================================
require_confirmation() {
    step_header "Confirmation de désinstallation"

    printf '%sActions prévues :%s\n' "$C_BOLD" "$C_OFF"
    printf '  - arrêt et désactivation du service %s\n' "$SERVICE_NAME"
    printf '  - suppression des fichiers applicatifs %s\n' "$APP_DIR"
    printf '  - suppression de la configuration Nginx propre a l'\''orchestrateur\n'
    printf '  - %s\n' "$([[ $PURGE_CONFIG -eq 1 ]] && echo "SUPPRESSION de ${CONFIG_DIR} (secrets inclus)" || echo "conservation de ${CONFIG_DIR}")"
    printf '  - %s\n' "$([[ $PURGE_DATA -eq 1 ]] && echo "SUPPRESSION de la BASE DE DONNÉES ${DB_PATH} (irréversible)" || echo "conservation des données ${DATA_DIR}")"
    printf '  - %s\n' "$([[ $PURGE_LOGS -eq 1 ]] && echo "SUPPRESSION des journaux ${LOG_DIR}" || echo "conservation des journaux ${LOG_DIR}")"
    printf '\n'

    local expected="DESINSTALLER"
    if [[ -n "$MAO_DOMAIN_CONF" ]]; then
        expected="$MAO_DOMAIN_CONF"
    fi
    if ! confirm_typed "$expected" "Cette opération supprime l'application. Confirmation explicite requise."; then
        die "Désinstallation annulée (confirmation explicite absente)."
    fi
    log_ok "Confirmation reçue."

    # --- Seconde confirmation DISTINCTE pour la perte de données -------------
    if (( PURGE_DATA || PURGE_LOGS )); then
        step_header "Seconde confirmation — suppression des données"
        log_warn "Les données ne sont PAS récupérables après cette opération."
        if (( PURGE_DATA )); then
            log_warn "  base de données : ${DB_PATH}"
        fi
        if (( PURGE_LOGS )); then
            log_warn "  journaux        : ${LOG_DIR}"
        fi
        if (( CONFIRM_DATA_LOSS )); then
            log_warn "--confirm-data-loss fourni : suppression des données confirmée pour automatisation."
        elif confirm_typed "SUPPRIMER" "Seconde confirmation : suppression définitive des données demandée." --destructive; then
            log_ok "Suppression des données confirmée distinctement."
        else
            log_warn "Seconde confirmation distincte absente : DONNÉES CONSERVÉES."
            PURGE_DATA=0
            PURGE_LOGS=0
        fi
    fi
}

# =============================================================================
#  3. Service systemd
# =============================================================================
stop_service() {
    step_header "Service systemd"
    if [[ ! -f "$SYSTEMD_UNIT_PATH" ]]; then
        log_info "Aucune unité systemd à retirer."
        return 0
    fi
    if (( DRY_RUN )); then
        log_info "[simulation] arrêt, désactivation et suppression de ${SYSTEMD_UNIT_PATH}"
        return 0
    fi
    if (( ! MAO_SKIP_SYSTEMD )); then
        if ! sysd stop "$SERVICE_NAME"; then
            log_warn "Arrêt du service non effectué (déjà arrêté ou systemd indisponible)."
        fi
        sysd disable "$SERVICE_NAME" >/dev/null 2>&1 || true
        sysd daemon-reload || true
    else
        log_warn "MAO_SKIP_SYSTEMD=1 : arrêt/désactivation du service ignorés (mode test)."
    fi
    log_ok "Service arrêté et désactivé."
    remove_file "$SYSTEMD_UNIT_PATH" "unité systemd"
    if (( ! MAO_SKIP_SYSTEMD )); then
        sysd daemon-reload || true
    fi
    log_ok "Unité systemd supprimée (les autres services ne sont pas touchés)."
}

# =============================================================================
#  4. Configuration Nginx propre à l'orchestrateur uniquement
# =============================================================================
remove_nginx_config() {
    step_header "Configuration Nginx (uniquement celle de l'orchestrateur)"
    if (( SKIP_NGINX )); then
        log_info "--skip-nginx : aucune configuration Nginx modifiée."
        return 0
    fi

    local others=0 f
    for f in "$NGINX_ENABLED_DIR"/*; do
        [[ -e "$f" || -L "$f" ]] || continue
        [[ "$(basename "$f")" == orchestrator-* ]] && continue
        others=$((others + 1))
    done
    log_info "Autres sites Nginx détectés : ${others} (ils ne sont pas modifiés)."

    # Lien symbolique (sites-enabled) — uniquement notre namespace.
    for f in "$NGINX_ENABLED_DIR"/orchestrator-*; do
        [[ -e "$f" || -L "$f" ]] || continue
        remove_file "$f" "lien Nginx activé $(basename "$f")"
    done
    # Fichier de site (sites-available) — uniquement notre namespace.
    for f in "$NGINX_AVAILABLE_DIR"/orchestrator-*; do
        [[ -e "$f" || -L "$f" ]] || continue
        remove_file "$f" "site Nginx $(basename "$f")"
    done
    # Snippet partagé.
    for f in "$NGINX_SNIPPETS_DIR"/orchestrator-*; do
        [[ -e "$f" || -L "$f" ]] || continue
        remove_file "$f" "snippet Nginx $(basename "$f")"
    done

    if (( DRY_RUN )); then
        log_info "[simulation] contrôle de configuration Nginx non exécuté."
        return 0
    fi

    # Le paquet Nginx est conservé, quoi qu'il arrive.
    if have_cmd "$MAO_NGINX_TEST" && have_cmd nginx; then
        if nginx -t >/dev/null 2>&1; then
            log_ok "Configuration Nginx toujours valide après nettoyage (nginx -t)."
            if (( ! MAO_SKIP_SYSTEMD )) && systemctl is-active --quiet nginx 2>/dev/null; then
                if systemctl reload nginx >/dev/null 2>&1; then
                    log_ok "Nginx rechargé : les autres sites continuent de fonctionner."
                else
                    log_warn "Nginx n'a pas pu être rechargé automatiquement : lancez « sudo systemctl reload nginx »."
                fi
            fi
        else
            log_warn "nginx -t signale un problème APRÈS le nettoyage : vérifiez votre configuration."
            log_warn "Aucune autre configuration Nginx n'a été modifiée par ce script."
        fi
    else
        log_warn "Nginx est absent de cette machine : la validité de la configuration restante n'a pas pu être vérifiée (nginx -t)."
        log_warn "Sur Ubuntu, contrôlez avec : sudo nginx -t && sudo systemctl reload nginx"
    fi
    log_info "Nginx reste installé : d'autres sites peuvent en dépendre. Désinstaller le paquet est un choix manuel :"
    log_info "  sudo apt-get remove --purge nginx nginx-common   # uniquement si plus aucun site n'en dépend"
}

# =============================================================================
#  5. Certificats TLS — jamais supprimés
# =============================================================================
tls_notice() {
    step_header "Certificats TLS (aucune suppression)"
    local domain="${MAO_DOMAIN_CONF:-<domaine inconnu>}"
    if [[ -d "${LE_LIVE_DIR}/${domain}" ]]; then
        log_info "Certificat conservé : ${LE_LIVE_DIR}/${domain}"
        log_info "Suppression manuelle, à votre discrétion :"
        log_info "  sudo certbot delete --cert-name ${domain}"
        log_info "  (à ne faire que si ce domaine n'est plus servi par Nginx)"
    else
        log_info "Aucun certificat Let's Encrypt trouvé pour ${domain}."
    fi
    log_info "Renouvellement automatique : le minuteur certbot.timer est conservé (il peut servir d'autres certificats)."
}

# =============================================================================
#  6. Application, configuration, données, utilisateur
# =============================================================================
remove_application() {
    step_header "Fichiers applicatifs"
    remove_path "$APP_DIR" "application"
    if [[ -d "$APP_BASE" ]] && [[ -z "$(ls -A "$APP_BASE" 2>/dev/null)" ]]; then
        remove_path "$APP_BASE" "répertoire racine vide de l'application"
    elif [[ -d "$APP_BASE" ]]; then
        log_info "Conservé (non vide, contient des éléments étrangers) : ${APP_BASE}"
    fi
}

remove_configuration() {
    step_header "Configuration"
    if (( PURGE_CONFIG )); then
        remove_path "$CONFIG_DIR" "configuration (secrets inclus)"
    else
        log_info "Configuration conservée : ${CONFIG_DIR}"
        log_info "  Elle contient les secrets (SECRET_KEY, ENROLLMENT_KEY) nécessaires pour"
        log_info "  réutiliser la base conservée. Suppression : --purge-config."
    fi
}

remove_data() {
    step_header "Données et journaux"
    if (( PURGE_DATA )); then
        remove_path "$DATA_DIR" "données persistantes (base SQLite incluse)"
    else
        log_info "Données conservées : ${DATA_DIR}"
        log_info "  Sauvegarde manuelle recommandée : sudo cp -a ${DATA_DIR} /chemin/vers/sauvegarde/"
    fi
    if (( PURGE_LOGS )); then
        remove_path "$LOG_DIR" "journaux applicatifs"
    else
        log_info "Journaux conservés : ${LOG_DIR}"
    fi
    if [[ -d "$ACME_WEBROOT" ]]; then
        remove_path "$ACME_WEBROOT" "racine ACME du projet (${ACME_WEBROOT})"
    fi
}

remove_service_user() {
    step_header "Utilisateur système dédié"
    local user="${SERVICE_USER_FROM_CONF:-$SERVICE_USER}"
    if (( ! PURGE_USER )); then
        log_info "Utilisateur « ${user} » conservé (les données conservées lui appartiennent)."
        log_info "  Suppression : --purge-user (les fichiers doivent être réattribués au préalable)."
        return 0
    fi
    if (( MAO_SKIP_USER_OPS )); then
        log_warn "MAO_SKIP_USER_OPS=1 : suppression de l'utilisateur ignorée (mode test)."
        return 0
    fi
    if ! id -u "$user" >/dev/null 2>&1; then
        log_info "Utilisateur « ${user} » absent."
        return 0
    fi
    if (( DRY_RUN )); then
        log_info "[simulation] suppression de l'utilisateur ${user}"
        return 0
    fi
    if userdel "$user" 2>/dev/null; then
        log_ok "Utilisateur « ${user} » supprimé (son répertoire de données est conservé)."
    else
        log_warn "La suppression de l'utilisateur « ${user} » a échoué : supprimez-le manuellement."
    fi
}

# =============================================================================
#  7. Résumé
# =============================================================================
summary() {
    step_header "Résumé de la désinstallation"
    cat <<EOF

${C_BOLD}Désinstallation terminée${C_OFF}
  Application          : supprimée (${APP_DIR})
  Service systemd      : supprimé (${SYSTEMD_UNIT_PATH})
  Configuration Nginx  : supprimée (sites-available/orchestrator-*, liens, snippet)
  Nginx                : installé et inchangé pour les autres sites
  Certificats TLS      : conservés
  Configuration        : $([[ $PURGE_CONFIG -eq 1 ]] && echo "supprimée (${CONFIG_DIR})" || echo "conservée (${CONFIG_DIR})")
  Données              : $([[ $PURGE_DATA -eq 1 ]] && echo "supprimées (${DATA_DIR})" || echo "conservées (${DATA_DIR})")
  Journaux             : $([[ $PURGE_LOGS -eq 1 ]] && echo "supprimés (${LOG_DIR})" || echo "conservés (${LOG_DIR})")
  Utilisateur système  : $([[ $PURGE_USER -eq 1 ]] && echo "supprimé" || echo "conservé")

$([[ $REMOVED_ANYTHING -eq 1 ]] && echo "Des éléments ont été supprimés (voir le détail ci-dessus)." || echo "Aucune suppression n'était nécessaire : l'installation était déjà absente.")

Réinstallation : sudo bash deploy/setup.sh --domain <domaine> --email <adresse>
  Les données conservées seront réutilisées (aucune réinitialisation).
EOF
    if (( DRY_RUN )); then
        printf '\n%sMode simulation : AUCUNE modification n a été appliquée.%s\n' "$C_YELLOW" "$C_OFF"
    fi
}

# =============================================================================
#  Programme principal
# =============================================================================
parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            --purge-config) PURGE_CONFIG=1; shift ;;
            --purge-data)   PURGE_DATA=1; shift ;;
            --purge-logs)   PURGE_LOGS=1; shift ;;
            --purge-user)   PURGE_USER=1; shift ;;
            --confirm-data-loss) CONFIRM_DATA_LOSS=1; shift ;;
            --skip-nginx)   SKIP_NGINX=1; shift ;;
            --dry-run)      DRY_RUN=1; shift ;;
            --yes|-y)       ASSUME_YES=1; shift ;;
            -h|--help)      usage; exit 0 ;;
            *) die "Option inconnue : $1 (voir --help)" ;;
        esac
    done
}

main() {
    parse_args "$@"

    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        if (( MAO_ALLOW_NON_ROOT )); then
            if [[ "$MAO_ETC_ROOT" == "/etc" || "$MAO_OPT_ROOT" == "/opt" || "$MAO_VAR_ROOT" == "/var" ]]; then
                die "MAO_ALLOW_NON_ROOT=1 est refusé avec les racines système par défaut. Utilisez des racines isolées (MAO_ETC_ROOT/MAO_OPT_ROOT/MAO_VAR_ROOT)."
            fi
            log_warn "MAO_ALLOW_NON_ROOT=1 : exécution hors root autorisée (réservé aux tests avec racines isolées)."
        else
            die "Ce script doit être exécuté en root : sudo bash deploy/uninstall.sh"
        fi
    fi
    if (( DRY_RUN )); then
        log_warn "Mode simulation (--dry-run) : aucune modification ne sera appliquée."
    fi

    load_install_conf
    assert_safe_project_path "$APP_DIR"
    assert_safe_project_path "$DATA_DIR"
    assert_safe_project_path "$CONFIG_DIR"

    printf '%sMulti-Agent Orchestrator — désinstallation%s\n' "$C_BOLD" "$C_OFF"
    inventory
    require_confirmation

    CURRENT_STEP="service systemd"        ; stop_service
    CURRENT_STEP="configuration Nginx"    ; remove_nginx_config
    CURRENT_STEP="certificats TLS"        ; tls_notice
    CURRENT_STEP="fichiers applicatifs"   ; remove_application
    CURRENT_STEP="configuration"          ; remove_configuration
    CURRENT_STEP="données et journaux"    ; remove_data
    CURRENT_STEP="utilisateur système"    ; remove_service_user

    CURRENT_STEP="terminé"
    summary
    log_ok "Désinstallation terminée."
}

main "$@"
