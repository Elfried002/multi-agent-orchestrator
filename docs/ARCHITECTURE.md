
# ARCHITECTURE.md
# Multi-Agent Orchestrator — Architecture technique

**Version :** 1.0  
**Statut :** Spécification de référence  
**Type :** Architecture logicielle et infrastructure  
**Environnement cible :** Ubuntu Server  
**Environnement de développement :** Windows + VS Code

---

## 1. Présentation du projet

Le Multi-Agent Orchestrator est une plateforme centralisée permettant d'enregistrer, d'authentifier, de superviser et d'orchestrer plusieurs agents d'intelligence artificielle.

La plateforme doit permettre à un administrateur de :

- Visualiser les agents connectés et leur état.
- Enregistrer de nouveaux agents.
- Attribuer des identifiants et des rôles aux agents.
- Superviser leurs activités et leurs tâches.
- Envoyer des tâches aux agents.
- Consulter les journaux d'activité et les événements de sécurité.
- Gérer les autorisations et les jetons d'accès.
- Activer ou désactiver le fonctionnement logique de l'orchestrateur.
- Surveiller l'état du service et son historique.

Le système doit fonctionner de manière persistante sur un serveur Ubuntu, indépendamment de la session SSH de l'administrateur.

---

## 2. Objectifs fonctionnels

### 2.1 Gestion des agents

Le système doit permettre :

1. L'enregistrement sécurisé d'un nouvel agent.
2. La réception de ses informations d'identification et de ses capacités déclarées.
3. L'attribution d'un identifiant unique par le serveur.
4. L'attribution d'un nom et d'un rôle selon les règles de gestion définies.
5. La génération d'un jeton d'accès individuel.
6. La réception périodique de signaux de présence (heartbeats).
7. La détection de la déconnexion d'un agent.
8. La révocation de ses autorisations.
9. La consultation de son historique d'activité.

Un agent ne doit jamais pouvoir choisir librement l'identifiant interne qui lui sera attribué par le serveur.

### 2.2 Gestion des tâches

Le système doit permettre :

- La création d'une tâche par l'administrateur.
- La sélection d'un agent ou d'un ensemble d'agents autorisés.
- La transmission de la tâche à un agent compatible.
- Le suivi de son état.
- L'enregistrement des résultats.
- La gestion des erreurs et des délais d'expiration.
- La conservation de l'historique des tâches.

### 2.3 Supervision

Le tableau de bord doit afficher :

- L'état global de l'orchestrateur.
- Le nombre d'agents enregistrés.
- Le nombre d'agents en ligne et hors ligne.
- Les tâches en attente, en cours, terminées ou en échec.
- Les événements récents.
- Les journaux d'audit.
- Les alertes de sécurité.
- L'état du service et sa durée de fonctionnement.

---

## 3. Architecture générale

Le système est organisé en trois couches principales.

### 3.1 Backend

Le backend constitue le cœur de l'orchestrateur.

Technologies :

- Python.
- FastAPI.
- Uvicorn.
- SQLite.
- SQLAlchemy ou une couche d'accès aux données équivalente.

Responsabilités :

- Authentification des administrateurs et des agents.
- Gestion des sessions et des jetons.
- Enregistrement et supervision des agents.
- Gestion des tâches.
- Exécution des règles d'orchestration.
- Persistance des données.
- Journalisation et audit.
- Surveillance de l'état du service.
- Exposition de l'API REST.

### 3.2 Frontend

Le frontend constitue l'interface d'administration.

Technologies :

- React.
- TypeScript.
- Vite.
- CSS et composants d'interface adaptés.

Responsabilités :

- Authentification de l'administrateur.
- Affichage du tableau de bord.
- Gestion des agents.
- Gestion des tâches.
- Consultation des journaux.
- Gestion des paramètres et de la sécurité.
- Contrôle de l'état logique de l'orchestrateur.

Le frontend ne doit jamais être considéré comme une frontière de sécurité. Toutes les autorisations doivent être vérifiées par le backend.

