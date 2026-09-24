
# API.md
# Multi-Agent Orchestrator — Spécification de l'API

**Version :** 1.0  
**Statut :** Spécification de référence  
**Protocole :** HTTPS en production  
**Format :** JSON  
**Préfixe API :** `/api/v1`

---

## 1. Présentation

L'API permet au frontend d'administration et aux agents autorisés de communiquer avec le backend.

Elle expose les fonctionnalités suivantes :

- Authentification administrateur.
- Enregistrement et authentification des agents.
- Gestion des agents.
- Heartbeats.
- Gestion des tâches.
- Consultation des journaux.
- Gestion de l'état de l'orchestrateur.
- Consultation de l'état de santé du service.

Toutes les routes doivent être documentées, validées et testées.

---

## 2. Règles générales

### 2.1 Format

Les requêtes et réponses utilisent JSON, sauf indication contraire.

Les dates sont exprimées au format ISO 8601 avec fuseau horaire UTC.

Exemple :

`2026-09-24T12:00:00Z`

### 2.2 Codes HTTP

| Code | Signification |
|---|---|
| 200 | Requête réussie |
| 201 | Ressource créée |
| 202 | Requête acceptée pour traitement |
| 204 | Requête réussie sans contenu |
| 400 | Requête invalide |
| 401 | Authentification manquante ou invalide |
| 403 | Autorisation insuffisante |
| 404 | Ressource introuvable |
| 409 | Conflit d'état |
| 422 | Données invalides |
| 429 | Limite de requêtes dépassée |
| 500 | Erreur interne |
| 503 | Service temporairement indisponible |

### 2.3 Format d'erreur

Toutes les erreurs doivent utiliser une structure cohérente.

```json
{
  "error": {
    "code": "INVALID_CREDENTIALS",
    "message": "Authentification invalide.",
    "request_id": "req_example"
  }
}
```

Ne jamais inclure de mot de passe, de jeton ou de secret dans une réponse d'erreur.

---

## 3. Authentification et autorisation

### 3.1 Administrateur

L'administrateur s'authentifie avec son nom d'utilisateur et son mot de passe.

Après authentification, le backend crée une session sécurisée.

La session doit être protégée contre le vol, les accès non autorisés et les attaques CSRF lorsque des cookies sont utilisés.

La déconnexion doit invalider la session côté serveur.

### 3.2 Agents

Les agents utilisent un jeton individuel.

Format de l'en-tête :

```http
Authorization: Bearer <AGENT_TOKEN>
```

Chaque requête authentifiée doit vérifier :

- L'existence du jeton.
- Sa validité.
- Son expiration éventuelle.
- Son état de révocation.
- Les autorisations associées.
- L'état logique de l'orchestrateur lorsque l'opération le nécessite.

Les jetons doivent être associés à un agent précis.

### 3.3 Clé d'enregistrement

Une clé d'enregistrement permet à un agent autorisé de demander son inscription.

Cette clé est distincte des jetons individuels des agents.

La clé d'enregistrement doit être protégée, révocable et limitée aux opérations nécessaires à l'enregistrement.

L'administrateur doit pouvoir consulter et gérer cette clé depuis le tableau de bord.

L'URL publique de l'API n'est pas un secret et ne remplace pas l'authentification.

---

## 4. Routes publiques

### 4.1 Santé du service

`GET /health`

Authentification : aucune.

Réponse :

```json
{
  "status": "ok",
  "service": "multi-agent-orchestrator",
  "version": "1.0.0"
}
```

Cette route ne doit révéler aucune information sensible sur le serveur.

---

## 5. Authentification administrateur

### 5.1 Connexion

`POST /api/v1/auth/login`

Authentification : aucune.

Requête :

```json
{
  "username": "admin",
  "password": "mot_de_passe"
}
```

Réponse : création d'une session sécurisée.

Le mot de passe ne doit pas être renvoyé dans la réponse.

### 5.2 Session courante

`GET /api/v1/auth/me`

Authentification : session administrateur.

Réponse :

```json
{
  "id": "admin_uuid",
  "username": "admin",
  "is_active": true
}
```

### 5.3 Déconnexion

`POST /api/v1/auth/logout`

Authentification : session administrateur.

Effet : invalider la session courante.

### 5.4 Modification du mot de passe

`POST /api/v1/auth/change-password`

