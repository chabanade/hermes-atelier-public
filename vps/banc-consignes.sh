#!/bin/bash
# ============================================================================
#  BANC D'ESSAI du CANAL PATRON (consignes injectees en tete de chaque mission).
#  Le faux ouvrier capture le PROMPT qu'il recoit. On verifie :
#   - avec consignes : le prompt commence par les consignes du patron PUIS la mission
#   - sans consignes (fichier vide) : le prompt = la mission seule (pas de pollution)
# ============================================================================
set -u
BASE=/tmp/consignes-test
PASS=0; FAIL=0
ok(){ echo "  [ OK  ] $1"; PASS=$((PASS+1)); }
ko(){ echo "  [ECHEC] $1"; FAIL=$((FAIL+1)); }
SRC=${SRC:-/c/Users/VOTRE_USER/claude-relay/vps}

rm -rf "$BASE"; mkdir -p "$BASE/queue/in" "$BASE/queue/out" "$BASE/queue/done" "$BASE/travaux"
: > "$BASE/alertes.jsonl"; : > "$BASE/noop.py"
sed 's/\r$//' "$SRC/aiguilleur.sh"  > "$BASE/aiguilleur.sh"
sed 's/\r$//' "$SRC/format-live.py" > "$BASE/format-live.py"

# faux ouvrier : capture le prompt recu (-p) dans un fichier, puis repond
cat > "$BASE/faux-cage.sh" <<'FAUX'
#!/bin/bash
prompt=""
while [ $# -gt 0 ]; do case "$1" in -p) prompt="${2:-}"; shift 2;; *) shift;; esac; done
printf '%s' "$prompt" > "$CAPTURE"
printf '%s\n' '{"type":"result","subtype":"success","result":"OK"}'
FAUX
chmod +x "$BASE/faux-cage.sh"

export AIG_QUEUE="$BASE/queue" AIG_CAGE="$BASE/faux-cage.sh" AIG_RUNAS="" \
       AIG_ALERTES="$BASE/alertes.jsonl" AIG_FORMAT="$BASE/noop.py" \
       AIG_FORMATLIVE="$BASE/format-live.py" AIG_HERMES_UID="$(id -u)" \
       AIG_PCQUEUE="$BASE/pcqueue" AIG_TRAVAUX="$BASE/travaux" AIG_ETATGW="" \
       AIG_CONSIGNES="$BASE/consignes.txt" CAPTURE="$BASE/capture.txt"

echo "============================================================"
echo " 1) AVEC consignes -> doivent etre injectees AVANT la mission"
echo "============================================================"
printf '%s\n' "Toujours m'expliquer sans jargon." "Ne jamais supprimer sans demander." > "$BASE/consignes.txt"
echo "Repare le bouton ENVOYER du site" > "$BASE/queue/in/ordre-1"
bash "$BASE/aiguilleur.sh"
CAP=$(cat "$BASE/capture.txt" 2>/dev/null)
echo "    --- prompt recu par l'ouvrier ---"; printf '%s\n' "$CAP" | sed 's/^/      /'
printf '%s' "$CAP" | grep -q "CONSIGNES PERMANENTES DU PATRON" && ok "l'en-tete consignes patron est present" || ko "en-tete consignes absent"
printf '%s' "$CAP" | grep -q "sans jargon" && ok "la consigne de Mehdi est dans le prompt" || ko "consigne absente"
printf '%s' "$CAP" | grep -q "Repare le bouton ENVOYER" && ok "la mission est bien la, APRES les consignes" || ko "mission absente"
# l'ordre : consignes AVANT mission
if printf '%s' "$CAP" | awk '/CONSIGNES PERMANENTES/{c=NR} /Repare le bouton/{m=NR} END{exit !(c>0 && m>c)}'; then ok "ordre correct : consignes puis mission"; else ko "ordre incorrect"; fi

echo ""
echo "============================================================"
echo " 2) SANS consignes (fichier vide) -> mission SEULE, pas de pollution"
echo "============================================================"
: > "$BASE/consignes.txt"; : > "$BASE/capture.txt"
echo "Analyse le fichier X" > "$BASE/queue/in/ordre-2"
bash "$BASE/aiguilleur.sh"
CAP2=$(cat "$BASE/capture.txt" 2>/dev/null)
printf '%s' "$CAP2" | grep -q "CONSIGNES PERMANENTES" && ko "en-tete injecte a tort alors que vide" || ok "aucune injection quand le fichier est vide"
printf '%s' "$CAP2" | grep -q "Analyse le fichier X" && ok "la mission seule est passee" || ko "mission absente"

echo ""
echo "============================================================"
echo " RESULTAT : $PASS reussis, $FAIL echoues"
echo "============================================================"
[ "$FAIL" -eq 0 ] && echo "TOUS_LES_TESTS_OK" || echo "DES_TESTS_ONT_ECHOUE"
