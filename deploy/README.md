# Déploiement — Multi-Agent Orchestrator (Ubuntu Server)

Ce répertoire contient **tout le déploiement de production**. Références :
`docs/INSTALLATION.md` (installation, mise à jour, désinstallation),
`docs/SECURITY.md` §3.2, §8, §14, §15 (utilisateur dédié, HTTPS, secrets),
`docs/ARCHITECTURE.md` §3.3 et §12 (Nginx, Certbot, systemd, SQLite).

| Fichier | Rôle |
|---|---|
| `setup.sh` | Installation complète en 14 étapes : prérequis, saisie masquée, réseau, détection de l'existant, dépendances, code, configuration, base, Nginx, TLS, systemd, démarrage, vérifications, résumé. |
| `update.sh` | Mise à jour des 11 points de `docs/INSTALLATION.md` §8 + sauvegarde et **retour arrière** (`--rollback`). |
| `uninstall.sh` | Désinstallation avec confirmations distinctes ; **données conservées par défaut**. |
| `nginx/orchestrator.conf` | Gabarit Nginx (jeton `__DOMAIN__`). Rendu par `setup.sh` en **deux** fichiers : le site et un snippet partagé. |
| `systemd/orchestrator.service` | Gabarit d'unité systemd durcie (utilisateur `orchestrator`, `EnvironmentFile`, durcissement). |
| `tests/test-uninstall-preservation.sh` | Test de non-régression : prouve que la désinstallation préserve Nginx étranger, les données et les certificats. |
| `tests/test-setup-rollback.sh` | Test unitaire : une configuration préexistante remplacée par `setup.sh` est restaurée si l'étape échoue. |
| `tests/validate-nginx-config.py` | Validation statique d'un fichier Nginx rendu (utilisée quand `nginx` est absent ; la CI lance en plus `nginx -t`). |

---

## 1. Prérequis

* Ubuntu Server **22.04 minimum** (cible : 24.04), accès `sudo`, accès Internet.
* Un domaine (ou sous-domaine) dont l'enregistrement **A/AAAA pointe déjà** vers le serveur.
* Les ports **80** et **443** accessibles depuis Internet.
* Espace disque : ≥ 2 Go libres.

Les paquets manquants sont installés par `setup.sh` **uniquement s'ils sont absents** :
`python3`, `python3-venv`, `git`, `curl`, `rsync`, `openssl`, `ca-certificates`,
`nginx`, `certbot`, `python3-certbot-nginx`, plus `nodejs`/`npm` si le frontend doit être compilé.
Aucun paquet n'est jamais désinstallé et les fichiers de configuration existants
(`--force-confold`) ne sont pas écrasés.

## 2. Chemins de production

```text
/opt/multi-agent-orchestrator/application/            code (backend/ + frontend/)
/opt/multi-agent-orchestrator/application/backend/.venv   environnement virtuel Python
/etc/multi-agent-orchestrator/production.env          secrets (0600)
/etc/multi-agent-orchestrator/install.conf            métadonnées (aucun secret, 0640)
/var/lib/multi-agent-orchestrator/orchestrator.db     base SQLite persistante
/var/lib/multi-agent-orchestrator/data/               données persistantes
/var/lib/multi-agent-orchestrator/backups/            sauvegardes horodatées (0700)
/var/log/multi-agent-orchestrator/                    application.log, audit.log
/etc/nginx/sites-available/orchestrator-<domaine>     site Nginx dédié
/etc/nginx/snippets/orchestrator-<domaine>.conf       règles partagées (proxy + statique)
/etc/systemd/system/orchestrator.service              service systemd
```

## 3. Installation

```bash
sudo bash deploy/setup.sh
```

Options utiles : `--domain`, `--email`, `--admin-user`, `--source-url`, `--branch`,
`--no-https`, `--skip-frontend`, `--reconfigure`, `--yes`, `--render-only <dir>`,
`--print-paths`, `--help`.

Le script demande ensuite le domaine, l'e-mail Let's Encrypt, le nom d'utilisateur
administrateur puis le **mot de passe saisi en mode masqué** (`read -s`, politique :
12 caractères minimum, 3 familles de caractères, pas de mot de passe courant).
Le mot de passe est transmis sur **stdin** au script backend
(`scripts/create_admin.py --username <nom> --password-stdin`) : il n'apparaît
jamais dans un argument de processus, un journal ou l'historique du shell.

