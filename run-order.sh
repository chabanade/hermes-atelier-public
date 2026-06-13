#!/bin/bash
# ============================================================================
#  LANCEUR OUVRIER PC (WSL)  --  recoit l'ordre en base64 ($1), le decode,
#  parse les tags @effort/@model en tete (poses par la salle de controle, defauts
#  injectes par le releveur), lance claude DANS LA CAGE (bubblewrap + videur) en
#  FLUX EN DIRECT (stream-json -> format-live), et renvoie la reponse finale sur
#  stdout (recuperee par le releveur poller.ps1).
# ============================================================================
ORDER=$(printf '%s' "$1" | base64 -d)
export ENABLE_CLAUDEAI_MCP_SERVERS=false
: > /home/ouvrier/journal/alertes.jsonl 2>/dev/null   # drapeaux vierges pour CET ordre (copilote.log conserve tout)
rm -f /home/ouvrier/travaux/.deploy-request 2>/dev/null
# Filet anti-faux-fige : on efface le drapeau de fin + le fichier resultat de
# l'ordre PRECEDENT. run-order les (re)cree en toute fin ; le releveur s'en sert
# pour savoir que l'ouvrier a REELLEMENT fini, meme si Windows ne detecte pas la
# sortie de wsl.exe (bug Start-Process/WSL qui faisait spinner le releveur a vide).
rm -f /home/ouvrier/journal/current.done /home/ouvrier/journal/current.result 2>/dev/null

# --- EFFORT + MODELE : epluchage des tags en tete, INSENSIBLE A L'ORDRE (meme
#     logique que l'aiguilleur VPS). @effort=low|medium|high|xhigh|max (ou @max),
#     @model=<id>. Le reste de la 1re ligne = la mission. xhigh/max = Opus only :
#     avec un modele non-Opus, on plafonne a high (sinon claude renvoie une 400).
NIV_EFFORT=""
MODELE_ID=""
n=0
while [ "$n" -lt 6 ]; do            # cap a 6 : garde-fou anti-boucle (2 tags max attendus)
  n=$((n + 1))
  PREMIERE=$(printf '%s' "$ORDER" | head -1)
  if printf '%s' "$PREMIERE" | grep -qiE '^@effort=(low|medium|high|xhigh|max)([[:space:]]|$)'; then
    NIV_EFFORT=$(printf '%s' "$PREMIERE" | sed -E '1s/^@effort=([A-Za-z]+).*/\1/' | tr 'A-Z' 'a-z')
    ORDER=$(printf '%s' "$ORDER" | sed -E '1s/^@effort=[A-Za-z]+[[:space:]]*//')
  elif printf '%s' "$PREMIERE" | grep -qiE '^@max([[:space:]]|$)'; then
    NIV_EFFORT="max"
    ORDER=$(printf '%s' "$ORDER" | sed '1s/^@max[[:space:]]*//')
  elif printf '%s' "$PREMIERE" | grep -qiE '^@model=[A-Za-z0-9._-]+([[:space:]]|$)'; then
    MODELE_ID=$(printf '%s' "$PREMIERE" | sed -E '1s/^@model=([A-Za-z0-9._-]+).*/\1/')
    ORDER=$(printf '%s' "$ORDER" | sed -E '1s/^@model=[A-Za-z0-9._-]+[[:space:]]*//')
  else
    break
  fi
done
if [ -n "$MODELE_ID" ] && ! printf '%s' "$MODELE_ID" | grep -qi '^claude-opus'; then
  case "$NIV_EFFORT" in xhigh|max) NIV_EFFORT="high";; esac
fi
EFFORT_FLAG=""; [ -n "$NIV_EFFORT" ] && EFFORT_FLAG="--effort $NIV_EFFORT"
MODELE_FLAG=""; [ -n "$MODELE_ID" ] && MODELE_FLAG="--model $MODELE_ID"

# --- ULTRACODE PAR DEFAUT (PC) : le releveur passe "$2=ultra" si l'interrupteur
#     de la salle est actif pour le PC. Si la mission ne demande pas deja
#     "ultracode", on prefixe le mot-cle -> mode multi-agents pour cet ordre.
if [ "$2" = "ultra" ] && ! printf '%s' "$ORDER" | head -1 | grep -qiw ultracode; then
  ORDER="ultracode $ORDER"
fi

# --- SUIVI EN DIRECT : l'ouvrier emet un flux (stream-json) ; format-live le
#     transforme au fur et a mesure en journal lisible -> current.live (le releveur
#     pousse ce fichier vers le VPS en .live-pc, lu par la salle). La reponse finale
#     va dans un fichier temporaire. On garde aussi le flux brut (current.raw) comme
#     filet : si pas de reponse finale (erreur, --model refuse...), on remonte le brut.
LIVE=/home/ouvrier/journal/current.live
RAW=/home/ouvrier/journal/current.raw
META=/home/ouvrier/journal/current.meta   # fiche telemetrie de CET ordre (cout/duree/tokens) ; le releveur la pousse vers le VPS
RES=$(mktemp)
: > "$RAW" 2>/dev/null
: > "$META" 2>/dev/null                    # ardoise vierge : pas de meta de l'ordre precedent si celui-ci est coupe avant la fin
timeout 3600 /home/ouvrier/cage-pc.sh claude -p "$ORDER" \
  --settings /home/ouvrier/hooks/ouvrier-settings.json \
  --permission-mode dontAsk $EFFORT_FLAG $MODELE_FLAG \
  --strict-mcp-config --mcp-config /home/ouvrier/hooks/empty-mcp.json \
  --output-format stream-json --verbose \
  --max-turns 120 </dev/null 2>&1 \
  | tee "$RAW" \
  | python3 /home/ouvrier/format-live.py "$LIVE" "$RES" 0 "$META" "$MODELE_ID" "$NIV_EFFORT" pc

RESULT=$(cat "$RES" 2>/dev/null)
rm -f "$RES"
if [ -z "$RESULT" ]; then
  RESULT="[ouvrier PC] (pas de reponse finale renvoyee) -- dernieres lignes brutes du flux :
$(tail -c 1500 "$RAW" 2>/dev/null)"
fi
# Filet : on ecrit la reponse dans un FICHIER (pas seulement sur stdout). Le
# releveur le lira si Windows n'a pas detecte la fin de wsl.exe -- ainsi le
# resultat n'est jamais perdu meme quand le canal stdout reste "ouvert" cote Windows.
printf '%s' "$RESULT" > /home/ouvrier/journal/current.result 2>/dev/null
printf '%s' "$RESULT"

# --- L'ouvrier a-t-il demande un deploiement ? -> on le lance HORS cage (podman rootless) ---
REQ=/home/ouvrier/travaux/.deploy-request
if [ -s "$REQ" ]; then
  /home/ouvrier/deploy-gateway.sh "$(head -1 "$REQ")" 2>&1
  rm -f "$REQ"
fi

# Drapeau de fin : DERNIERE action de l'ouvrier. Le releveur guette ce fichier ;
# des qu'il apparait, l'ordre est REELLEMENT termine -> il sort de l'attente et
# livre le resultat, sans attendre un signal de fin de process que Windows ne
# donne pas toujours (cause des faux "fige 75 min").
: > /home/ouvrier/journal/current.done 2>/dev/null
