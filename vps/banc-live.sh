#!/bin/bash
# ============================================================================
#  BANC D'ESSAI du SUIVI EN DIRECT (.live).
#  Le faux ouvrier emet un flux stream-json : 2 actions AVANT un sleep, puis le
#  resultat APRES. On verifie que le .live contient deja les 1eres actions PENDANT
#  le travail (= temps reel), puis que le .out final est correct et le .live nettoye.
# ============================================================================
set -u
BASE=/tmp/live-test
PASS=0; FAIL=0
ok(){ echo "  [ OK  ] $1"; PASS=$((PASS+1)); }
ko(){ echo "  [ECHEC] $1"; FAIL=$((FAIL+1)); }

rm -rf "$BASE"; mkdir -p "$BASE/queue/in" "$BASE/queue/out" "$BASE/queue/done"
: > "$BASE/alertes.jsonl"; : > "$BASE/noop.py"
sed 's/\r$//' /mnt/c/Users/VOTRE_USER/claude-relay/vps/aiguilleur.sh  > "$BASE/aiguilleur.sh"
sed 's/\r$//' /mnt/c/Users/VOTRE_USER/claude-relay/vps/format-live.py > "$BASE/format-live.py"

cat > "$BASE/faux-cage.sh" <<'FAUX'
#!/bin/bash
prompt=""
while [ $# -gt 0 ]; do case "$1" in -p) prompt="${2:-}"; shift 2;; *) shift;; esac; done
secs=$(printf '%s' "$prompt" | grep -oE 'SLEEP=[0-9]+' | head -1 | cut -d= -f2); [ -z "$secs" ] && secs=0
printf '%s\n' '{"type":"system","subtype":"init"}'
printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"Je commence le travail."}]}}'
printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"echo etape-un"}}]}}'
sleep "$secs"
printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"/projet/server.py"}}]}}'
printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"REPONSE-OUVRIER-FAUX"}]}}'
printf '%s\n' '{"type":"result","subtype":"success","result":"REPONSE-OUVRIER-FAUX"}'
FAUX
chmod +x "$BASE/faux-cage.sh"

export AIG_QUEUE="$BASE/queue" AIG_CAGE="$BASE/faux-cage.sh" AIG_RUNAS="" \
       AIG_ALERTES="$BASE/alertes.jsonl" AIG_FORMAT="$BASE/noop.py" \
       AIG_FORMATLIVE="$BASE/format-live.py" AIG_HERMES_UID="$(id -u)" \
       AIG_PCQUEUE="$BASE/pcqueue" AIG_TRAVAUX="$BASE/travaux"

echo "============================================================"
echo " Ordre LONG -> le .live doit se remplir PENDANT le travail"
echo "============================================================"
echo "SLEEP=8 test live" > "$BASE/queue/in/colis-live"
bash "$BASE/aiguilleur.sh" &
AIGPID=$!
sleep 3   # le faux ouvrier a emis ses 2 premieres actions, puis dort 8s
L="$BASE/queue/out/colis-live.live"
[ -f "$L" ] && ok "le .live existe PENDANT le travail" || ko "pas de .live pendant le travail"
echo "    --- contenu du .live a t+3s (pendant le sleep) ---"
sed 's/^/      /' "$L" 2>/dev/null
grep -q "Je commence le travail" "$L" 2>/dev/null && ok "le .live montre le MESSAGE de l'ouvrier en direct" || ko "message ouvrier absent du .live"
grep -q "echo etape-un" "$L" 2>/dev/null && ok "le .live montre la COMMANDE en direct (avant la fin)" || ko "commande absente du .live"
grep -q "REPONSE-OUVRIER-FAUX" "$L" 2>/dev/null && ko "le .live contient deja le resultat final AVANT le sleep (pas temps reel)" || ok "le .live ne contient PAS encore la fin (preuve : c'est bien du direct)"
wait "$AIGPID"
echo "    --- apres la fin ---"
O="$BASE/queue/out/colis-live.out"
grep -q "REPONSE-OUVRIER-FAUX" "$O" 2>/dev/null && ok "le .out final contient la reponse de l'ouvrier" || ko ".out final incorrect"
[ ! -f "$L" ] && ok "le .live est nettoye apres livraison" || ko "le .live traine apres livraison"
[ -f "$BASE/queue/done/colis-live" ] && ok "ordre archive" || ko "ordre non archive"

echo ""
echo "============================================================"
echo " RESULTAT : $PASS reussis, $FAIL echoues"
echo "============================================================"
[ "$FAIL" -eq 0 ] && echo "TOUS_LES_TESTS_OK" || echo "DES_TESTS_ONT_ECHOUE"
