#!/bin/bash
# ============================================================================
#  BANC D'ESSAI du SAS HOSTINGER.
#  Le faux ouvrier depose deux demandes pare-feu dans .hostinger-request, puis
#  emet un flux minimal. On verifie que l'aiguilleur appelle le GUICHET (root)
#  pour chaque demande, joint le resultat au .out, et vide la demande ensuite.
#  Aucun appel reseau : le guichet est remplace par un faux qui journalise.
# ============================================================================
set -u
BASE=/tmp/hostinger-test
PASS=0; FAIL=0
ok(){ echo "  [ OK  ] $1"; PASS=$((PASS+1)); }
ko(){ echo "  [ECHEC] $1"; FAIL=$((FAIL+1)); }

rm -rf "$BASE"; mkdir -p "$BASE/queue/in" "$BASE/queue/out" "$BASE/queue/done" "$BASE/travaux"
: > "$BASE/alertes.jsonl"; : > "$BASE/noop.py"; : > "$BASE/guichet.log"
SRC=${SRC:-/c/Users/VOTRE_USER/claude-relay/vps}
sed 's/\r$//' "$SRC/aiguilleur.sh"  > "$BASE/aiguilleur.sh"
sed 's/\r$//' "$SRC/format-live.py" > "$BASE/format-live.py"

# faux guichet : journalise ses arguments (espaces normalises) et renvoie une ligne reconnaissable
cat > "$BASE/faux-guichet.sh" <<'GW'
#!/bin/bash
a=$(echo $*)            # collapse + trim des espaces (diag a des args vides)
echo "$a" >> "$HG_LOG"
echo "GUICHET[$a]"
GW
chmod +x "$BASE/faux-guichet.sh"

# faux cage/ouvrier : depose deux demandes pare-feu PUIS emet un flux minimal
cat > "$BASE/faux-cage.sh" <<'FAUX'
#!/bin/bash
printf '%s\n' 'diag' 'open 5349 udp' > "$AIG_TRAVAUX/.hostinger-request"
printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"Je verifie le pare-feu."}]}}'
printf '%s\n' '{"type":"result","subtype":"success","result":"REPONSE-OUVRIER"}'
FAUX
chmod +x "$BASE/faux-cage.sh"

export AIG_QUEUE="$BASE/queue" AIG_CAGE="$BASE/faux-cage.sh" AIG_RUNAS="" \
       AIG_ALERTES="$BASE/alertes.jsonl" AIG_FORMAT="$BASE/noop.py" \
       AIG_FORMATLIVE="$BASE/format-live.py" AIG_HERMES_UID="$(id -u)" \
       AIG_PCQUEUE="$BASE/pcqueue" AIG_TRAVAUX="$BASE/travaux" \
       AIG_ETATGW="" AIG_HOSTGW="$BASE/faux-guichet.sh" HG_LOG="$BASE/guichet.log"

echo "============================================================"
echo " L'ouvrier demande 'diag' + 'open 5349 udp' -> le guichet doit etre appele"
echo "============================================================"
echo "SLEEP=0 test hostinger" > "$BASE/queue/in/ordre-fw"
bash "$BASE/aiguilleur.sh"

O="$BASE/queue/out/ordre-fw.out"
echo "    --- .out produit ---"; sed 's/^/      /' "$O" 2>/dev/null
grep -q "=== SAS HOSTINGER" "$O" 2>/dev/null && ok "le .out contient la section SAS HOSTINGER" || ko "section SAS HOSTINGER absente"
grep -q "GUICHET\[diag\]" "$O" 2>/dev/null && ok "guichet appele pour 'diag'" || ko "appel 'diag' absent du .out"
grep -q "GUICHET\[open 5349 udp\]" "$O" 2>/dev/null && ok "guichet appele pour 'open 5349 udp'" || ko "appel 'open 5349 udp' absent"
grep -q "^diag$" "$BASE/guichet.log" 2>/dev/null && ok "journal guichet : diag recu avec les bons arguments" || ko "journal guichet sans diag"
grep -q "^open 5349 udp$" "$BASE/guichet.log" 2>/dev/null && ok "journal guichet : open 5349 udp recu avec les bons arguments" || ko "journal guichet sans open"
[ ! -s "$BASE/travaux/.hostinger-request" ] && ok "la demande est videe apres traitement (pas de rejeu)" || ko "demande pas videe"
[ -f "$BASE/queue/done/ordre-fw" ] && ok "ordre archive" || ko "ordre non archive"

echo ""
echo "============================================================"
echo " RESULTAT : $PASS reussis, $FAIL echoues"
echo "============================================================"
[ "$FAIL" -eq 0 ] && echo "TOUS_LES_TESTS_OK" || echo "DES_TESTS_ONT_ECHOUE"