### 3.3 Infrastructure

L'infrastructure de production comprend :

- Ubuntu Server.
- Nginx comme reverse proxy.
- HTTPS avec Certbot et Let's Encrypt.
- systemd pour la gestion du service.
- Git pour le déploiement du code.
- Une base de données SQLite persistante.

---

## 4. Architecture logique

```text
                     ADMINISTRATEUR
                           |
                           v
                 NAVIGATEUR WEB
                           |
                           v
                 FRONTEND REACT
                           |
                           v
                     HTTPS / TLS
                           |
                           v
                        NGINX
                           |
                           v
                  BACKEND FASTAPI
                           |
            +--------------+--------------+
            |              |              |
            v              v              v
       AUTHENTIFICATION  ORCHESTRATION  SUPERVISION
            |              |              |
            +--------------+--------------+
                           |
                           v
                     BASE SQLITE
                           |
             +-------------+-------------+
             |             |             |
             v             v             v
          AGENT A       AGENT B       AGENT C
          HERMES         HERMES         HERMES
```

Les agents communiquent avec le backend via HTTPS. Ils ne doivent jamais accéder directement à la base de données.

---

## 5. Structure du backend

Le backend est organisé selon les responsabilités suivantes.

### `api/`

Contient les routes HTTP de l'application.

- `auth.py` : authentification et gestion des sessions administrateur.
- `agents.py` : enregistrement, consultation et gestion des agents.
- `tasks.py` : création et suivi des tâches.
- `logs.py` : consultation des journaux.
- `settings.py` : paramètres de l'orchestrateur.
- `health.py` : vérification de santé du service.

### `core/`

Contient les composants transversaux.

- `config.py` : configuration de l'application.
- `security.py` : fonctions cryptographiques et protections.
- `authentication.py` : mécanismes d'authentification et d'autorisation.
- `state.py` : état logique persistant de l'orchestrateur.

### `models/`

Contient les modèles de données persistants.

- `admin.py`
- `agent.py`
- `task.py`
- `event.py`
- `token.py`

### `schemas/`

Contient les schémas de validation des entrées et sorties API.

### `services/`

Contient la logique métier.

- `agent_service.py`
- `task_service.py`
- `enrollment_service.py`
- `orchestrator_service.py`
- `token_service.py`

### `database/`

Contient la configuration de la base de données, les modèles de base et les migrations.

### `monitoring/`

Contient la surveillance de présence des agents, les contrôles de santé et la gestion des alertes.

### `audit/`

Contient la journalisation d'audit et la gestion des événements.

### `utils/`

Contient les fonctions utilitaires partagées.

---

## 6. Structure du frontend

Le frontend comprend les pages suivantes :

| Page | Fonction |
|---|---|
| `Login.tsx` | Connexion administrateur |
| `Dashboard.tsx` | Vue globale de l'orchestrateur |
| `Agents.tsx` | Gestion et supervision des agents |
| `Tasks.tsx` | Gestion des tâches |
| `Logs.tsx` | Consultation des journaux |
| `Security.tsx` | Sécurité et événements sensibles |
| `Settings.tsx` | Paramètres et configuration |

Les composants sont regroupés par domaine fonctionnel : layout, dashboard, agents, tasks, logs et ui.

Les appels HTTP sont centralisés dans `services/api.ts`. La gestion de l'authentification est centralisée dans `services/auth.ts`.

---

## 7. Modèle de données

La base de données doit conserver les informations nécessaires au fonctionnement du système.

### 7.1 Administrateurs

Champs minimaux :

- `id` : identifiant unique.
- `username` : nom d'utilisateur unique.
- `password_hash` : empreinte sécurisée du mot de passe.
- `is_active` : compte actif ou désactivé.
- `created_at` : date de création.
- `last_login_at` : dernière connexion.

Le mot de passe en clair ne doit jamais être stocké.

### 7.2 Agents

Champs minimaux :

