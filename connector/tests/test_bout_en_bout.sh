#!/usr/bin/env bash
# Bout en bout : connecteur Hermes contre un VRAI serveur HTTP uvicorn.
#
# 1. démarrage du backend (uvicorn) sur un port de test, base dédiée
# 2. création du compte administrateur par le script officiel (--password-stdin)
# 3. connexion administrateur + création d'une tâche attribuée à l'agent
# 4. connecteur : enroll → status → run (heartbeat + pull + ack + résultat)
# 5. vérification de l'état réel côté serveur
# 6. cas négatifs : double enregistrement, tâche sans exécuteur configuré
#
# Usage : bash connector/tests/test_bout_en_bout.sh
set -Eeuo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Les arguments passés à Python (natif Windows) doivent être des chemins natifs :
# le shell MSYS convertit les chemins de commande, pas les arguments.
if command -v cygpath >/dev/null 2>&1; then
  RACINE_N="$(cygpath -w "$RACINE")"
else
  RACINE_N="$RACINE"
fi
BACKEND="$RACINE/backend"
PYTHON="$BACKEND/.venv/Scripts/python.exe"
[ -x "$PYTHON" ] || PYTHON="$BACKEND/.venv/bin/python"
# Deux formes du même répertoire : POSIX pour les opérations shell, natif pour
# les arguments et variables passés à Python (natif Windows).
BASE_TRAVAIL="${LOCALAPPDATA:-$HOME}/Temp/mo-e2e"
if command -v cygpath >/dev/null 2>&1; then
  TRAVAIL_SHELL="$(cygpath -u "$BASE_TRAVAIL")"
  TRAVAIL="$(cygpath -w "$BASE_TRAVAIL")"
else
  TRAVAIL_SHELL="$BASE_TRAVAIL"
  TRAVAIL="$BASE_TRAVAIL"
fi
PORT="${PORT_E2E:-8171}"
BASE="http://127.0.0.1:${PORT}"
ETAT_CONNECTEUR="$TRAVAIL/state.json"

MOT_DE_PASSE="Connecteur-E2E-2026!"
CLE_ENREGISTREMENT="cle-enregistrement-e2e-$(date +%s)-abcdef"

reussis=0
echoues=0
verifier() {
  if [ "$1" = "1" ]; then
    reussis=$((reussis + 1)); printf '  [OK]   %s\n' "$2"
  else
    echoues=$((echoues + 1)); printf '  [ECHEC] %s\n' "$2"
  fi
}

nettoyer() {
  if [ -n "${PID_SERVEUR:-}" ]; then
    kill "$PID_SERVEUR" 2>/dev/null || true
    wait "$PID_SERVEUR" 2>/dev/null || true
  fi
}
trap nettoyer EXIT

rm -rf "$TRAVAIL_SHELL"
mkdir -p "$TRAVAIL_SHELL"

echo "  interpréteur   : $PYTHON"
echo "  racine native  : $RACINE_N"
echo "=== 1. Démarrage du backend (uvicorn, port $PORT) ==="
cd "$BACKEND"
SECRET_KEY="secret-de-test-e2e-0123456789abcdefghijklmnop" \
ORCHESTRATOR_ENV=development \
DATABASE_PATH="$TRAVAIL/orchestrateur.db" \
ENROLLMENT_KEY="$CLE_ENREGISTREMENT" \
LOG_LEVEL=INFO \
MONITOR_ENABLED=true \
MONITOR_INTERVAL_SECONDS=5 \
LOGIN_RATE_LIMIT="100/minute" \
ALLOWED_ORIGINS="" \
ENABLE_DOCS=false \
"$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning \
  > "$TRAVAIL/serveur.log" 2>&1 &
PID_SERVEUR=$!

pret=0
for _ in $(seq 1 60); do
  if "$PYTHON" - "$BASE" <<'PY' 2>/dev/null
import sys, urllib.request
with urllib.request.urlopen(f"{sys.argv[1]}/health", timeout=2) as r:
    sys.exit(0 if r.status == 200 else 1)
PY
  then pret=1; break; fi
  sleep 0.5
done
verifier "$pret" "le service répond sur /health"
if [ "$pret" != "1" ]; then tail -20 "$TRAVAIL_SHELL/serveur.log"; exit 1; fi

