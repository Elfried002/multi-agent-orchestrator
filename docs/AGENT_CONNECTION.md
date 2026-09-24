
# AGENT_CONNECTION.md
# Multi-Agent Orchestrator — Protocole de connexion des agents

**Version :** 1.0  
**Statut :** Spécification de référence  
**Runtime cible :** Hermes Agent  
**Protocole :** HTTPS + API JSON

---

## 1. Objectif

Ce document définit le protocole de connexion entre un agent IA et le Multi-Agent Orchestrator.

Il décrit :

- La configuration d'un agent.
- L'enregistrement initial.
- L'attribution de l'identité par le serveur.
- L'authentification.
- L'envoi des heartbeats.
- La récupération des tâches.
- La transmission des résultats.
- La déconnexion.
- La révocation.
- La reconnexion après interruption.

Le protocole doit être implémenté par un connecteur compatible avec le runtime de l'agent.

Un prompt seul ne constitue pas une intégration réseau. Le runtime Hermes doit disposer d'un connecteur, d'une extension ou d'un mécanisme d'intégration capable d'exécuter les échanges décrits ici.

---

## 2. Architecture de connexion

```text
+--------------------------+
|     AGENT HERMES         |
|                          |
|  Runtime IA              |
|  Connecteur orchestrateur|
|  Identité persistante    |
|  Jeton individuel        |
+-------------+------------+
              |
              | HTTPS
              |
              v
+--------------------------+
|     NGINX / TLS          |
+-------------+------------+
              |
              v
+--------------------------+
|  BACKEND FASTAPI         |
|                          |
|  Authentification        |
|  Enregistrement          |
|  Gestion des agents      |
|  Gestion des tâches      |
|  Supervision             |
+-------------+------------+
              |
              v
+--------------------------+
|      BASE SQLITE         |
+--------------------------+
```

---

## 3. Informations de connexion

Chaque agent doit disposer des informations suivantes :

| Élément | Description |
|---|---|
| `ORCHESTRATOR_URL` | URL publique du backend |
| `ENROLLMENT_KEY` | Clé utilisée pour l'enregistrement initial |
| `AGENT_ID` | Identifiant attribué par le serveur |
| `AGENT_TOKEN` | Jeton individuel délivré après enregistrement |
| `CLIENT_INSTANCE_ID` | Identifiant local stable du connecteur |
| `AGENT_NAME` | Nom attribué par le serveur |
| `AGENT_ROLE` | Rôle attribué par le serveur |

L'URL identifie le point d'accès du service. Elle n'est pas un secret.

La clé d'enregistrement et le jeton individuel sont des secrets et doivent être protégés.

L'identifiant interne de l'agent doit être attribué par le serveur.

---

## 4. Configuration initiale

L'administrateur récupère depuis le tableau de bord :

1. L'URL de l'orchestrateur.
2. La clé d'enregistrement.

Ces informations sont transmises au connecteur de l'agent par un canal sécurisé.

Le connecteur doit créer un identifiant local stable lors de sa première installation.

Exemple de configuration logique :

```env
ORCHESTRATOR_URL=https://api.example.com
ENROLLMENT_KEY=<SECRET>
CLIENT_INSTANCE_ID=<UUID_STABLE>
```

Le fichier de configuration contenant les secrets doit être protégé.

Il ne doit pas être ajouté au dépôt Git.

---

## 5. Étape 1 — Enregistrement initial

Le connecteur transmet une demande d'enregistrement.

Route :

`POST /api/v1/agents/enroll`

En-tête :

```http
Authorization: Bearer <ENROLLMENT_KEY>
Content-Type: application/json
```

Corps :

```json
{
  "runtime": "hermes",
  "client_instance_id": "instance_uuid",
  "requested_name": "Research Agent",
  "declared_role": "research",
  "capabilities": [
    "research",
    "summarization"
  ],
  "version": "1.0.0"
}
```

Le serveur doit :

- Vérifier la clé d'enregistrement.
- Valider les informations reçues.
- Déterminer les autorisations applicables.
- Attribuer un identifiant unique.
- Définir le nom et le rôle de l'agent.
- Créer un jeton individuel.
- Enregistrer l'agent.
- Journaliser l'événement.

Les capacités déclarées sont des informations fournies par l'agent. Elles ne doivent pas être considérées comme une preuve indépendante de ses compétences.

---

