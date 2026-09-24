
# SECURITY.md
# Multi-Agent Orchestrator — Politique de sécurité

**Version :** 1.0  
**Statut :** Spécification de référence

---

## 1. Objectif

Ce document définit les exigences de sécurité applicables au Multi-Agent Orchestrator.

La sécurité doit être intégrée à toutes les phases :

- Conception.
- Développement.
- Tests.
- Déploiement.
- Exploitation.
- Maintenance.

Le Builder Agent doit considérer la sécurité comme une exigence fonctionnelle obligatoire.

---

## 2. Modèle de menace

Le système doit prendre en compte les menaces suivantes :

- Tentatives de connexion non autorisées.
- Vol ou fuite de clés d'API.
- Utilisation de jetons révoqués.
- Usurpation d'identité d'un agent.
- Enregistrement frauduleux d'agents.
- Accès non autorisé aux données d'autres agents.
- Modification non autorisée des tâches.
- Injection de données malveillantes.
- Exploitation de routes insuffisamment protégées.
- Attaques par force brute.
- Déni de service applicatif.
- Divulgation de données sensibles dans les journaux.
- Compromission du serveur ou de la base de données.
- Mauvaise configuration de Nginx ou de TLS.
- Altération du code ou des dépendances.
- Exécution de tâches non autorisées par un agent.

---

## 3. Principes de sécurité

### 3.1 Défense en profondeur

La sécurité ne doit pas dépendre d'une seule mesure.

Les protections doivent être réparties entre :

- L'authentification.
- L'autorisation.
- Le backend.
- Le stockage.
- Le réseau.
- Le système d'exploitation.
- La supervision.
- Les journaux d'audit.

### 3.2 Moindre privilège

Chaque composant doit disposer uniquement des autorisations nécessaires.

Le backend doit fonctionner sous un utilisateur système non privilégié.

Un agent ne doit pas disposer automatiquement des privilèges d'administration.

### 3.3 Refus par défaut

Toute opération non explicitement autorisée doit être refusée.

### 3.4 Validation systématique

Toutes les données entrantes doivent être validées côté serveur.

Les validations du frontend ne remplacent jamais celles du backend.

---

## 4. Authentification administrateur

L'administrateur utilise un nom d'utilisateur et un mot de passe.

Exigences :

- Mot de passe stocké sous forme d'empreinte sécurisée.
- Utilisation d'un algorithme de hachage de mot de passe adapté, comme Argon2id ou bcrypt.
- Protection contre les tentatives répétées.
- Messages d'erreur ne révélant pas si le compte existe.
- Sessions sécurisées.
- Déconnexion effective.
- Protection contre les attaques CSRF lorsque des cookies sont utilisés.
- Cookies configurés avec `HttpOnly`, `Secure` en HTTPS et une politique `SameSite` adaptée.
- Expiration et renouvellement des sessions selon une politique documentée.

Le mot de passe ne doit jamais être transmis dans les journaux ou stocké en clair.

---

## 5. Gestion des clés et des jetons

### 5.1 Clé d'enregistrement

La clé d'enregistrement permet de demander l'enregistrement d'un agent.

Elle doit :

- Être générée cryptographiquement.
- Être suffisamment longue et imprévisible.
- Être protégée contre la divulgation.
- Pouvoir être renouvelée.
- Pouvoir être révoquée.
- Être utilisable uniquement pour les opérations prévues.

### 5.2 Jetons individuels des agents

Chaque agent doit disposer d'un jeton individuel.

Le jeton doit :

- Être généré de manière sécurisée.
- Être associé à un seul agent.
- Être vérifié à chaque requête.
- Pouvoir être révoqué.
- Ne pas être partagé entre agents.
- Ne pas être stocké en clair dans les journaux.

Le serveur doit conserver une représentation sécurisée du jeton permettant sa vérification.

### 5.3 Rotation

La rotation d'une clé ou d'un jeton doit invalider l'ancien secret selon une procédure documentée.

Les changements doivent être journalisés sans exposer les valeurs des secrets.

### 5.4 Révocation

Une révocation doit empêcher immédiatement les nouvelles opérations authentifiées avec le secret révoqué.

---

