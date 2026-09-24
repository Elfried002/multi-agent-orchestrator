# Connecteur Hermes ↔ Multi-Agent Orchestrator

Implémentation du protocole décrit dans `docs/AGENT_CONNECTION.md`, côté agent.
Le connecteur est le **seul** mécanisme d'intégration réseau : un prompt système ou
un script indépendant ne constitue pas une intégration.

Dépendances : bibliothèque standard Python uniquement (aucun paquet à installer).

## Installation

Le connecteur n'a besoin que de Python 3.11+ :

```bash
python3 connector/hermes_connector.py --help
```

Sur un hôte où Hermes Agent est installé, le même interpréteur suffit.
Aucun paquet supplémentaire, aucune clé API tierce.

## Utilisation

### 1. Enregistrement

La clé d'enregistrement est fournie par l'administrateur (page **Paramètres** du
tableau de bord, `GET /api/v1/settings/enrollment-key`).

```bash
ORCHESTRATOR_ENROLLMENT_KEY='<clé>' \
python3 connector/hermes_connector.py enroll \
  --url https://orchestrateur.exemple.com \
  --name "Hermes production 01" \
  --role builder \
  --capability build --capability test
```

Le serveur attribue lui-même l'identifiant interne (`agt_…`) ; le connecteur le
conserve avec son jeton dans un fichier d'état local en `0600`
(`~/.orchestrator-agent/state.json`, modifiable avec `--state`).

**Le jeton n'est jamais affiché** : `status` n'en montre que le préfixe, les
quatre derniers caractères et une empreinte SHA-256 tronquée.

### 2. Boucle de travail

```bash
python3 connector/hermes_connector.py run --url https://orchestrateur.exemple.com
```

Chaque cycle :

1. *heartbeat* (présence horodatée par le serveur) ;
2. si l'orchestrateur répond `ONLINE`, récupération des tâches attribuées ;
3. acquittement (idempotent : une tâche déjà prise en charge n'est jamais rejouée) ;
4. exécution, puis transmission du résultat.

En cas d'erreur, le délai double progressivement (max 300 s, avec un aléa) ; une
révocation ou un jeton refusé arrête la boucle avec le code de sortie `2`.

### 3. Exécution des tâches : explicite et jamais implicite

Par défaut, **le connecteur n'exécute rien**. Il acquitte la tâche puis la marque
en échec avec le motif « aucun exécuteur configuré ». Cette décision est
délibérée : le contenu d'une tâche ne doit jamais devenir une commande locale
par accident.

Pour autoriser une exécution, il faut la déclarer :

```bash
python3 connector/hermes_connector.py run \
  --commande 'printf "%s" "$ORCHESTRATOR_TASK_TITLE" >> /var/log/livrables.txt'
```

Variables disponibles pour l'exécuteur : `ORCHESTRATOR_TASK_ID`,
`ORCHESTRATOR_TASK_TITLE`, `ORCHESTRATOR_TASK_DESCRIPTION`,
`ORCHESTRATOR_AGENT_ID`. L'exécuteur est lancé avec un délai maximal
(`--task-timeout`, 120 s par défaut) ; la sortie standard et l'erreur standard
sont renvoyées au serveur (tronquées).

`--dry-run` permet d'acquitter sans exécuter, pour valider une chaîne complète.

### 3 bis. Intégration du runtime Hermes

Deuxième exécuteur possible, et c'est celui de l'intégration réelle : la CLI Hermes
locale traite l'énoncé de la tâche et sa réponse devient le résultat enregistré par
l'orchestrateur.

```bash
python3 connector/hermes_connector.py run --hermes
# ou, si la commande n'est pas dans le PATH :
python3 connector/hermes_connector.py run --hermes --hermes-binaire /chemin/vers/hermes
```

Le connecteur construit une consigne à partir du titre et de la description de la
tâche, l'envoie à `hermes -z` en mode ponctuel, puis transmet :

- en cas de succès, `{"moteur": "hermes-cli", "agent": "hermes", "resume": "<réponse>"}`
  et la tâche passe en `COMPLETED` ;
- en cas d'échec ou de dépassement de délai, un message d'erreur exploitable et la
  tâche passe en `FAILED`.

Le serveur ne décide donc jamais **quoi** exécuter : il fournit un énoncé, le
connecteur le confie au runtime explicitement autorisé par l'exploitant.

Banc d'essai de cette intégration (démarre un vrai serveur, enrôle un agent, crée une
tâche, la fait traiter par Hermes et vérifie le résultat enregistré ; ignoré si la CLI
Hermes est absente) :

```bash
python3 connector/tests/test_integration_hermes.py
```

### 4. Autres commandes

| Commande | Effet |
|---|---|
| `status` | identité locale, orchestrateur, empreinte du jeton, nombre de tâches traitées |
| `forget` | supprime le fichier d'état local — **le jeton reste valide côté serveur** jusqu'à révocation de l'agent |
| `disconnect` | demande la déconnexion logique. Le protocole ne prévoit pas cette route côté agent : la déconnexion est une action **administrative** (`POST /api/v1/agents/{id}/disconnect`) ou, à défaut, l'arrêt des heartbeats, que le serveur interprète comme un passage `OFFLINE` |

## Service systemd (exemple, côté agent)

```ini
[Unit]
Description=Connecteur Hermes - Multi-Agent Orchestrator
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=hermes
EnvironmentFile=/etc/hermes-connector.env
ExecStart=/usr/bin/python3 /opt/connector/hermes_connector.py run --url ${ORCHESTRATOR_URL}
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

`/etc/hermes-connector.env` (mode `0600`, propriétaire `hermes`) :

```
ORCHESTRATOR_URL=https://orchestrateur.exemple.com
```

Le jeton d'agent vit dans le fichier d'état (`0600`) et n'a pas à figurer dans
l'unité systemd.

## Sécurité

- **Vérification TLS toujours active** : aucune option ne permet de la désactiver.
- **Aucune donnée sensible journalisée** : les traces sont en JSON sur la sortie
  standard (reprises par journald) et ne contiennent jamais le jeton.
- **Protection contre les doublons** : l'identité locale du poste (nom d'hôte,
  système, architecture) est dérivée en identifiant d'instance stable ; un second
  enregistrement est refusé par le serveur (`AGENT_ALREADY_ENROLLED`, HTTP 409)
  et le connecteur explique de réutiliser le jeton conservé.
- **Reprise après interruption** : les identifiants des tâches déjà traitées sont
  conservés localement (500 derniers) pour éviter tout double traitement après un
  redémarrage ; la transmission du résultat reste idempotente côté serveur.

## Vérification

Le test bout en bout démarre un vrai serveur HTTP, crée le compte administrateur,
enregistre l'agent, exécute une tâche réelle et vérifie l'état côté serveur, puis
contrôle qu'une tâche hostile n'est **pas** exécutée sans exécuteur déclaré :

```bash
bash connector/tests/test_bout_en_bout.sh
```