Le script est **idempotent** : une réexécution ne recrée pas le compte
administrateur, ne régénère pas les secrets et sauvegarde horodatée toute
configuration qu'il remplacerait. Il s'arrête au premier échec bloquant sans
laisser de configuration partiellement activée.

### Ordre interne notable (TLS)

1. Nginx est d'abord activé **en HTTP seul** (le challenge ACME doit être joignable).
2. `certbot certonly --webroot` émet ou réutilise le certificat du domaine.
3. Le site est ensuite réécrit avec le bloc HTTPS + redirection HTTP → HTTPS, puis
   rechargé — et **revient à la version HTTP seule** si `nginx -t` refuse la nouvelle
   configuration (aucune coupure, aucun certificat supprimé).

### Compte administrateur et clé d'enregistrement

* Le compte est créé en base ; seul le hachage du mot de passe y est stocké.
* La clé d'enregistrement est générée (`secrets.token_urlsafe(48)`) et écrite dans
  `production.env`. **Consultation recommandée : tableau de bord → Sécurité**
  (`GET /api/v1/settings/enrollment-key`). En dernier recours :
  `sudo grep '^ENROLLMENT_KEY=' /etc/multi-agent-orchestrator/production.env`.
* Rotation : `POST /api/v1/settings/enrollment-key/rotate` (tableau de bord).

## 4. Mise à jour

```bash
sudo bash deploy/update.sh                 # version locale du dépôt
sudo bash deploy/update.sh --branch main   # branche d'un dépôt Git (--source-url)
sudo bash deploy/update.sh --skip-frontend
```

Déroulé : vérification de l'installation et du service → **sauvegarde cohérente de la
base** (API backup SQLite, service en marche) et de la configuration (0600) →
arrêt bref du service → récupération du code → dépendances → migrations →
compilation du frontend → redémarrage → vérification de `GET /health` → rapport.

Les données persistantes ne sont **jamais** supprimées ni réinitialisées.

### Retour arrière

```bash
sudo bash deploy/update.sh --list-backups
sudo bash deploy/update.sh --rollback                    # dernière sauvegarde
sudo bash deploy/update.sh --rollback 20260924T120000Z   # sauvegarde nommée
sudo bash deploy/update.sh --rollback --with-config      # restaure aussi production.env
```

Le retour arrière arrête le service, **conserve la base courante** sous
`orchestrator.db.pre-rollback.<horodatage>`, restaure la base sauvegardée (empreinte
SHA-256 vérifiée si disponible), revient à la révision de code enregistrée lorsque
l'application est un dépôt Git, puis redémarre et vérifie la santé. Les sauvegardes
ne sont jamais supprimées automatiquement.

## 5. Désinstallation

```bash
sudo bash deploy/uninstall.sh                     # application seule, données CONSERVÉES
sudo bash deploy/uninstall.sh --purge-config      # + /etc/multi-agent-orchestrator
sudo bash deploy/uninstall.sh --purge-data --purge-logs
sudo bash deploy/uninstall.sh --purge-user
sudo bash deploy/uninstall.sh --dry-run           # simulation, aucune modification
```

* La première confirmation exige la saisie du **domaine** (ou `--yes`).
* La suppression de la **base et des journaux** exige une **seconde confirmation
  distincte** (saisie de `SUPPRIMER`). En automatisation seulement :
  `--confirm-data-loss`. `--yes` **ne suffit pas** à supprimer des données.
* Jamais touchés : autres sites Nginx, certificats TLS préexistants, paquet Nginx,
  bases de données étrangères, autres services, pare-feu. Un garde-fou refuse tout
  chemin qui ne relève pas du projet.

## 6. Maintenance

