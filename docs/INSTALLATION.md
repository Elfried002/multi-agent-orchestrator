
# INSTALLATION.md
# Multi-Agent Orchestrator — Installation et déploiement

**Version :** 1.0  
**Statut :** Spécification de référence  
**Environnement cible :** Ubuntu Server  
**Déploiement :** Script Bash automatisé

---

## 1. Objectif

Ce document définit le processus d'installation, de mise à jour, de vérification et de désinstallation du Multi-Agent Orchestrator.

L'installation doit être reproductible et préserver les services préexistants du serveur.

Le script principal est :

`deploy/setup.sh`

---

## 2. Environnement cible

Le serveur doit disposer :

- D'un système Ubuntu Server compatible avec les dépendances retenues.
- D'un accès administrateur via sudo.
- D'une connexion Internet.
- D'un nom de domaine ou sous-domaine.
- D'un enregistrement DNS pointant vers le serveur.
- Des ports nécessaires accessibles.
- D'un espace disque suffisant.
- D'une adresse IP publique si le service doit être accessible depuis Internet.

Les versions précises des dépendances doivent être documentées et testées avant la mise en production.

---

## 3. Préparation du domaine

L'administrateur doit disposer d'un nom de domaine ou d'un sous-domaine.

Exemple :

`api.example.com`

Le DNS doit pointer vers l'adresse IP du serveur.

Le script doit vérifier :

- La résolution DNS.
- La cohérence de l'adresse résolue avec le serveur cible, lorsque cette vérification est possible.
- La disponibilité des ports nécessaires.
- La présence éventuelle d'une configuration Nginx existante.

Le script doit signaler les problèmes de DNS avant de demander l'émission d'un certificat TLS.

---

## 4. Informations demandées pendant l'installation

Le script doit demander les informations suivantes.

### 4.1 Domaine

Question :

`Entrez votre domaine ou sous-domaine :`

Exemple :

`api.example.com`

### 4.2 Adresse e-mail

Question :

`Entrez votre adresse e-mail pour Let's Encrypt :`

Cette adresse sert à la gestion du certificat TLS.

### 4.3 Compte administrateur

Le script doit demander :

- Le nom d'utilisateur administrateur.
- Le mot de passe administrateur.
- La confirmation du mot de passe.

Le mot de passe doit être masqué pendant la saisie.

Le script doit vérifier que les deux saisies correspondent et appliquer une politique de robustesse.

Le mot de passe doit être transmis de manière sécurisée au processus d'initialisation du backend.

Il ne doit jamais être écrit en clair dans les journaux, dans le dépôt Git ou dans un fichier de configuration.

Le backend doit stocker uniquement une empreinte sécurisée du mot de passe.

---

## 5. Étapes de l'installation

Le script `setup.sh` doit exécuter les étapes suivantes dans l'ordre.

### Étape 1 — Vérification des privilèges

- Vérifier que le script est exécuté avec les privilèges nécessaires.
- Vérifier la version du système.
- Vérifier la disponibilité des outils requis.
- Arrêter l'installation en cas de prérequis bloquant.

### Étape 2 — Collecte des paramètres

- Demander le domaine ou sous-domaine.
- Demander l'adresse e-mail.
- Demander le nom d'utilisateur administrateur.
- Demander le mot de passe et sa confirmation.
- Valider les données saisies.

### Étape 3 — Vérification réseau

- Vérifier le DNS.
- Vérifier la connectivité Internet.
- Vérifier les ports nécessaires.
- Identifier les conflits potentiels avec les services existants.

### Étape 4 — Vérification de l'infrastructure existante

Le script doit détecter :

- Une installation précédente de l'orchestrateur.
- Un service systemd existant.
- Une configuration Nginx existante.
- Un certificat Let's Encrypt existant.
- Une base de données existante.
- Un répertoire de données existant.

Aucune configuration préexistante ne doit être supprimée ou écrasée sans validation explicite.

### Étape 5 — Installation des dépendances

Installer uniquement les dépendances nécessaires :

- Python.
- Git.
- Nginx si nécessaire.
- Certbot si nécessaire.
- Les bibliothèques système requises.