Authentification : session administrateur.

Requête :

```json
{
  "current_password": "ancien_mot_de_passe",
  "new_password": "nouveau_mot_de_passe"
}
```

Le backend doit vérifier le mot de passe actuel avant d'accepter la modification.

---

## 6. Enregistrement des agents

### 6.1 Demande d'enregistrement

`POST /api/v1/agents/enroll`

Authentification : clé d'enregistrement.

Requête :

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

Le champ `client_instance_id` est un identifiant stable créé par le connecteur local lors de sa première installation. Il ne constitue pas une identité de confiance.

Le serveur doit :

1. Vérifier la clé d'enregistrement.
2. Valider les données.
3. Appliquer les règles d'enregistrement.
4. Attribuer un identifiant interne unique.
5. Déterminer le nom et le rôle autorisés.
6. Créer l'enregistrement de l'agent.
7. Générer un jeton individuel.
8. Enregistrer un événement d'audit.

Réponse :

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

Le jeton individuel doit être transmis de manière sécurisée et ne doit pas être journalisé.

La politique de renouvellement et de réenregistrement doit empêcher la création incontrôlée de doublons.

### 6.2 Informations de l'agent courant

`GET /api/v1/agents/me`

Authentification : jeton d'agent.

Réponse :

```json
{
  "agent_id": "agent_uuid",
  "name": "Research Agent",
  "role": "research",
  "status": "ONLINE",
  "capabilities": [
    "research",
    "summarization"
  ]
}
```

### 6.3 Heartbeat

`POST /api/v1/agents/heartbeat`

Authentification : jeton d'agent.

Requête :

```json
{
  "status": "ONLINE",
  "runtime_status": "ready",
  "timestamp": "2026-09-24T12:00:00Z"
}
```

Réponse :

```json
{
  "accepted": true,
  "agent_status": "ONLINE",
  "orchestrator_status": "ONLINE",
  "server_time": "2026-09-24T12:00:01Z"
}
```

Le serveur doit utiliser son propre horodatage pour déterminer le dernier contact fiable.

### 6.4 Récupération des tâches disponibles

`GET /api/v1/agents/tasks`

Authentification : jeton d'agent.

Retourne les tâches attribuées à l'agent et prêtes à être récupérées.

### 6.5 Confirmation de prise en charge

`POST /api/v1/agents/tasks/{task_id}/ack`

Authentification : jeton d'agent.

Effet : confirmer la prise en charge de la tâche.

### 6.6 Mise à jour d'une tâche

`POST /api/v1/agents/tasks/{task_id}/status`

Authentification : jeton d'agent.

Requête :

```json
{
  "status": "RUNNING",
  "message": "Exécution démarrée."
}
```

Seules les transitions autorisées sont acceptées.

### 6.7 Transmission du résultat

`POST /api/v1/agents/tasks/{task_id}/result`

Authentification : jeton d'agent.

Requête :

```json
{
  "status": "COMPLETED",
  "result": {
    "summary": "Résultat de la tâche."
  }
}
```

Le backend doit vérifier que l'agent est autorisé à transmettre le résultat de cette tâche.

---

## 7. Gestion des agents par l'administrateur

Toutes les routes de cette section exigent une session administrateur.

### 7.1 Liste des agents

`GET /api/v1/agents`

Paramètres facultatifs :

- `status`
- `role`
- `search`
- `page`
- `page_size`

Réponse :