## 6. Contrôle d'accès

Les autorisations doivent être appliquées côté backend.

### Administrateur

Peut, selon les droits accordés :

- Consulter le tableau de bord.
- Gérer les agents.
- Créer et superviser les tâches.
- Modifier l'état de l'orchestrateur.
- Consulter les journaux.
- Gérer les clés d'enregistrement.
- Modifier son mot de passe.

### Agent

Peut uniquement :

- S'authentifier avec son propre jeton.
- Consulter son identité.
- Envoyer son heartbeat.
- Consulter les tâches qui lui sont attribuées.
- Mettre à jour les tâches autorisées.
- Transmettre les résultats des tâches qui lui sont attribuées.

Un agent ne doit pas pouvoir :

- Créer un compte administrateur.
- Modifier les paramètres de sécurité.
- Consulter les jetons d'autres agents.
- Révoquer un autre agent.
- Modifier l'état de l'orchestrateur.
- Lire les journaux d'administration.
- Accéder aux tâches d'autres agents sans autorisation explicite.

---

## 7. Sécurité de l'enregistrement des agents

L'enregistrement doit être authentifié et contrôlé.

Le serveur doit :

1. Vérifier la clé d'enregistrement.
2. Valider les données reçues.
3. Limiter la fréquence des demandes.
4. Détecter les demandes répétées suspectes.
5. Attribuer un identifiant interne.
6. Créer un jeton individuel.
7. Enregistrer l'événement.
8. Refuser les capacités non autorisées lorsque des politiques de capacités sont définies.

Les capacités déclarées par un agent ne doivent jamais suffire à lui accorder automatiquement des privilèges.

---

## 8. Sécurité des communications

En production, les communications externes doivent utiliser HTTPS.

Nginx doit :

- Utiliser un certificat TLS valide.
- Rediriger HTTP vers HTTPS après configuration du certificat.
- Transmettre les en-têtes nécessaires au backend.
- Éviter de faire confiance aux en-têtes d'adresse IP fournis directement par un client.
- Limiter les méthodes et chemins exposés lorsque cela est possible.
- Préserver la confidentialité des données de session.

Le backend ne doit faire confiance aux en-têtes `X-Forwarded-For` ou similaires que lorsque la requête provient d'un proxy explicitement approuvé.

---

## 9. Protection des données

Les données doivent être limitées à ce qui est nécessaire au fonctionnement du système.

Exigences :

- Contrôle d'accès aux données.
- Protection des secrets.
- Sauvegardes sécurisées.
- Permissions restrictives sur les fichiers.
- Conservation des historiques selon une politique définie.
- Suppression contrôlée des données.
- Absence de secrets dans les réponses API.
- Absence de secrets dans le dépôt Git.

Les résultats des agents doivent être considérés comme des données non fiables.

---

## 10. Journalisation de sécurité

Le système doit journaliser les événements suivants :

- Connexions administrateur réussies.
- Tentatives de connexion échouées.
- Déconnexions.
- Enregistrements d'agents.
- Échecs d'authentification d'agents.
- Heartbeats anormaux.
- Révocations de jetons.
- Changements de l'état ONLINE/OFFLINE.
- Création, modification et annulation de tâches.
- Erreurs de sécurité.
- Changements de configuration sensibles.
- Redémarrages et erreurs du service.

Chaque événement doit inclure, lorsque disponible :

- Un identifiant d'événement.
- Une date et une heure.
- Un type d'événement.
- Un niveau de gravité.
- L'identité de l'acteur.
- L'adresse IP observée.
- L'objet concerné.
- Le résultat de l'opération.

### Secrets interdits dans les journaux

Ne jamais journaliser :

- Les mots de passe.
- Les jetons d'accès.
- Les clés d'enregistrement.
- Les cookies de session.
- Les en-têtes `Authorization`.
- Les secrets de configuration.

Les données sensibles présentes dans les requêtes ou résultats doivent être filtrées ou expurgées.

---

## 11. Surveillance et alertes

Le système doit détecter et signaler :

- Les échecs d'authentification répétés.
- Les tentatives d'utilisation de jetons révoqués.
- Les erreurs d'autorisation.
- Les agents devenus hors ligne.
- Les erreurs répétées de l'API.
- Les changements d'état du service.
- Les anomalies de comportement détectables à partir des journaux.