echo
echo "=== 2. Création du compte administrateur (script officiel) ==="
printf '%s\n' "$MOT_DE_PASSE" | ORCHESTRATOR_ENV=development \
  DATABASE_PATH="$TRAVAIL/orchestrateur.db" \
  SECRET_KEY="secret-de-test-e2e-0123456789abcdefghijklmnop" \
  "$PYTHON" scripts/create_admin.py --username admin --password-stdin \
  > "$TRAVAIL/admin.log" 2>&1
code=$?
verifier "$([ $code -eq 0 ] && echo 1 || echo 0)" "compte administrateur créé (code $code)"
grep -qi "mot de passe" "$TRAVAIL_SHELL/admin.log" && echo "  (le mot de passe n'est pas réaffiché)"

echo
echo "=== 3. Connexion administrateur et création d'une tâche ==="
"$PYTHON" - "$BASE" "$MOT_DE_PASSE" "$TRAVAIL" <<'PY'
import json, sys, urllib.request, urllib.error

base, mot_de_passe, travail = sys.argv[1], sys.argv[2], sys.argv[3]

def appel(methode, chemin, corps=None, jeton=None, csrf=None, cookie=None):
    donnees = json.dumps(corps).encode() if corps is not None else None
    requete = urllib.request.Request(f"{base}{chemin}", data=donnees, method=methode)
    requete.add_header("Accept", "application/json")
    if donnees is not None:
        requete.add_header("Content-Type", "application/json")
    if jeton:
        requete.add_header("Authorization", f"Bearer {jeton}")
    if csrf:
        requete.add_header("X-CSRF-Token", csrf)
    if cookie:
        requete.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(requete, timeout=10) as reponse:
            return reponse.status, json.loads(reponse.read().decode() or "null"), reponse.headers
    except urllib.error.HTTPError as erreur:
        brut = erreur.read().decode()
        return erreur.code, (json.loads(brut) if brut else None), erreur.headers

statut, corps, entetes = appel("POST", "/api/v1/auth/login",
                               {"username": "admin", "password": mot_de_passe})
assert statut == 200, (statut, corps)
csrf = corps["csrf_token"]
cookie = entetes.get("Set-Cookie", "").split(";")[0]
json.dump({"csrf": csrf, "cookie": cookie}, open(f"{travail}/session.json", "w"))

# Tâche sans agent : elle doit rester PENDING (aucun agent enregistré à ce stade).
statut, tache, _ = appel("POST", "/api/v1/tasks", {"title": "Tâche avant enregistrement"},
                         csrf=csrf, cookie=cookie)
assert statut == 201, (statut, tache)
json.dump(tache, open(f"{travail}/tache_pending.json", "w"))
print(f"  tâche créée sans agent : {tache['id']} ({tache['status']})")
PY
verifier "$([ $? -eq 0 ] && echo 1 || echo 0)" "tâche créée par l'administrateur (statut PENDING)"

echo
echo "=== 4. Connecteur : enregistrement ==="
ORCHESTRATOR_STATE_DIR="$TRAVAIL" "$PYTHON" "$RACINE_N/connector/hermes_connector.py" enroll \
  --url "$BASE" --enrollment-key "$CLE_ENREGISTREMENT" --name "Hermes E2E" --role builder \
  --capability build --capability test --state "$ETAT_CONNECTEUR" 2>&1 | sed 's/^/  /'
verifier "$([ -f "$TRAVAIL_SHELL/state.json" ] && echo 1 || echo 0)" "état local écrit"
case "$(uname -s 2>/dev/null || echo inconnu)" in
  Linux|Darwin)
    permissions=$(stat -c '%a' "$TRAVAIL_SHELL/state.json" 2>/dev/null || stat -f '%Lp' "$TRAVAIL_SHELL/state.json" 2>/dev/null || echo "?")
    verifier "$([ "$permissions" = "600" ] && echo 1 || echo 0)" "jeton conservé en 0600 (permissions: $permissions)"
    ;;
  *)
    # Windows (MSYS) : chmod n'agit que sur l'attribut lecture seule, les bits POSIX
    # n'existent pas. Le code applique bien 0600 ; la vérification n'a de sens que
    # sous Linux/Ubuntu et reste donc déclarée NON VÉRIFIÉE ici.
    echo "  [N/A]  permissions POSIX du jeton non vérifiables sous Windows — à confirmer sur Ubuntu"
    ;;