## 6. Étape 2 — Réception de l'identité

Le serveur retourne :

```json
{
  "agent_id": "agent_uuid",
  "name": "Research Agent",
  "role": "research",
  "status": "PENDING",
  "access_token": "agent_token",
  "token_type": "Bearer"
}
```

Le connecteur doit conserver de manière sécurisée :

- L'identifiant attribué par le serveur.
- Le nom attribué.
- Le rôle attribué.
- Le jeton individuel.

Le jeton individuel ne doit être accessible qu'au connecteur autorisé.

Le serveur ne doit pas transmettre le jeton dans les journaux.

---

## 7. Étape 3 — Authentification

Après l'enregistrement, toutes les requêtes agent utilisent le jeton individuel.

En-tête :

```http
Authorization: Bearer <AGENT_TOKEN>
```

Le connecteur doit refuser de poursuivre les opérations si le jeton est absent ou invalide.

Le serveur doit vérifier le jeton à chaque requête.

---

## 8. Étape 4 — Confirmation de présence

Le connecteur envoie un heartbeat périodique.

Route :

`POST /api/v1/agents/heartbeat`

Corps :

```json
{
  "status": "ONLINE",
  "runtime_status": "ready",
  "timestamp": "2026-09-24T12:00:00Z"
}
```

Le serveur actualise le dernier contact et l'état de l'agent.

La fréquence des heartbeats doit être configurable.

Le connecteur doit gérer les erreurs réseau temporaires avec une stratégie de nouvelle tentative progressive.

---

## 9. Étape 5 — Récupération des tâches

Le connecteur demande les tâches qui lui sont attribuées.

Route :

`GET /api/v1/agents/tasks`

Le serveur ne doit retourner que les tâches autorisées pour cet agent.

Lorsqu'une tâche est récupérée, le connecteur doit confirmer sa prise en charge.

Route :

`POST /api/v1/agents/tasks/{task_id}/ack`

Une même tâche ne doit pas être exécutée plusieurs fois par erreur à cause d'une simple répétition de requête.

Le système doit prévoir une stratégie d'idempotence et de récupération après interruption.

---

## 10. Étape 6 — Exécution d'une tâche

Le connecteur transmet la tâche au mécanisme d'exécution approprié du runtime Hermes.

Le connecteur doit :

1. Vérifier que la tâche est autorisée.
2. Vérifier que la tâche est compatible avec les capacités disponibles.
3. Signaler le démarrage de l'exécution.
4. Exécuter la tâche dans le cadre des permissions autorisées.
5. Collecter le résultat.
6. Transmettre le résultat au serveur.

L'exécution d'une tâche ne doit pas accorder automatiquement à l'agent un accès au système hôte, aux secrets ou à des ressources non autorisées.

---

## 11. Étape 7 — Transmission du résultat

Route :

`POST /api/v1/agents/tasks/{task_id}/result`

Exemple :

```json
{
  "status": "COMPLETED",
  "result": {
    "summary": "Résultat de la tâche.",
    "details": "Informations complémentaires."
  }
}
```

Le serveur doit :

- Vérifier l'identité de l'agent.
- Vérifier l'attribution de la tâche.
- Valider le format du résultat.
- Enregistrer le résultat.
- Mettre à jour l'état de la tâche.
- Générer un événement d'audit.

Les résultats reçus doivent être considérés comme des données non fiables.

---

## 12. Déconnexion

Une déconnexion normale doit permettre au connecteur de signaler la fin de sa session.

Le serveur conserve l'identité et l'historique de l'agent.

Une absence de heartbeat doit entraîner le passage de l'agent à l'état `OFFLINE` après expiration du délai configuré.

Une déconnexion ne révoque pas automatiquement le jeton individuel.

---

## 13. Révocation

Lorsqu'un administrateur révoque un agent :

- Le serveur invalide son jeton.
- Les nouvelles requêtes authentifiées sont refusées.
- Les tâches en cours sont traitées selon la politique de révocation.
- Un événement de sécurité est enregistré.

Le connecteur doit cesser ses tentatives de connexion lorsque le serveur indique que ses autorisations sont révoquées.

Un nouvel enregistrement est nécessaire pour rétablir l'accès.

---

## 14. Reconnexion après interruption

En cas de perte de connexion :