- `id` : identifiant unique attribué par le serveur.
- `name` : nom attribué à l'agent.
- `role` : rôle attribué.
- `runtime` : type de runtime, par exemple Hermes.
- `capabilities` : capacités déclarées.
- `status` : état de connexion.
- `source_ip` : dernière adresse IP observée.
- `created_at` : date d'enregistrement.
- `last_seen_at` : dernier signal de présence.
- `credential_id` : référence du jeton d'accès.
- `metadata` : informations complémentaires validées.

Les capacités déclarées par un agent ne constituent pas une preuve de confiance.

### 7.3 Tâches

Champs minimaux :

- `id` : identifiant unique.
- `title` : titre.
- `description` : description.
- `status` : état de la tâche.
- `priority` : priorité.
- `assigned_agent_id` : agent assigné, si applicable.
- `created_by` : administrateur à l'origine de la tâche.
- `created_at` : date de création.
- `started_at` : date de démarrage.
- `completed_at` : date de fin.
- `result` : résultat.
- `error_message` : erreur éventuelle.

### 7.4 Événements

Champs minimaux :

- `id` : identifiant unique.
- `event_type` : type d'événement.
- `severity` : niveau de gravité.
- `message` : description.
- `agent_id` : agent concerné, si applicable.
- `actor_type` : administrateur, agent ou système.
- `actor_id` : identifiant de l'acteur.
- `source_ip` : adresse IP observée, si disponible.
- `created_at` : date de l'événement.
- `metadata` : données complémentaires expurgées des secrets.

### 7.5 Jetons

Champs minimaux :

- `id` : identifiant interne.
- `token_hash` : empreinte du jeton.
- `token_type` : type de jeton.
- `agent_id` : agent associé, si applicable.
- `created_at` : date de création.
- `expires_at` : date d'expiration, si applicable.
- `revoked_at` : date de révocation, si applicable.
- `last_used_at` : dernière utilisation.

Les jetons ne doivent pas être enregistrés en clair dans les journaux.

### 7.6 État de l'orchestrateur

L'état logique doit être persistant.

Valeurs :

- `ONLINE`
- `OFFLINE`

Cet état doit survivre aux redémarrages du serveur.

---

## 8. Gestion de l'état de l'orchestrateur

L'orchestrateur doit fonctionner comme un service persistant.

Il faut distinguer :

1. L'état du processus système.
2. L'état logique de l'orchestrateur.
3. L'état de santé du backend.

Le processus système reste actif pour permettre à l'administrateur de consulter le tableau de bord et de modifier l'état logique.

### ONLINE

- Les agents autorisés peuvent s'enregistrer et communiquer.
- Les heartbeats sont traités.
- Les tâches peuvent être distribuées.
- Les opérations autorisées sont exécutées.

### OFFLINE

- Les nouveaux enregistrements d'agents sont refusés.
- Les tâches ne sont pas distribuées.
- Les opérations métier des agents sont refusées.
- Les agents peuvent être informés que l'orchestrateur est hors ligne.
- Le tableau de bord reste accessible à l'administrateur.
- Les opérations d'administration autorisées restent disponibles.

L'état doit être enregistré dans la base de données.

### Redémarrage

Au démarrage, le service doit restaurer l'état logique persistant.

- Si l'état enregistré est `ONLINE`, l'orchestrateur reprend son fonctionnement.
- Si l'état enregistré est `OFFLINE`, il reste logiquement hors ligne.

Le comportement de reprise des tâches interrompues doit être défini et testé afin d'éviter les exécutions en double.

---

## 9. Gestion de la présence des agents

Les agents envoient périodiquement un heartbeat authentifié.

Chaque heartbeat actualise `last_seen_at`.

Un mécanisme de surveillance compare le dernier signal reçu à un seuil de délai configurable.

États possibles :

- `PENDING` : agent enregistré, mais connexion initiale non confirmée.
- `ONLINE` : agent actif et récemment joignable.
- `OFFLINE` : aucun heartbeat reçu dans le délai autorisé.
- `REVOKED` : autorisation révoquée.

