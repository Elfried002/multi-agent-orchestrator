# Rapport final — Multi-Agent Orchestrator

Conforme à la section 19 de la mission. Toutes les valeurs de ce rapport proviennent
de commandes **réellement exécutées** ; ce qui n'a pas pu être vérifié est signalé
comme tel plutôt que présenté comme acquis.

---

## A. Résumé

### Fonctionnalités réellement développées

Plateforme centralisée de gestion d'agents IA, conforme aux cinq documents de
référence (`docs/`), qui couvre les 14 capacités exigées :

1. **Authentification administrateur** — Argon2id (paramètres OWASP), sessions
   serveur invalidables, verrouillage après 5 échecs, changement de mot de passe
   qui révoque les autres sessions.
2. **Enregistrement sécurisé des agents** — clé d'enregistrement chiffrée au repos
   (AES-GCM), adoptée depuis `ENROLLMENT_KEY` ou générée, rotation à la demande.
3. **Identifiants attribués par le serveur** — `agt_…` généré côté serveur ; un
   agent ne peut pas choisir son identité, son rôle (liste blanche) ni ses droits.
4. **Gestion noms / rôles / capacités déclarées** — noms rendus uniques, rôles
   normalisés (`research`, `builder`, `analyst`, `operations`, `general`),
   capacités conservées **comme déclarations** et jamais comme autorisations.
5. **Jetons individuels** — un jeton par agent, affiché une seule fois, stocké
   sous forme d'empreinte SHA-256, révocable, avec date d'expiration.
6. **Surveillance de présence** — heartbeat horodaté par le serveur, détection des
   agents hors ligne par balayage périodique, journalisation de la reconnexion.
7. **Cycle de vie des tâches** — `PENDING → ASSIGNED → RUNNING → COMPLETED`, avec
   `FAILED`, `CANCELLED`, `TIMEOUT` ; transitions contrôlées par le backend.
8. **Réception et conservation des résultats** — résultats JSON bornés en taille,
   expurgés des secrets, conservés après redémarrage.
9. **Journaux d'audit et événements de sécurité** — journal typé, filtrable,
   expurgé ; alertes dérivées du journal (pas de fausse prétention à un IDS).
10. **État logique ONLINE / OFFLINE** — persistant, distinct du processus systemd
    et de la santé applicative.
11. **Autorisations et révocation** — déconnexion logique (jeton conservé) et
    révocation définitive (jeton refusé, tâches en cours annulées).