1. Le connecteur détecte l'échec.
2. Il conserve son identité locale.
3. Il tente une reconnexion avec une stratégie de délai progressif.
4. Il réutilise son jeton individuel tant qu'il reste valide.
5. Il vérifie son identité auprès du serveur.
6. Il reprend les opérations autorisées.

Le connecteur ne doit pas créer un nouvel agent à chaque redémarrage.

Le serveur doit empêcher la création incontrôlée de doublons pour une même identité locale.

---

## 15. Comportement lorsque l'orchestrateur est OFFLINE

Lorsque l'état logique est `OFFLINE` :

- Le serveur refuse les opérations métier des agents.
- Les nouvelles demandes d'enregistrement sont refusées.
- Les tâches ne sont pas distribuées.
- Le tableau de bord administrateur reste accessible.
- Le connecteur peut réessayer ultérieurement selon une stratégie contrôlée.

L'agent doit distinguer :

- Une indisponibilité réseau.
- Un état logique `OFFLINE`.
- Un jeton invalide.
- Une autorisation révoquée.

Il ne doit pas tenter de contourner un refus du serveur.

---

## 16. Gestion des erreurs

Le connecteur doit traiter les erreurs suivantes :

| Situation | Comportement |
|---|---|
| Erreur réseau temporaire | Nouvelle tentative progressive |
| HTTP 401 | Vérifier les informations d'authentification |
| HTTP 403 | Arrêter l'opération non autorisée |
| HTTP 404 | Vérifier l'identifiant de ressource |
| HTTP 409 | Réconcilier l'état de la tâche |
| HTTP 429 | Respecter le délai de reprise |
| HTTP 500 | Nouvelle tentative contrôlée |
| HTTP 503 | Attendre le rétablissement du service |

Les erreurs ne doivent pas entraîner une boucle de requêtes agressive.

---

## 17. Sécurité du connecteur

Le connecteur doit :

- Protéger le jeton individuel.
- Ne jamais afficher les secrets dans les logs.
- Vérifier le certificat TLS du serveur.
- Ne pas désactiver la validation TLS.
- Valider les réponses reçues.
- Limiter la taille des résultats transmis.
- Respecter les autorisations du serveur.
- Ne pas exécuter de tâche non autorisée.
- Conserver une identité locale stable.
- Éviter les doublons de tâches.

---

## 18. Informations visibles dans le tableau de bord

L'administrateur doit pouvoir consulter :

- L'identifiant de l'agent.
- Son nom.
- Son rôle.
- Son runtime.
- Ses capacités déclarées.
- Son état.
- Sa dernière adresse IP observée.
- Son dernier heartbeat.
- Sa date d'enregistrement.
- Ses tâches.
- Son historique d'événements.

Les secrets d'authentification ne doivent jamais être affichés dans la liste des agents.

---

## 19. Tests de connexion

Le Builder Agent doit vérifier :

1. L'enregistrement avec une clé valide.
2. Le refus d'une clé invalide.
3. L'attribution d'un identifiant unique.
4. La réception du jeton individuel.
5. La connexion avec un jeton valide.
6. Le refus d'un jeton invalide.
7. Le fonctionnement des heartbeats.
8. La détection de l'état OFFLINE.
9. La récupération des tâches autorisées.
10. La transmission des résultats.
11. Le refus d'accès aux tâches d'un autre agent.
12. La révocation immédiate d'un jeton.
13. La reconnexion après interruption.
14. La reprise après redémarrage du connecteur.
15. Le comportement lorsque l'orchestrateur est OFFLINE.
16. L'absence de secrets dans les journaux.

---

## 20. Critères d'acceptation

Le protocole est validé si :

- Un agent peut s'enregistrer avec une clé valide.
- Le serveur lui attribue une identité.
- Le connecteur conserve cette identité.
- L'agent s'authentifie avec son jeton individuel.
- Les heartbeats sont traités.
- Les tâches sont récupérées et exécutées selon les autorisations.
- Les résultats sont transmis et persistés.
- Les jetons révoqués sont refusés.
- Les reconnexions ne créent pas de doublons.
- Le comportement OFFLINE est respecté.
- Les tests du protocole passent.

---

## 21. Règle de référence

Ce document constitue la référence du protocole de connexion des agents.

Toute intégration Hermes doit respecter ce protocole.

Le Builder Agent doit distinguer les fonctionnalités réellement implémentées dans le connecteur des fonctionnalités seulement décrites dans la documentation.