```json
{
  "items": [
    {
      "id": "agent_uuid",
      "name": "Research Agent",
      "role": "research",
      "runtime": "hermes",
      "status": "ONLINE",
      "source_ip": "203.0.113.10",
      "last_seen_at": "2026-09-24T12:00:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

### 7.2 Détails d'un agent

`GET /api/v1/agents/{agent_id}`

### 7.3 Déconnexion logique d'un agent

`POST /api/v1/agents/{agent_id}/disconnect`

Effet :

- Demander l'arrêt de la session active.
- Invalider la connexion courante selon le protocole.
- Conserver l'enregistrement de l'agent.

Une déconnexion n'est pas une révocation définitive.

### 7.4 Révocation d'un agent

`POST /api/v1/agents/{agent_id}/revoke`

Effet :

- Révoquer les jetons actifs.
- Bloquer les nouvelles requêtes de l'agent.
- Enregistrer l'événement de révocation.
- Exiger un nouvel enregistrement pour rétablir l'accès.

### 7.5 Suppression d'un agent

`DELETE /api/v1/agents/{agent_id}`

La suppression doit être soumise à des règles de sécurité et de conservation des historiques.

La révocation doit être effectuée avant toute suppression définitive.

---

## 8. Gestion des tâches par l'administrateur

### 8.1 Création

`POST /api/v1/tasks`

Requête :

```json
{
  "title": "Analyse de données",
  "description": "Analyser les données fournies.",
  "priority": "NORMAL",
  "assigned_agent_id": "agent_uuid"
}
```

Réponse :

```json
{
  "id": "task_uuid",
  "status": "PENDING",
  "title": "Analyse de données",
  "assigned_agent_id": "agent_uuid"
}
```

### 8.2 Liste

`GET /api/v1/tasks`

Paramètres facultatifs :

- `status`
- `agent_id`
- `priority`
- `page`
- `page_size`

### 8.3 Détails

`GET /api/v1/tasks/{task_id}`

### 8.4 Annulation

`POST /api/v1/tasks/{task_id}/cancel`

L'annulation doit être refusée si la tâche est déjà dans un état terminal ou si elle ne peut plus être annulée.

---

## 9. Gestion de l'orchestrateur

Toutes les routes de cette section exigent une session administrateur.

### 9.1 État courant

`GET /api/v1/settings/orchestrator`

Réponse :

```json
{
  "desired_state": "ONLINE",
  "service_status": "RUNNING",
  "health_status": "HEALTHY"
}
```

### 9.2 Changement d'état

`POST /api/v1/settings/orchestrator/state`

Requête :

```json
{
  "desired_state": "OFFLINE"
}
```

Valeurs autorisées :

- `ONLINE`
- `OFFLINE`

Le changement doit être enregistré dans la base de données et dans les journaux d'audit.

### 9.3 Clé d'enregistrement

`GET /api/v1/settings/enrollment-key`

Authentification : session administrateur.

Effet : afficher la clé d'enregistrement à un administrateur autorisé.

Cette opération doit être journalisée sans enregistrer la clé en clair dans les logs.

### 9.4 Rotation de la clé

`POST /api/v1/settings/enrollment-key/rotate`

Authentification : session administrateur.

Effet : révoquer l'ancienne clé et générer une nouvelle clé.

Les agents qui ne disposent que de l'ancienne clé ne doivent plus pouvoir s'enregistrer.

---

## 10. Journaux et événements

### 10.1 Liste des événements

`GET /api/v1/logs`

Authentification : session administrateur.

Filtres :

- `severity`
- `event_type`
- `agent_id`
- `start_date`
- `end_date`
- `page`
- `page_size`

### 10.2 Détails d'un événement

`GET /api/v1/logs/{event_id}`

Les données retournées doivent être expurgées des secrets.

---

## 11. Sécurité des routes

Chaque route doit préciser :

- Le type d'authentification requis.
- Les rôles autorisés.
- Les validations d'entrée.
- Les transitions d'état autorisées.
- Les événements d'audit nécessaires.
- Les erreurs possibles.

Aucune route d'administration ne doit être accessible à un agent.

Aucune route agent ne doit permettre de consulter les données d'un autre agent sans autorisation explicite.

Les routes de documentation interactive de l'API doivent être protégées en production par une authentification administrateur.

---

## 12. Documentation OpenAPI

Le backend doit générer une documentation OpenAPI cohérente avec les routes réellement implémentées.

Les schémas, réponses, codes HTTP et mécanismes d'authentification doivent correspondre au comportement réel du système.

---

## 13. Critères de validation

L'API est validée lorsque :

1. Toutes les routes prévues sont implémentées.
2. Les erreurs utilisent un format cohérent.
3. Les données invalides sont rejetées.
4. Les agents non authentifiés sont refusés.
5. Les agents révoqués sont refusés.
6. Les autorisations sont vérifiées côté serveur.
7. Les changements d'état sont persistants.
8. Les événements sensibles sont journalisés.
9. Les tests couvrent les réponses nominales et les erreurs.
10. La documentation OpenAPI correspond à l'implémentation.

---

## 14. Règle de référence

Ce document constitue la référence du contrat API.

Toute modification de route, de schéma, de méthode d'authentification ou de comportement doit être documentée et validée avant d'être intégrée.