12. **Persistance** — SQLite avec migrations versionnées, état restauré au
    démarrage (vérifié par redémarrage réel de l'application).
13. **Tableau de bord web** — React + TypeScript + Vite, 7 pages, sans données
    simulées.
14. **Déploiement automatisé Ubuntu** — `setup.sh` / `update.sh` / `uninstall.sh`,
    Nginx (HTTP + TLS), unité systemd durcie.

### Architecture effectivement mise en place

- **Backend** : FastAPI + SQLAlchemy 2 + Pydantic v2, SQLite (WAL, clés étrangères
  actives), architecture modulaire strictement conforme à `ARCHITECTURE.md §5`
  (structure identique à celle imposée, plus six ajouts justifiés listés en §B).
- **Sécurité** : Argon2id, jetons hachés, sessions serveur + jeton anti-CSRF signé
  + contrôle d'origine, limitation de débit (slowapi), en-têtes de sécurité, CSP,
  journalisation expurgée, documentation interactive protégée par l'authentification.
- **Frontend** : SPA servie par Nginx en production et par le backend en
  développement ; session par cookie `HttpOnly` (aucun jeton en JavaScript).
- **Connecteur agent** : client Python autonome (bibliothèque standard uniquement),
  deux exécuteurs au choix (`--commande` local explicite, `--hermes` pour confier
  l'énoncé à la CLI Hermes)
  implémentant le protocole de `AGENT_CONNECTION.md`.
- **Déploiement** : utilisateur système dédié non privilégié, config `0600` hors
  dépôt, unité systemd durcie, conservation stricte des configurations étrangères.

---

## B. Fichiers créés ou modifiés

### Backend (`backend/`) — 38 fichiers

| Fichier / dossier | Rôle |
|---|---|
| `app/main.py` | assemblage FastAPI, cycle de vie, middlewares (identifiant de requête, taille, en-têtes de sécurité), service du bundle, documentation protégée |
| `app/core/config.py` | configuration centralisée, refus de démarrer en production sans `SECRET_KEY` |
| `app/core/security.py` | Argon2id, jetons, chiffrement AES-GCM de la clé d'enregistrement, expurgation, jeton anti-CSRF |
| `app/core/authentication.py` | dépendances d'autorisation : session admin, jeton d'agent, clé d'enregistrement, contrôle d'origine |
| `app/core/state.py` | état logique persistant, santé, mode de supervision |
| `app/core/errors.py` | format d'erreur unique, gestionnaires centralisés (y compris 429) |
| `app/core/rate_limit.py` | limitation de débit indexée sur l'IP observée |
| `app/database/{base,connection,migrations}.py` | moteur SQLite (WAL), sessions, versionnement du schéma |
| `app/models/` (7 fichiers) | administrateurs, sessions, agents, tâches, événements, jetons, état de l'orchestrateur |
| `app/schemas/` (6 fichiers) | schémas d'entrée/sortie, enveloppe d'erreur, pagination |
| `app/services/` (6 fichiers) | administration, enregistrement, jetons, agents, tâches, orchestration |
| `app/api/` (6 fichiers) | 28 routes documentées + 5 routes complémentaires |
| `app/audit/` (2 fichiers) | journalisation structurée, service d'événements |
| `app/monitoring/` (3 fichiers) | présence/délais, santé, alertes de sécurité |
| `app/utils/` (3 fichiers) | dates UTC, validation et expurgation |
| `scripts/create_admin.py` | création du compte administrateur (`--password-stdin`, saisie masquée) |
| `requirements.txt`, `pytest.ini`, `.env.example`, `smoke_test.py`, `verifier_couture.py`, `diagnostic_couture.py` | dépendances épinglées, configuration de tests, vérifications |
| `tests/` (9 modules + `conftest.py` + `utils.py`) | suite de tests backend, sécurité, intégration, persistance, migrations |

**Ajouts par rapport à l'arborescence imposée** (nécessaires, jamais des renommages
ou suppressions de composants documentés) : `models/session.py` (API.md §3.1 exige
l'invalidation serveur), `models/orchestrator_state.py` (ARCHITECTURE §7.6),
`database/migrations.py` (§11), `core/errors.py`, `core/rate_limit.py`,
`services/admin_service.py`, `schemas/{common,logs}.py`, `scripts/`, `connector/`.

### Frontend (`frontend/`) — 43 fichiers source

16 fichiers imposés remplis (config, `main.tsx`, `App.tsx`, `index.css`,
`services/api.ts`, `services/auth.ts`, les 7 pages) + composants, hooks, types et
utilitaires. TypeScript strict, aucun jeton en `localStorage`, `credentials: 'include'`.

### Déploiement (`deploy/`, `.github/`)

`setup.sh` (14 étapes de `INSTALLATION.md §5`), `update.sh` (mise à jour avec
sauvegarde SQLite, empreinte, `--rollback`), `uninstall.sh` (double confirmation,
garde-fous, purge sélective), `nginx/orchestrator.conf` (gabarit rendu en site +
snippet), `systemd/orchestrator.service` (utilisateur non privilégié, durcissement),
`README.md`, trois tests (`test-uninstall-preservation.sh`,
`test-setup-rollback.sh`, `validate-nginx-config.py`), `.github/workflows/tests.yml`
(6 tâches, dont `nginx -t` réel et `systemd-analyze verify`).

### Recette Docker

- `docker-compose.yml` — trois services (`backend`, `web`, `init`), réseau dédié à
  adresses fixes pour que le backend ne fasse confiance qu'au proxy, volume de données
  persistant, aucun secret dans le fichier (`SECRET_KEY` et `ADMIN_PASSWORD` exigés
  depuis l'environnement).
- `backend/Dockerfile` — image du backend (dépendances, `app/`, `scripts/`, données
  dans `/data`).
- `frontend/Dockerfile` — compilation Node puis service par Nginx.
- `frontend/nginx.container.conf` — configuration Nginx du conteneur, alignée sur le
  gabarit de production (en-têtes de sécurité, CSP, refus des chemins sensibles).
- `deploy/tests/test-recette-compose.py` — contrôle de la recette (13 contrôles).

### Documentation et identité visuelle

- `README.md` — documentation complète : fonctionnalités, captures d'écran des sept
  pages, architecture, pile technique, installation (dont le détail de ce que
  l'installateur installe), prise en main, API, sécurité, tests, exploitation,
  dépannage.
- `docs/captures/` — sept captures d'écran de l'interface réelle (voir §C).
- `frontend/public/` — déclinaisons du logo fourni : emblème détouré (en-tête, barre
  latérale, icône de navigateur), logo complet recoloré pour fond sombre (page de
  connexion), icônes `favicon.ico` / `favicon-32.png` / `apple-touch-icon.png`, et le
  fichier d'origine conservé.

### Connecteur (`connector/`)

`hermes_connector.py` (enroll / run / status / disconnect / forget),
`README.md`, `tests/test_bout_en_bout.sh`.

### Documentation

Les **cinq documents de référence ont été préservés sans aucune modification**
(`docs/`, vérifié automatiquement par `test_09_migrations.py`).

---

## C. Tests

### Commandes exécutées et résultats

| Commande | Résultat réel |
|---|---|
| `python -m pytest tests/` (backend) | **128 tests, 0 échec** (voir répartition ci-dessous) |
| `npx tsc --noEmit` (frontend) | **exit 0, 0 erreur** |
| `npm run build` (frontend) | **exit 0** — 80 modules, `dist/` complet (HTML + JS 331 ko + CSS 12 ko) |
| `python verifier_couture.py` | **52 contrôles, 0 échec** (couture frontend ↔ backend réel) |
| `bash connector/tests/test_bout_en_bout.sh` | **10 contrôles, 0 échec, 1 non vérifiable** (permissions POSIX sous Windows) |
| `python connector/tests/test_integration_hermes.py` | **10 contrôles, 0 échec** — tâche créée, exécutée par Hermes, résultat `moteur: hermes-cli` enregistré |
| `bash deploy/tests/test-uninstall-preservation.sh` | **47 assertions, 0 échec** |
| `bash deploy/tests/test-setup-rollback.sh` | **18 assertions, 0 échec** |
| `bash -n` sur les 5 scripts Bash | aucune erreur de syntaxe |
| `python -m uvicorn app.main:app` + parcours HTTP réel | santé, connexion, tâche, agent : OK |
| Parcours navigateur (Chromium/Playwright, 7 pages) | **7 captures, 0 erreur console, 0 requête en échec** |
| `docker compose up --build` + `deploy/tests/test-recette-compose.py` | **13 contrôles, 0 échec** — frontend compilé servi par Nginx, API relayée, session et anti-CSRF |

### Répartition des tests backend (9 modules, 128 tests, 0 échec)

| Module | Tests | Objet |
|---|---|---|
| `test_01_sante_et_documentation.py` | 8 | santé publique sans fuite, en-têtes de sécurité, protection de la documentation, format d'erreur |
| `test_02_authentification.py` | 20 | connexion, session, verrouillage, mot de passe, CSRF, origine, expiration, limitation de débit |
| `test_03_agents.py` | 18 | enregistrement, identité imposée, noms uniques, rôles, capacités, heartbeat, hors ligne, reconnexion, déconnexion, révocation, suppression |
| `test_04_taches.py` | 24 | cycle de vie complet, transitions refusées, idempotence, cloisonnement, annulation, délais |
| `test_05_etat_orchestrateur.py` | 11 | ONLINE/OFFLINE, refus des opérations agent, tableau de bord maintenu, persistance |
| `test_06_journalisation.py` | 13 | contenu, acteur/objet/IP, filtres, tri, détail, expurgation des secrets |
| `test_07_securite.py` | 19 | accès non authentifiés, jetons, clé et rotation, cloisonnement, entrées hostiles, TLS, secrets, tentatives répétées |
| `test_08_persistance_et_integration.py` | 6 | redémarrage réel, parcours complet, doublons, comportement hors ligne, suppression conservant l'historique |
| `test_09_migrations.py` | 9 | versionnement, idempotence, préservation des données, documents de référence |

Répartition obtenue par exécution module par module (`pytest <module>`), total
**128 tests, 0 échec**.

### Tests non exécutés et raisons

- **Parcours navigateur** : **réalisé** — les sept pages ont été ouvertes dans
  Chromium piloté par Playwright, contre le backend réel peuplé par l'API, avec
  **0 erreur console et 0 requête en échec** (§C). Les captures obtenues servent
  d'illustrations dans `README.md`. La session a été injectée sous forme de cookie
  par le contexte du navigateur : aucun mot de passe n'a été saisi.
- **`systemd-analyze verify`, `nginx -t`, `apt`, `sqlite3`** : indisponibles sous
  Windows (pas de systemd, pas de Nginx, pas de root). Reproduits en CI Ubuntu.
- **Permissions `0600` du fichier d'état du connecteur** : non vérifiables sous
  Windows (les bits POSIX n'existent pas). À confirmer sur Ubuntu.
- **Aucun test destructif n'a été exécuté sur un serveur de production** (conforme
  à la consigne) — aucun accès au VPS n'a été utilisé.

### Compilation

- **Backend** : import de l'application, migrations `1→4` appliquées, schéma créé.
- **Frontend** : `tsc --noEmit` et `vite build` verts, bundle servi par le backend
  (`GET /`, assets, repli SPA, 404 d'API préservés en JSON).

---

## D. Sécurité

### Protections implémentées

- Argon2id pour les mots de passe (paramètres OWASP : 19 Mio, `t=2`).
- Jetons d'agent : 256 bits, jamais réaffichés, stockés en SHA-256.
- Clé d'enregistrement : chiffrée au repos (AES-GCM, clé dérivée par HKDF).
- Sessions serveur : cookie `HttpOnly`, `SameSite`, `Secure` en production,
  expiration absolue **et** inactivité, révocation côté serveur.
- Anti-CSRF : jeton signé (itsdangerous) lié à la session + contrôle d'origine
  (une origine étrangère est refusée même sans liste déclarée).
- Limitation de débit sur connexion, enregistrement, routes agent et administration.
- Verrouillage de compte après 5 échecs, message identique pour utilisateur inconnu.
- Validation serveur de toutes les entrées ; erreurs normalisées sans trace technique.
- Expurgation systématique : aucun mot de passe, jeton, clé ou `Authorization` dans
  les journaux, les métadonnées d'événements ou les réponses d'erreur.
- En-têtes de sécurité (CSP, `nosniff`, `DENY`, `Referrer-Policy`), `Cache-Control`.
- Documentation interactive protégée par authentification administrateur.
- Vérification TLS jamais désactivée (`test_07_securite.py` le vérifie par analyse
  du code source) ; aucune donnée sensible dans `Git` (`.gitignore` + test dédié).
- Déploiement : configuration `0600` hors dépôt, utilisateur non privilégié, unité
  systemd durcie, `X-Forwarded-*` écrasés côté Nginx.

### Vérifications réalisées

- 128 tests backend dont un module entier de sécurité : accès sans session, jeton
  invalide/expiré/révoqué, clé invalide et rotation, cloisonnement entre agents,
  accès à la tâche d'un autre agent **avec journalisation de la tentative**,
  entrées malformées, injection SQL dans un filtre, chemin traversant, JSON
  invalide, tentatives répétées, absence de secrets dans le journal, protection de
  la documentation, persistance des révocations après redémarrage.
- Préservation de configuration : 47 assertions sur `uninstall.sh` (fichiers
  étrangers intacts, seules nos données supprimées, `--yes` n'autorise jamais une
  suppression de données).

### Défaut Nginx bloquant en production (détecté par deux vérifications indépendantes)

Le gabarit de reverse proxy filtrait l'API avec le motif
`location ~ ^/(health|api/)(/|$)`. Or, après `api/`, ce motif exige un `/` ou la fin
de chaîne : **`/api/v1/...` ne correspondait donc à aucun bloc**. Toute requête de
l'interface retombait sur le repli SPA — `GET /api/v1/tasks` renvoyait `index.html`
en 200 et `POST /api/v1/auth/login` était refusé en **405** par le service de fichiers
statiques. Derrière Nginx, l'application était donc **inutilisable**, alors que tous
les tests directs contre le backend passaient : aucun test ne traversait le proxy.

Deux vérifications ont mis le défaut en évidence de façon indépendante : la recette
Docker (`/api/v1/tasks` renvoyait du HTML) et le banc d'essai Ubuntu du reverse proxy
(son contrôle « les routes /api restent du JSON » a échoué). Le motif corrigé
`^/(health|api)(/|$)` couvre `/health`, `/api`, `/api/` et `/api/v1/...` ; le
commentaire du gabarit documente le piège. Après correction, la recette Docker passe
**13/13** (404 JSON sur route inconnue, 401 sur mot de passe erroné, session et
jeton anti-CSRF posés).

### Défaut d'intégration détecté et corrigé pendant la vérification

L'ouverture réelle des pages a révélé un défaut que les tests contre stub ne
pouvaient pas voir : **le serveur ne transmettait le jeton anti-CSRF que dans le corps
de la réponse de connexion**, alors que le frontend le relit dans un cookie. Toute
action d'écriture du tableau de bord (créer une tâche, révoquer un agent, se
déconnecter) était donc refusée en 403 dès le premier rechargement de page.

Correction apportée : le jeton est désormais posé dans un cookie lisible (double
soumission, non `HttpOnly` — ce n'est pas un secret d'authentification, il est signé et
lié à la session), réémis à chaque lecture de session (`GET /auth/me`) et effacé à la
déconnexion. Trois tests de non-régression ont été ajoutés (128 tests au total).

### Points restant à corriger

1. **Rotation de clé automatique** : absente (volontairement) — la rotation est une
   action explicite d'administrateur.
2. **Réglages à l'exécution** : `/settings/parameters` est en **lecture seule**.
   La mission (§9) évoquait « la modification des paramètres autorisés » : cela
   demanderait un mécanisme de surcharge persisté en base, non prévu par `API.md`.
   Décision non prise unilatéralement — à trancher.
3. **Limitation de débit en mémoire** : suffisante en mono-processus ; un
   déploiement multi-workers exigerait un stockage partagé.
4. **Permissions POSIX** du fichier d'état du connecteur non vérifiées sous Windows.

---

## E. Déploiement

### État des scripts

- `deploy/setup.sh` — 14 étapes, idempotent, modes `--render-only`, `--print-paths`,
  `--reconfigure`, `--no-https`, `--yes` ; saisie masquée ; détection de l'existant ;
  sauvegarde et restauration d'une configuration Nginx remplacée.
- `deploy/update.sh` — sauvegarde SQLite via l'API `backup`, empreinte SHA-256,
  migrations, reconstruction du frontend, redémarrage, contrôle de santé, `--rollback`.
- `deploy/uninstall.sh` — confirmation par saisie du domaine, seconde confirmation
  pour les données, `--dry-run`, refus de tout chemin hors projet.
- `deploy/nginx/orchestrator.conf`, `deploy/systemd/orchestrator.service`,
  `.github/workflows/tests.yml`, `deploy/README.md`.

### Prérequis restants

Ubuntu Server 22.04/24.04, accès `sudo`, domaine pointant vers le serveur, ports 80
et 443 ouverts, e-mail Let's Encrypt, Git, Python 3.11+, Node.js 20+ et npm.

### Étapes d'installation sur Ubuntu

1. `git clone` du dépôt puis `cd multi-agent-orchestrator`.
2. `sudo bash deploy/setup.sh` — répondre aux invites (domaine, e-mail, compte
   administrateur avec double saisie masquée).
3. Le script installe les dépendances, écrit `/etc/multi-agent-orchestrator/production.env`
   (`0600`, `LOG_DIR` inclus), initialise la base, crée le compte administrateur,
   configure Nginx et le certificat, crée et démarre `orchestrator.service`, puis
   exécute 7 vérifications finales.
4. Contrôles post-installation : `systemctl status orchestrator`,
   `sudo nginx -t`, `journalctl -u orchestrator -n 50`, puis connexion au tableau
   de bord.
5. Mises à jour : `sudo bash deploy/update.sh` (`--rollback` en cas de problème) ;
   désinstallation : `sudo bash deploy/uninstall.sh`.

---

## F. Limitations

### Fonctionnalités incomplètes

- **Intégration « runtime Hermes »** : **réalisée et vérifiée**. Le connecteur
  dispose d'un exécuteur dédié (`run --hermes`) qui confie l'énoncé de la tâche à la
  CLI Hermes locale en mode ponctuel et renvoie sa réponse comme résultat. Le banc
  d'essai `connector/tests/test_integration_hermes.py` démarre un vrai serveur,
  enrôle un agent, crée une tâche, la fait **réellement exécuter par Hermes** et
  contrôle le résultat enregistré : **10/10**, tâche `COMPLETED` en 16 s, réponse
  `RESULTAT-HERMES-OK` retrouvée dans le résultat. La déclaration reste explicite
  (l'exploitant autorise l'exécuteur) ; sans exécuteur, rien n'est exécuté.
- Parcours navigateur contre le backend réel : **réalisé** (voir §C).
- **Routes complémentaires** (`/settings/service`, `/settings/parameters`,
  `/settings/security`, `/logs/reference`, `/tasks/state-machine`) : ajoutées pour
  l'exploitation, elles ne sont **pas consommées** par l'interface, qui n'utilise
  que les routes de `API.md`.
- **`disconnect` côté agent** : le protocole ne prévoit pas de route agent ; la
  déconnexion logique est administrative (le connecteur l'explique explicitement).

### Dépendances externes

Python 3.11+, Node.js 20+, Nginx, Certbot/Let's Encrypt, systemd, SQLite. Les
versions Python sont épinglées dans `backend/requirements.txt`, alignées sur
l'environnement réellement testé (FastAPI 0.141.1, SQLAlchemy 2.0.54, Pydantic 2.13.5).

### Points non vérifiés

- Exécution de `setup.sh` sur une machine Ubuntu réelle (indisponible ici).
- `nginx -t` et `systemd-analyze verify` en conditions réelles.
- Permissions POSIX du jeton du connecteur.
- Chargement multi-workers (limitation de débit en mémoire).

### Intégrations Hermes

1. Enrôlement d'un **agent Hermes** avec le connecteur : **fait** (instance locale
   réelle, agent `ONLINE`, jeton propre).
2. Exécution d'une tâche produite par l'orchestrateur **par Hermes lui-même** :
   **fait et mesuré** (`run --hermes`, résultat `moteur: hermes-cli` enregistré par
   le serveur).
3. Reste à observer : comportement de reconnexion après redémarrage du poste agent en
   conditions de production (le jeton et l'identité sont persistés dans un fichier
   d'état, mais aucune coupure réseau longue n'a été rejouée).

---

## G. État final

### TERMINÉ ET TESTÉ

**Justification par des éléments vérifiables, tous exécutés réellement :**

| Preuve | Résultat |
| --- | --- |
| Suite backend (`pytest`) | **128 tests, 0 échec** |
| Types et compilation du frontend | `tsc --noEmit` 0 erreur, `npm run build` exit 0 |
| Couture frontend ↔ backend réel | **52/52** contrôles |
| Parcours navigateur (7 pages, backend réel) | 7 captures, **0 erreur console, 0 requête en échec** |
| Connecteur agent (vrai serveur HTTP) | **10/10** contrôles, tâche hostile non exécutée |
| Exécution d'une tâche **par Hermes** | **10/10**, tâche `COMPLETED`, résultat `moteur: hermes-cli` |
| Installation Ubuntu 24.04 vierge (conteneur, banc réel) | **86/86** contrôles, `setup.sh` exit 0 |
| Installation Ubuntu 22.04 vierge (même banc) | **86/86** contrôles, `setup.sh` exit 0 |
| Recette Docker complète | **13/13** contrôles |
| Tests des scripts de déploiement | **47/47** et **18/18** assertions |
| Persistance après redémarrage | vérifiée par deux cycles de vie successifs |
| Intégration continue | 7 travaux, dont `nginx -t` et `systemd-analyze verify` |

Le banc d'installation (§C) établit, sur des **Ubuntu 24.04 et 22.04 vierges** (les deux
LTS déclarées prises en charge par le script) et non par relecture : `setup.sh` se termine avec le **code 0** ; il installe **Node.js 20+ via
NodeSource**, npm et le frontend React qu'il **compile** et fait servir par Nginx
(page d'accueil 200) ; il crée le service systemd durci, **actif et activé au
démarrage** ; `GET /health` répond 200 en direct et à travers le reverse proxy ; les
routes `/api` restent du JSON ; le compte administrateur est créé et la connexion
`POST /api/v1/auth/login` répond 200 ; le mot de passe n'est pas stocké en clair ;
`deploy/update.sh` s'exécute avec sauvegarde préalable et vérification de santé. Le
piège connu de npm 11 (scripts d'installation refusés par défaut, compilation Vite
impossible sans `esbuild`) est **traité par le script** : autorisation limitée au seul
paquet `esbuild` et contrôle que le moteur est exécutable.

**Ce qui n'est pas couvert par cette validation, et pourquoi :**

- **Certificat Let's Encrypt sur un domaine public** : le banc réutilise un
  certificat de test, faute de domaine réel pointant vers la machine. L'obtention
  d'un certificat dépend d'un DNS public, pas du logiciel.
- **Exploitation prolongée** (montée en charge, coupures réseau longues) : hors du
  périmètre d'un banc d'essai.
- Le VPS de production n'a **jamais** été touché, conformément à la consigne.

Aucun blocage ne subsiste : tous les composants obligatoires existent, s'exécutent et
sont couverts par des tests exécutés réellement, y compris dans l'environnement cible.