Le script doit vérifier si les dépendances sont déjà installées.

Il ne doit pas désinstaller des composants système sans nécessité documentée.

### Étape 6 — Installation du code

- Créer un répertoire dédié à l'application.
- Récupérer le dépôt Git ou utiliser le code fourni.
- Créer un environnement virtuel Python.
- Installer les dépendances backend.
- Installer et compiler le frontend.
- Vérifier que la compilation s'est terminée correctement.

### Étape 7 — Configuration

- Générer un fichier de configuration de production.
- Configurer le domaine.
- Configurer la base de données.
- Générer les secrets nécessaires.
- Configurer les paramètres de sécurité.
- Définir les chemins de stockage persistants.

Les secrets doivent être placés dans un emplacement protégé, accessible uniquement aux utilisateurs autorisés.

### Étape 8 — Initialisation de la base de données

- Créer la base SQLite si elle n'existe pas.
- Appliquer les migrations.
- Créer le compte administrateur à partir des informations saisies.
- Initialiser l'état logique de l'orchestrateur.
- Générer la clé d'enregistrement des agents.

La création initiale du compte administrateur doit être atomique et ne doit pas créer plusieurs comptes identiques lors d'une réexécution.

### Étape 9 — Configuration Nginx

- Créer une configuration dédiée à l'orchestrateur.
- Configurer le reverse proxy vers le backend.
- Configurer les en-têtes nécessaires.
- Configurer la redirection HTTP vers HTTPS après activation du certificat.
- Tester la configuration avant rechargement.

Le script doit préserver les autres sites Nginx.

Il ne doit pas remplacer une configuration existante portant un autre nom de domaine.

### Étape 10 — Certificat TLS

- Détecter les certificats existants.
- Réutiliser un certificat valide lorsqu'il correspond au domaine et que son utilisation est appropriée.
- Sinon, demander ou effectuer l'émission d'un certificat avec Certbot.
- Vérifier le certificat.
- Configurer HTTPS.

Aucun certificat existant ne doit être supprimé automatiquement.

### Étape 11 — Création du service systemd

Créer :

`/etc/systemd/system/orchestrator.service`

Le service doit :

- Utiliser un utilisateur système dédié non privilégié.
- Démarrer le backend depuis le bon répertoire.
- Utiliser l'environnement Python prévu.
- Charger les secrets depuis un fichier protégé.
- Redémarrer le processus en cas de panne selon une politique documentée.
- Être activé au démarrage du serveur.

Le service doit rester actif même lorsque l'administrateur se déconnecte de SSH.

### Étape 12 — Démarrage

- Recharger la configuration systemd.
- Activer le service.
- Démarrer le service.
- Vérifier son état.
- Vérifier les journaux de démarrage.

### Étape 13 — Vérifications finales

Vérifier :

- La santé du backend.
- L'accessibilité HTTPS.
- La disponibilité du frontend.
- La connexion à la base de données.
- La présence du compte administrateur.
- L'état du service systemd.
- La disponibilité de l'interface de connexion.

### Étape 14 — Résumé final

Afficher :

- Le domaine configuré.
- L'URL du tableau de bord.
- L'URL de connexion.
- L'état du service.
- Le chemin des journaux.
- Le chemin de la base de données.
- La commande permettant de vérifier le service.

Ne jamais afficher le mot de passe administrateur ni les secrets internes.

---

## 6. Répertoires de production

Les chemins doivent être centralisés dans la configuration du déploiement.

Organisation recommandée :

```text
/opt/multi-agent-orchestrator/
    application/

/etc/multi-agent-orchestrator/
    production.env

/var/lib/multi-agent-orchestrator/
    orchestrator.db
    data/

/var/log/multi-agent-orchestrator/
    application.log
    audit.log
```

Les permissions doivent être définies de manière restrictive.

Les chemins définitifs doivent être cohérents entre le script d'installation, le service systemd et le backend.

---

## 7. Service systemd

Nom du service :

`orchestrator.service`

Commandes de gestion :

```bash
sudo systemctl status orchestrator
sudo systemctl start orchestrator
sudo systemctl stop orchestrator
sudo systemctl restart orchestrator
sudo journalctl -u orchestrator -f
```

