#!/bin/bash
# ============================================================================
#  BANC D'ESSAI du SUIVI DE COLIS de l'aiguilleur -- SANS toucher au vrai ouvrier.
#  Un FAUX ouvrier (faux-cage.sh) remplace claude : il dort N secondes si l'ordre
#  contient "SLEEP=N", puis repond. On lance le VRAI aiguilleur.sh modifie dessus,
#  via les variables AIG_* (qui n'existent QUE pour les tests).
#  But : prouver que rien n'est casse ET que le suivi de colis marche.
# ============================================================================
set -u
BASE=/tmp/aig-test
AIG_SRC=/mnt/c/Users/VOTRE_USER/claude-relay/vps/aiguilleur.sh

PASS=0; FAIL=0
ok(){ echo "  [ OK  ] $1"; PASS=$((PASS+1)); }
ko(){ echo "  [ECHEC] $1"; FAIL=$((FAIL+1)); }
statusnum(){ grep -oE 'COEUR : [0-9]+' "$1" 2>/dev/null | grep -oE '[0-9]+' | head -1; }

# --- Preparer un environnement de test propre -------------------------------
rm -rf "$BASE"; mkdir -p "$BASE/queue/in" "$BASE/queue/out" "$BASE/queue/done"
: > "$BASE/alertes.jsonl"
: > "$BASE/noop-format.py"

cat > "$BASE/faux-cage.sh" <<'FAUX'
#!/bin/bash
# Faux ouvrier : recoit "claude -p <ordre> ...". Dort N s si l'ordre contient SLEEP=N.
prompt=""
while [ $# -gt 0 ]; do
  case "$1" in
    -p) prompt="${2:-}"; shift 2;;
    *) shift;;
  esac
done
secs=$(printf '%s' "$prompt" | grep -oE 'SLEEP=[0-9]+' | head -1 | cut -d= -f2)
[ -z "$secs" ] && secs=0
sleep "$secs"
printf 'REPONSE-OUVRIER-FAUX apres %ss\n' "$secs"
FAUX
chmod +x "$BASE/faux-cage.sh"

# L'aiguilleur sous test (conversion CRLF -> LF)
sed 's/\r$//' "$AIG_SRC" > "$BASE/aiguilleur.sh"; chmod +x "$BASE/aiguilleur.sh"

# Variables de test communes (le vrai ouvrier/sudo/chemins prod ne sont PAS touches)
export AIG_QUEUE="$BASE/queue" AIG_CAGE="$BASE/faux-cage.sh" AIG_RUNAS="" \
       AIG_ALERTES="$BASE/alertes.jsonl" AIG_FORMAT="$BASE/noop-format.py" \
       AIG_HERMES_UID="$(id -u)" AIG_PCQUEUE="$BASE/pcqueue"

echo "============================================================"
echo " TEST 1 : ordre LONG -> le battement de coeur doit MONTER"
echo "============================================================"
echo "SLEEP=20 test heartbeat" > "$BASE/queue/in/colis-long"
export AIG_BEAT_TICKS=2          # battement accelere (toutes les 4s) pour un test rapide
bash "$BASE/aiguilleur.sh" &
AIGPID=$!
sleep 7
S="$BASE/queue/out/colis-long.status"
[ -f "$S" ] && ok "le .status existe pendant le travail" || ko "le .status devrait exister pendant le travail"
B1=$(statusnum "$S"); echo "    -> battement lu #1 : ${B1:-(rien)}"
sleep 8
B2=$(statusnum "$S"); echo "    -> battement lu #2 : ${B2:-(rien)}"
if [ -n "${B1:-}" ] && [ -n "${B2:-}" ] && [ "$B2" -gt "$B1" ]; then
  ok "le battement a AUGMENTE ($B1 -> $B2) = ouvrier vivant, visible par Hermes"
else
  ko "le battement aurait du augmenter (lu $B1 puis $B2)"
fi
wait "$AIGPID"
unset AIG_BEAT_TICKS
[ -f "$BASE/queue/out/colis-long.out" ] && ok "le .out final est livre" || ko "le .out final manque"
grep -q "REPONSE-OUVRIER-FAUX" "$BASE/queue/out/colis-long.out" 2>/dev/null \
  && ok "le .out contient bien la reponse de l'ouvrier" || ko "le .out ne contient pas la reponse"
[ ! -f "$S" ] && ok "le .status a disparu apres livraison (colis livre, pas de fantome)" || ko "le .status traine encore apres livraison"
[ -f "$BASE/queue/done/colis-long" ] && ok "l'ordre est archive dans done/" || ko "l'ordre n'est pas archive"

echo ""
echo "============================================================"
echo " TEST 2 : ordre COURT -> livraison immediate, aucun colis fantome"
echo "============================================================"
echo "SLEEP=0 reponse rapide" > "$BASE/queue/in/colis-court"
bash "$BASE/aiguilleur.sh"
[ -f "$BASE/queue/out/colis-court.out" ] && ok "le .out est livre" || ko "le .out manque"
[ ! -f "$BASE/queue/out/colis-court.status" ] && ok "aucun .status residuel" || ko "un .status fantome traine"

echo ""
echo "============================================================"
echo " TEST 3 : DEPASSEMENT DE DELAI -> message clair + nettoyage du suivi"
echo "============================================================"
echo "SLEEP=30 ordre trop long" > "$BASE/queue/in/colis-timeout"
export AIG_TIMEOUT=3
bash "$BASE/aiguilleur.sh"
unset AIG_TIMEOUT
OUT="$BASE/queue/out/colis-timeout.out"
grep -q "depassement du delai" "$OUT" 2>/dev/null && ok "message de depassement de delai present" || ko "message de depassement manquant"
[ ! -f "$BASE/queue/out/colis-timeout.status" ] && ok "le .status est nettoye meme apres un timeout" || ko "un .status fantome traine apres timeout"

echo ""
echo "============================================================"
echo " TEST 4 : NON-REGRESSION du routage @pc"
echo "============================================================"
echo "@pc fais quelque chose sur le PC" > "$BASE/queue/in/colis-pc"
bash "$BASE/aiguilleur.sh"
[ -f "$BASE/pcqueue/in/colis-pc" ] && ok "l'ordre @pc est route vers la file PC" || ko "l'ordre @pc n'a pas ete route"
[ ! -f "$BASE/queue/out/colis-pc.out" ] && ok "l'aiguilleur n'a pas traite l'ordre @pc lui-meme" || ko "l'aiguilleur a traite a tort l'ordre @pc"
[ ! -f "$BASE/queue/out/colis-pc.status" ] && ok "aucun .status cree pour un ordre route" || ko "un .status a ete cree a tort pour @pc"

echo ""
echo "============================================================"
echo " RESULTAT : $PASS reussis, $FAIL echoues"
echo "============================================================"
[ "$FAIL" -eq 0 ] && echo "TOUS_LES_TESTS_OK" || echo "DES_TESTS_ONT_ECHOUE"