esac

AGENT_ID="$("$PYTHON" -c "import json,sys;print(json.load(open(sys.argv[1]))['agent_id'])" "$ETAT_CONNECTEUR")"
echo "  agent attribué par le serveur : $AGENT_ID"

echo
echo "=== 5. Connecteur : second enregistrement (doit être refusé) ==="
sortie=$("$PYTHON" "$RACINE_N/connector/hermes_connector.py" enroll \
  --url "$BASE" --enrollment-key "$CLE_ENREGISTREMENT" --name "Hermes doublon" \
  --state "$ETAT_CONNECTEUR" 2>&1 || true)
verifier "$(echo "$sortie" | grep -q "existe déjà\|AGENT_ALREADY_ENROLLED" && echo 1 || echo 0)" \
  "connecteur redémarré : aucun doublon créé"

echo
echo "=== 6. Tâche attribuée à l'agent, puis connecteur « run » ==="
"$PYTHON" - "$BASE" "$TRAVAIL" "$AGENT_ID" <<'PY'
import json, sys, urllib.request, urllib.error

base, travail, agent_id = sys.argv[1], sys.argv[2], sys.argv[3]
session = json.load(open(f"{travail}/session.json"))

def appel(methode, chemin, corps=None):
    donnees = json.dumps(corps).encode() if corps is not None else None
    requete = urllib.request.Request(f"{base}{chemin}", data=donnees, method=methode)
    requete.add_header("Accept", "application/json")
    if donnees is not None:
        requete.add_header("Content-Type", "application/json")
    requete.add_header("X-CSRF-Token", session["csrf"])
    requete.add_header("Cookie", session["cookie"])
    try:
        with urllib.request.urlopen(requete, timeout=10) as reponse:
            return reponse.status, json.loads(reponse.read().decode() or "null")
    except urllib.error.HTTPError as erreur:
        brut = erreur.read().decode()
        return erreur.code, (json.loads(brut) if brut else None)

statut, tache = appel("POST", "/api/v1/tasks", {
    "title": "Livrable exécuté par le connecteur",
    "description": "Produit par le connecteur Hermes en test bout en bout.",
    "priority": "HIGH",
    "assigned_agent_id": agent_id,
})
assert statut == 201, (statut, tache)
json.dump(tache, open(f"{travail}/tache_agent.json", "w"))
print(f"  tâche attribuée : {tache['id']} ({tache['status']})")
PY
verifier "$([ $? -eq 0 ] && echo 1 || echo 0)" "tâche attribuée à l'agent par l'administrateur"

echo
echo "=== 7. Connecteur : exécution réelle d'une tâche ==="
"$PYTHON" "$RACINE_N/connector/hermes_connector.py" run \
  --url "$BASE" --state "$ETAT_CONNECTEUR" --interval 1 --tours 2 \
  --commande 'printf "livrable produit pour %s" "$ORCHESTRATOR_TASK_TITLE"' 2>&1 | sed 's/^/  /'
verifier "$([ $? -eq 0 ] && echo 1 || echo 0)" "boucle du connecteur terminée sans erreur"

echo
echo "=== 8. Vérification de l'état réel côté serveur ==="
"$PYTHON" - "$BASE" "$TRAVAIL" "$AGENT_ID" <<'PY'
import json, sys, urllib.request, urllib.error

base, travail, agent_id = sys.argv[1], sys.argv[2], sys.argv[3]
session = json.load(open(f"{travail}/session.json"))
tache = json.load(open(f"{travail}/tache_agent.json"))

def lire(chemin):
    requete = urllib.request.Request(f"{base}{chemin}")
    requete.add_header("X-CSRF-Token", session["csrf"])
    requete.add_header("Cookie", session["cookie"])
    with urllib.request.urlopen(requete, timeout=10) as reponse:
        return json.loads(reponse.read().decode())

agent = lire(f"/api/v1/agents/{agent_id}")
detail = lire(f"/api/v1/tasks/{tache['id']}")
journal = lire("/api/v1/logs?page_size=100")

print(f"  agent          : {agent['status']} (dernier signal {agent['last_seen_at']})")
print(f"  tâche          : {detail['status']} (résultat présent : {detail['result'] is not None})")
if detail.get("result"):
    print(f"  sortie produite: {json.dumps(detail['result'].get('sortie', ''))[:90]}")