Le service doit démarrer automatiquement au redémarrage du serveur.

L'état logique de l'orchestrateur doit être restauré depuis la base de données.

L'arrêt du processus systemd doit rester distinct du passage logique à OFFLINE depuis le tableau de bord.

---

## 8. Mise à jour

Script :

`deploy/update.sh`

Le processus de mise à jour doit :

1. Vérifier l'installation existante.
2. Vérifier l'état du service.
3. Sauvegarder la base de données.
4. Sauvegarder la configuration pertinente.
5. Récupérer la version souhaitée du code.
6. Installer les dépendances nécessaires.
7. Appliquer les migrations compatibles.
8. Compiler le frontend.
9. Redémarrer le service si nécessaire.
10. Vérifier la santé du système.
11. Signaler clairement le résultat.

La mise à jour ne doit pas supprimer les données persistantes.

Une stratégie de retour arrière doit être documentée.

---

## 9. Désinstallation

Script :

`deploy/uninstall.sh`

La désinstallation doit demander une confirmation explicite.

Elle doit distinguer :

### Suppression de l'application

- Arrêter et désactiver le service dédié.
- Supprimer les fichiers applicatifs propres à l'orchestrateur.
- Supprimer la configuration Nginx propre à l'orchestrateur.

### Conservation des données

Par défaut, les données persistantes doivent être conservées ou sauvegardées.

La suppression de la base de données et des journaux doit exiger une confirmation distincte.

### Protection de l'infrastructure existante

Le script ne doit pas :

- Supprimer les autres sites Nginx.
- Supprimer des certificats TLS préexistants.
- Désinstaller Nginx s'il est utilisé par d'autres services.
- Supprimer des bases de données étrangères au projet.
- Modifier les services sans rapport avec l'orchestrateur.

---

## 10. Gestion des erreurs

Chaque étape doit vérifier son résultat.

En cas d'échec :

- Afficher un message compréhensible.
- Enregistrer l'erreur sans divulguer de secret.
- Interrompre les étapes dépendantes.
- Éviter de laisser une configuration partiellement activée.
- Fournir les informations nécessaires à la correction.

Les opérations destructrices doivent être évitées par défaut.

---

## 11. Sécurité du déploiement

Le script doit :

- Utiliser des permissions restrictives.
- Éviter les secrets dans les arguments de processus.
- Ne pas écrire de mots de passe dans l'historique du shell.
- Protéger les fichiers d'environnement.
- Valider les entrées utilisateur.
- Éviter l'exécution de commandes construites directement à partir de chaînes non fiables.
- Vérifier les fichiers téléchargés.
- Ne pas désactiver le pare-feu global.
- Ne pas ouvrir de ports inutiles.

---

## 12. Tests d'installation

Le Builder Agent doit tester :

1. Une installation neuve.
2. Une installation avec Nginx déjà présent.
3. Une installation avec un certificat existant.
4. Une installation avec un DNS invalide.
5. Une installation avec un port occupé.
6. Une saisie de mot de passe incorrecte.
7. Une réexécution du script.
8. Une mise à jour.
9. Une désinstallation avec conservation des données.
10. Une désinstallation avec suppression explicitement confirmée.
11. Le redémarrage du serveur.
12. La conservation de l'état ONLINE/OFFLINE.

Les tests destructifs doivent être réalisés dans un environnement isolé.

---

## 13. Critères d'acceptation

L'installation est validée si :

- Le script s'exécute sans intervention manuelle inutile.
- Le compte administrateur est créé de manière sécurisée.
- Le service démarre automatiquement.
- Le tableau de bord est accessible en HTTPS.
- Les données persistent après redémarrage.
- L'état logique est restauré correctement.
- Les configurations préexistantes sont préservées.
- La mise à jour ne détruit pas les données.
- La désinstallation respecte les confirmations et les limites définies.

---

## 14. Règle de référence

Ce document constitue la référence pour l'installation, la mise à jour et la désinstallation.

Toute opération touchant aux certificats, aux configurations Nginx ou aux données préexistantes doit respecter les règles de préservation définies ici.