```bash
# Service
sudo systemctl status orchestrator
sudo systemctl restart orchestrator
sudo systemctl stop orchestrator
sudo journalctl -u orchestrator -f
sudo journalctl -u orchestrator -n 100 --no-pager

# Santé
curl -s https://<domaine>/health
curl -s http://127.0.0.1:8000/health

# Nginx
sudo nginx -t
sudo systemctl reload nginx
sudo tail -f /var/log/nginx/orchestrator-<domaine>.access.log

# Certificat
sudo certbot certificates
sudo certbot renew --dry-run

# Base de données (sauvegarde cohérente, à chaud)
sudo -u orchestrator sqlite3 /var/lib/multi-agent-orchestrator/orchestrator.db \
  ".backup '/var/lib/multi-agent-orchestrator/backups/manuel-$(date -u +%Y%m%dT%H%M%SZ).db'"
# Sans le paquet sqlite3 : la sauvegarde Python équivalente est utilisée par update.sh

# Restauration manuelle
sudo systemctl stop orchestrator
sudo cp -a /var/lib/multi-agent-orchestrator/backups/<horodatage>/orchestrator.db \
          /var/lib/multi-agent-orchestrator/orchestrator.db
sudo chown orchestrator:orchestrator /var/lib/multi-agent-orchestrator/orchestrator.db
sudo systemctl start orchestrator

# Configuration et permissions
sudo stat -c '%a %U:%G %n' /etc/multi-agent-orchestrator/production.env   # 600 orchestrator:orchestrator
sudo systemctl is-enabled orchestrator
```

L'état logique `ONLINE`/`OFFLINE` est **applicatif** et persistant : il ne se
confond pas avec l'état systemd et n'est pas modifié par un redémarrage.

## 7. Tests du déploiement

```bash
# Syntaxe et analyse statique
bash -n deploy/setup.sh deploy/update.sh deploy/uninstall.sh deploy/tests/*.sh
shellcheck -S warning deploy/*.sh deploy/tests/*.sh

#  Préservation à la désinstallation (arborescence isolée, rien de système touché)
bash deploy/tests/test-uninstall-preservation.sh

# Annulation partielle de setup.sh (configuration préexistante restaurée)
bash deploy/tests/test-setup-rollback.sh

# Rendu des gabarits sans rien installer, puis validation structurelle
bash deploy/setup.sh --render-only /tmp/mao-render --domain api.example.com
python3 deploy/tests/validate-nginx-config.py --mode tls --domain api.example.com \
  /tmp/mao-render/nginx-site.conf
python3 deploy/tests/validate-nginx-config.py --mode snippet /tmp/mao-render/nginx-snippet.conf

# Unité systemd (Ubuntu)
sudo systemd-analyze verify /etc/systemd/system/orchestrator.service
```

La CI (`.github/workflows/tests.yml`) exécute `bash -n`, `shellcheck`, un vrai
`nginx -t` (avec un certificat auto-signé) suivi de requêtes réelles sur le
challenge ACME / le frontend / un chemin sensible, `systemd-analyze verify`, le test
de préservation, `pytest` et la compilation du frontend.

## 8. Points à confirmer avec le backend

1. **Journaux applicatifs.** `docs/INSTALLATION.md` §6 demande
   `/var/log/multi-agent-orchestrator/{application.log,audit.log}` ; le contrat
   d'environnement du backend (`ORCHESTRATOR_ENV`, `DATABASE_PATH`, `SECRET_KEY`,
   `ADMIN_SESSION_TTL_HOURS`, `AGENT_OFFLINE_THRESHOLD_SECONDS`, `ENROLLMENT_KEY`,
   `LOG_LEVEL`, `ALLOWED_ORIGINS`, `TRUSTED_PROXIES`, `ENABLE_DOCS`) ne comporte
   **aucune variable de chemin de journal**. `setup.sh` crée donc le répertoire et
   les deux fichiers (0640, `orchestrator:adm`), et le service journalise sur
   `journald` (`journalctl -u orchestrator`). Si le backend écrit dans des fichiers,
   il doit utiliser cet emplacement.
2. **Routes de documentation interactive.** Elles ne sont pas publiées par Nginx
   (`ENABLE_DOCS=false` en production, `docs/SECURITY.md` §16.13).
3. **WebSocket.** Le snippet désactive `Connection: upgrade` vers le backend ; si un
   temps réel WebSocket est ajouté, il faudra une directive `map $http_upgrade` dans
   le contexte `http` et `proxy_set_header Upgrade`.

## 9. Environnements isolés (tests)

`setup.sh`, `update.sh` et `uninstall.sh` acceptent des racines redirigées pour les
tests : `MAO_ETC_ROOT`, `MAO_OPT_ROOT`, `MAO_VAR_ROOT`, `MAO_SYSTEMCTL`
(stub `systemctl`), `MAO_SKIP_USER_OPS`, `MAO_SKIP_SYSTEMD`, et pour
`uninstall.sh` `MAO_ALLOW_NON_ROOT=1` (refusé si les racines par défaut `/etc`,
`/opt`, `/var` sont conservées). Ces variables ne doivent **jamais** être utilisées
sur un serveur de production.