types = {item["event_type"] for item in journal["items"]}
attendus = {"agent.enrolled", "agent.heartbeat", "task.created", "task.assigned",
            "task.acked", "task.completed"}
manquants = attendus - types
print(f"  événements     : {len(types)} types distincts, manquants : {sorted(manquants) or 'aucun'}")

assert agent["status"] == "ONLINE", agent["status"]
assert detail["status"] == "COMPLETED", detail["status"]
assert detail["result"] and "livrable produit" in detail["result"]["sortie"]
assert not manquants, manquants
print("  => cycle complet vérifié côté serveur")
PY
verifier "$([ $? -eq 0 ] && echo 1 || echo 0)" "cycle complet : agent ONLINE, tâche COMPLETED avec résultat"

echo
echo "=== 9. Sans exécuteur configuré : la tâche est refusée, pas exécutée ==="
"$PYTHON" - "$BASE" "$TRAVAIL" "$AGENT_ID" <<'PY'
import json, sys, urllib.request, urllib.error
base, travail, agent_id = sys.argv[1], sys.argv[2], sys.argv[3]
session = json.load(open(f"{travail}/session.json"))

def appel(methode, chemin, corps=None):
    donnees = json.dumps(corps).encode() if corps is not None else None
    requete = urllib.request.Request(f"{base}{chemin}", data=donnees, method=methode)
    requete.add_header("Accept", "application/json")
    if donnees is not None:
        requete.add_header("Content-Type", "application/json")
    requete.add_header("X-CSRF-Token", session["csrf"])
    requete.add_header("Cookie", session["cookie"])
    try:
        with urllib.request.urlopen(requete, timeout=10) as reponse:
            return reponse.status, json.loads(reponse.read().decode() or "null")
    except urllib.error.HTTPError as erreur:
        brut = erreur.read().decode()
        return erreur.code, (json.loads(brut) if brut else None)

statut, tache = appel("POST", "/api/v1/tasks", {
    "title": "rm -rf / --no-preserve-root", "assigned_agent_id": agent_id})
assert statut == 201, (statut, tache)
json.dump(tache, open(f"{travail}/tache_refusee.json", "w"))
print(f"  tâche piégée créée : {tache['id']}")
PY
rm -f "$ETAT_CONNECTEUR.bak"
"$PYTHON" "$RACINE_N/connector/hermes_connector.py" run --url "$BASE" --state "$ETAT_CONNECTEUR" \
  --interval 1 --tours 2 2>&1 | sed 's/^/  /'
"$PYTHON" - "$BASE" "$TRAVAIL" <<'PY'
import json, sys, urllib.request
base, travail = sys.argv[1], sys.argv[2]
session = json.load(open(f"{travail}/session.json"))
tache = json.load(open(f"{travail}/tache_refusee.json"))
requete = urllib.request.Request(f"{base}/api/v1/tasks/{tache['id']}")
requete.add_header("X-CSRF-Token", session["csrf"])
requete.add_header("Cookie", session["cookie"])
with urllib.request.urlopen(requete, timeout=10) as reponse:
    detail = json.loads(reponse.read().decode())
print(f"  état de la tâche piégée : {detail['status']}")
print(f"  motif consigné           : {json.dumps(detail['result'], ensure_ascii=False)[:140]}")
assert detail["status"] == "FAILED", detail["status"]
assert "aucun exécuteur configuré" in json.dumps(detail["result"], ensure_ascii=False)
print("  => aucune commande distante exécutée sans exécuteur explicite")
PY
verifier "$([ $? -eq 0 ] && echo 1 || echo 0)" "tâche hostile non exécutée, marquée FAILED avec motif"

echo
echo "=== 10. Arrêt propre du service (journalisation de l'arrêt) ==="
kill -TERM "$PID_SERVEUR" 2>/dev/null || true
wait "$PID_SERVEUR" 2>/dev/null || true
PID_SERVEUR=""
verifier "1" "service arrêté proprement"
grep -c '"event_type"' "$TRAVAIL_SHELL/serveur.log" >/dev/null 2>&1 || true

echo
echo "============================================================"
echo "  Contrôles réussis : $reussis — échecs : $echoues"
echo "============================================================"
[ "$echoues" -eq 0 ]