Les alertes doivent être visibles dans le tableau de bord.

Le système doit distinguer la journalisation applicative de la surveillance réseau au niveau des paquets.

La journalisation applicative ne remplace pas un IDS ou une capture réseau.

---

## 12. Protection du backend

Exigences :

- Validation des schémas d'entrée.
- Requêtes SQL paramétrées ou ORM sécurisé.
- Gestion centralisée des erreurs.
- Limitation de débit sur les routes sensibles.
- Protection contre les accès directs aux objets non autorisés.
- Vérification de l'appartenance d'une tâche à l'agent demandeur.
- Contrôle de la taille des requêtes.
- Validation des fichiers et contenus reçus.
- Gestion des dépendances.
- Absence de mode debug en production.

---

## 13. Sécurité du frontend

Le frontend doit :

- Ne jamais contenir de secret serveur.
- Ne pas stocker les jetons sensibles dans un emplacement accessible au JavaScript sans nécessité.
- Échapper les contenus affichés.
- Éviter l'injection de HTML non fiable.
- Protéger les formulaires sensibles.
- Masquer les informations non autorisées.
- Utiliser HTTPS en production.
- Gérer correctement l'expiration des sessions.

Les autorisations d'interface ne remplacent pas les contrôles du backend.

---

## 14. Sécurité du serveur

Le déploiement doit :

- Utiliser un utilisateur système dédié.
- Appliquer des permissions restrictives.
- Protéger les fichiers de configuration.
- Limiter les ports ouverts.
- Préserver les règles de pare-feu existantes.
- Éviter les services inutiles.
- Protéger la base de données.
- Sécuriser les sauvegardes.
- Conserver les journaux utiles à l'audit.
- Éviter l'exécution du backend en root.

---

## 15. Gestion des secrets

Les secrets de production doivent être fournis par un mécanisme sécurisé.

Ils ne doivent pas être :

- Commités dans Git.
- Inclus dans les images ou bundles frontend.
- Affichés dans les messages d'erreur.
- Imprimés dans les journaux.
- Transmis à des services externes non autorisés.

Le fichier `.env.example` doit contenir uniquement des valeurs fictives et des instructions.

Le fichier réel de configuration doit être exclu du dépôt.

---

## 16. Tests de sécurité obligatoires

Le Builder Agent doit tester :

1. Connexion avec mot de passe incorrect.
2. Accès à une route admin sans session.
3. Accès à une route admin avec un jeton agent.
4. Utilisation d'un jeton révoqué.
5. Utilisation d'une clé d'enregistrement invalide.
6. Accès à une tâche appartenant à un autre agent.
7. Tentative de modification non autorisée d'un état.
8. Tentatives répétées d'authentification.
9. Validation des entrées malformées.
10. Absence de secrets dans les journaux.
11. Expiration et invalidation des sessions.
12. Vérification des permissions de fichiers.
13. Contrôle de l'accès aux routes de documentation.
14. Vérification de la persistance des révocations après redémarrage.

---

## 17. Gestion des vulnérabilités

Toute vulnérabilité détectée doit être :

1. Documentée.
2. Reproduite dans un environnement de test.
3. Évaluée selon son impact.
4. Corrigée.
5. Couvertе par un test de non-régression.
6. Vérifiée après correction.

Aucune vulnérabilité critique connue ne doit être ignorée avant une mise en production.

---

## 18. Critères d'acceptation

Le système est considéré comme conforme si :

- Les accès non autorisés sont refusés.
- Les secrets sont protégés.
- Les jetons individuels sont révocables.
- Les permissions sont vérifiées côté serveur.
- Les événements sensibles sont audités.
- Les communications de production utilisent HTTPS.
- Les tests de sécurité passent.
- Les journaux ne divulguent aucun secret.
- Les données persistent de manière sécurisée.

---

## 19. Règle de référence

Ce document constitue la référence de sécurité du projet.

Toute fonctionnalité doit respecter les exigences de ce document.

Une fonctionnalité ne doit pas être déclarée terminée si elle contourne un contrôle de sécurité requis.