La détection d'un agent hors ligne doit être persistée et générer un événement d'audit.

---

## 10. Gestion des tâches

Cycle de vie minimal :

```text
PENDING
   |
   v
ASSIGNED
   |
   v
RUNNING
   |
   +--------> COMPLETED
   |
   +--------> FAILED
   |
   +--------> CANCELLED
   |
   +--------> TIMEOUT
```

Chaque transition doit être validée par le backend.

Une tâche ne doit pas être considérée comme terminée sans résultat de confirmation valide ou règle explicite de clôture.

Les tâches doivent être persistantes et consultables après redémarrage.

---

## 11. Sécurité de l'architecture

Le système doit appliquer les principes suivants :

- Authentification obligatoire pour toutes les opérations sensibles.
- Autorisation vérifiée côté backend.
- Jetons individuels pour les agents.
- Stockage sécurisé des mots de passe.
- Stockage sécurisé des secrets.
- Communications HTTPS en production.
- Journalisation des opérations sensibles.
- Protection contre les tentatives d'authentification répétées.
- Validation stricte des données entrantes.
- Limitation des privilèges.
- Révocation immédiate des jetons compromis.
- Absence de secrets dans les journaux et le dépôt Git.

---

## 12. Architecture de production

### Nginx

Nginx reçoit les connexions HTTPS et transmet les requêtes au backend.

Le serveur doit conserver les configurations Nginx préexistantes qui ne concernent pas l'orchestrateur.

### Certbot

Certbot gère les certificats TLS.

L'installation doit vérifier les certificats existants avant toute création ou modification.

### systemd

Le service systemd assure :

- Le démarrage au lancement du système.
- Le redémarrage du processus en cas de panne.
- La gestion des journaux système.
- L'exécution sous un utilisateur dédié non privilégié.

L'état logique `ONLINE` ou `OFFLINE` reste géré par l'application et ne doit pas être confondu avec l'état systemd.

### SQLite

La base SQLite doit être conservée dans un répertoire de données persistant, indépendant du répertoire du code.

Les sauvegardes doivent être cohérentes et restaurables.

---

## 13. Structure du dépôt

```text
multi-agent-orchestrator/
├── backend/
├── frontend/
├── deploy/
├── docs/
├── .github/
├── .gitignore
├── README.md
├── LICENSE
└── docker-compose.yml
```

L'architecture détaillée du dépôt est celle validée dans la structure du projet.

Aucun composant majeur ne doit être supprimé ou remplacé sans validation préalable.

---

## 14. Exigences de qualité

Le code doit être :

- Modulaire.
- Typé lorsque possible.
- Documenté.
- Testable.
- Sécurisé par défaut.
- Facile à maintenir.
- Compatible avec Windows pour le développement.
- Déployable sur Ubuntu Server.

Les erreurs doivent être explicites et journalisées sans divulgation de secrets.

---

## 15. Critères d'acceptation

L'architecture est considérée comme correctement implémentée si :

1. Le backend démarre sans erreur.
2. Les données persistent après redémarrage.
3. Un administrateur peut se connecter.
4. Un agent autorisé peut s'enregistrer.
5. Un agent reçoit une identité attribuée par le serveur.
6. Les heartbeats actualisent son état.
7. Les tâches peuvent être créées et suivies.
8. L'état ONLINE/OFFLINE est persistant.
9. Les agents révoqués ne peuvent plus utiliser leur ancien jeton.
10. Les événements sensibles sont journalisés.
11. Le frontend communique avec le backend.
12. Le déploiement respecte la configuration existante du serveur.

---

## 16. Règle de référence

Ce document constitue la référence architecturale du projet.

Toute implémentation doit respecter les composants, les responsabilités et les règles décrits ici.

En cas de contradiction entre ce document et une décision de développement, le Builder Agent doit signaler la contradiction et demander une validation avant de modifier l'architecture.