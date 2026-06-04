#!/bin/bash
# ============================================================================
#  HOSTINGER-GATEWAY (VPS)  --  le "guichet pare-feu" : l'ouvrier demande une
#  action pare-feu Hostinger, le guichet (root) la realise via l'API OFFICIELLE
#  d'Hostinger, et RIEN d'autre. Le jeton ne sort jamais, ne passe ni par la
#  cage, ni par le chat, ni par Telegram.
# ----------------------------------------------------------------------------
#  Meme philosophie que root-gateway : l'ouvrier (sans root, sans jeton) ecrit
#  une DEMANDE simple dans ~/travaux/.hostinger-request (une action par ligne) ;
#  l'aiguilleur (root, hors cage) appelle ce guichet pour chaque ligne. Le jeton
#  vit dans un coffre root-only que seul ce guichet peut lire.
#
#  SECURITE (anti-lockout) : chez Hostinger, un pare-feu attache met "tout bloque
#  par defaut" -> risque de couper le SSH. Donc ce guichet :
#    - n'AJOUTE que des regles "accept" a un pare-feu DEJA attache au VPS ;
#    - REFUSE de creer/attacher (activate) un pare-feu de zero (= GO humain) ;
#    - verifie que le SSH (22) reste autorise avant d'agir ;
#    - si AUCUN pare-feu n'est attache : il le DIT (= le port n'est PAS bloque
#      cote Hostinger, le souci est ailleurs) au lieu d'en creer un dangereux.
#
#  Schema reel de l'API (verifie 2026-06-04) :
#    GET /api/vps/v1/virtual-machines      -> [ {id, hostname, state, firewall_group_id, ...}, ... ]
#    GET /api/vps/v1/firewall              -> { data:[ {id, name, is_synced, rules}, ... ] } (ou array)
#    GET /api/vps/v1/firewall/{id}         -> { id, name, is_synced, rules:[{id,action,protocol,port,source,source_detail}] }
#    POST /api/vps/v1/firewall/{id}/rules  -> ajoute une regle
#    DELETE .../rules/{ruleId}             -> retire une regle
#    POST .../sync/{vmId}                  -> applique le pare-feu au VPS
#    (un VPS est "attache" a un pare-feu quand son firewall_group_id == l'id du pare-feu)
#
#  Usage :   hostinger-gateway.sh diag
#            hostinger-gateway.sh open  <port> <tcp|udp>
#            hostinger-gateway.sh close <port> <tcp|udp>
# ============================================================================
set -u

HOST=${HG_HOST:-https://developers.hostinger.com}   # base API confirmee
TOKENFILE=${HG_TOKEN:-/root/.secrets/hostinger-api.token}
JOURNAL=${HG_JOURNAL:-/home/ouvrier/journal/hostinger-gateway.log}
VMID=${HG_VMID:-}                 # si vide -> auto-decouverte par hostname
VPS_MATCH=${HG_VPS_MATCH:-VOTRE_ID_VPS}   # quel VPS cibler : hostname contenant ceci (le VPS atelier)
TIMEOUT=${HG_TIMEOUT:-25}

log(){ printf '%s\t%s\n' "$(date -Is)" "$1" >> "$JOURNAL" 2>/dev/null; }
have_jq(){ command -v jq >/dev/null 2>&1; }

VERB="${1:-diag}"; PORT="${2:-}"; PROTO_IN="${3:-tcp}"
PROTO=$(printf '%s' "$PROTO_IN" | tr '[:lower:]' '[:upper:]')   # TCP / UDP

# --- jeton present et lisible (root only) -----------------------------------
if [ ! -r "$TOKENFILE" ]; then
  echo "[hostinger] Aucun jeton lisible dans $TOKENFILE."
  echo "            -> Mehdi doit l'y deposer (une seule fois). Rien d'autre a faire."
  log "NO-TOKEN $TOKENFILE"; exit 1
fi
TOKEN=$(tr -d ' \t\r\n' < "$TOKENFILE")
[ -n "$TOKEN" ] || { echo "[hostinger] Le coffre jeton est vide."; log "EMPTY-TOKEN"; exit 1; }

# --- appel API (le jeton ne s'affiche jamais) -------------------------------
api(){ # method path [json] -> corps + derniere ligne HTTPSTATUS:NNN
  local m="$1" p="$2" d="${3:-}"
  if [ -n "$d" ]; then
    curl -s -m "$TIMEOUT" -w '\nHTTPSTATUS:%{http_code}' -X "$m" "$HOST$p" \
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$d"
  else
    curl -s -m "$TIMEOUT" -w '\nHTTPSTATUS:%{http_code}' -X "$m" "$HOST$p" \
      -H "Authorization: Bearer $TOKEN"
  fi
}
http_code(){ printf '%s' "$1" | sed -n 's/^HTTPSTATUS:\([0-9]*\)$/\1/p' | tail -1; }
http_body(){ printf '%s' "$1" | sed '$ { /^HTTPSTATUS:/d; }'; }

# normalise une reponse "liste" en tableau jq, qu'elle soit [..] ou {data:[..]}
JQ_ARR='(if type=="array" then . else (.data // []) end)'

# --- decouverte du VPS cible (par hostname) ---------------------------------
decouvrir_vmid(){
  local r b c id
  r=$(api GET /api/vps/v1/virtual-machines); c=$(http_code "$r"); b=$(http_body "$r")
  [ "$c" = 200 ] || { echo "[hostinger] Liste VPS : HTTP $c (jeton ? base URL ?)" >&2; log "VM-LIST HTTP $c : $b"; return 1; }
  id=$(printf '%s' "$b" | jq -r --arg m "$VPS_MATCH" "$JQ_ARR | map(select((.hostname//\"\")|test(\$m))) | .[0].id // empty" 2>/dev/null)
  [ -z "$id" ] && id=$(printf '%s' "$b" | jq -r "$JQ_ARR | .[0].id // empty" 2>/dev/null)
  [ -n "$id" ] && { printf '%s' "$id"; return 0; }
  echo "[hostinger] Identifiant VPS introuvable." >&2; log "VM-ID introuvable : $b"; return 1
}

# --- pare-feu attache a un VPS (via firewall_group_id), ou vide --------------
firewall_attache(){
  local vmid="$1" r b c
  r=$(api GET /api/vps/v1/virtual-machines); c=$(http_code "$r"); b=$(http_body "$r")
  [ "$c" = 200 ] || { log "FW-ATT HTTP $c : $b"; return 1; }
  printf '%s' "$b" | jq -r --arg vm "$vmid" "$JQ_ARR | map(select((.id|tostring)==\$vm)) | .[0].firewall_group_id // empty" 2>/dev/null
}

# ============================================================================
#  DIAG  -- 100% non destructif : montre la verite (port bloque ou non).
# ============================================================================
if [ "$VERB" = "diag" ]; then
  have_jq || { echo "[hostinger] 'jq' requis pour diag."; exit 1; }
  echo "===================== DIAG HOSTINGER ====================="
  echo "Base API : $HOST"
  R=$(api GET /api/vps/v1/virtual-machines); C=$(http_code "$R"); VMS=$(http_body "$R")
  echo "VPS (HTTP $C) :"
  printf '%s' "$VMS" | jq -r "$JQ_ARR[] | \"  - id=\(.id)  \(.hostname)  etat=\(.state)  pare-feu attache=\(.firewall_group_id // \"AUCUN\")\"" 2>/dev/null
  R=$(api GET /api/vps/v1/firewall); C=$(http_code "$R"); FWS=$(http_body "$R")
  echo "Pare-feux (HTTP $C) :"
  NB=$(printf '%s' "$FWS" | jq -r "$JQ_ARR | length" 2>/dev/null)
  if [ "${NB:-0}" = 0 ]; then
    echo "  (AUCUN pare-feu) => tout est ouvert cote Hostinger."
  else
    printf '%s' "$FWS" | jq -r "$JQ_ARR[] | \"  - fw id=\(.id) nom=\(.name) synchronise=\(.is_synced) regles=\((.rules//[])|length)\"" 2>/dev/null
  fi
  # Verdict pour le VPS cible
  VID=$(decouvrir_vmid 2>/dev/null)
  if [ -n "$VID" ]; then
    ATT=$(firewall_attache "$VID")
    echo "--- VERDICT pour ton atelier (VPS $VID) ---"
    if [ -z "$ATT" ]; then
      echo "  AUCUN pare-feu attache => Hostinger ne bloque RIEN. Un port 'ferme'"
      echo "  ne l'est PAS ici : le souci serait cote serveur (ufw / le service)."
    else
      echo "  Pare-feu attache : $ATT (deny-all par defaut). Seuls les ports listes passent."
      echo "  -> pour ouvrir un port : hostinger-gateway.sh open <port> <tcp|udp>"
    fi
  fi
  echo "=========================================================="
  log "DIAG ok (vps=$VID att=${ATT:-aucun})"
  exit 0
fi

# ============================================================================
#  OPEN / CLOSE  -- ajoute / retire une regle "accept" sur le pare-feu ATTACHE.
# ============================================================================
if [ "$VERB" != "open" ] && [ "$VERB" != "close" ]; then
  echo "[hostinger] Action inconnue : '$VERB' (attendu : diag | open | close)"; exit 1
fi
case "$PORT" in (''|*[!0-9]*) echo "[hostinger] Port invalide : '$PORT'"; exit 1;; esac
[ "$PROTO" = TCP ] || [ "$PROTO" = UDP ] || { echo "[hostinger] Protocole : tcp ou udp seulement."; exit 1; }
have_jq || { echo "[hostinger] 'jq' requis (l'ouvrier peut l'installer via le sas root : apt-get install -y jq)."; log "NO-JQ"; exit 1; }

[ -n "$VMID" ] || VMID=$(decouvrir_vmid) || exit 1
FWID=$(firewall_attache "$VMID")
if [ -z "$FWID" ] || [ "$FWID" = null ]; then
  echo "[hostinger] AUCUN pare-feu Hostinger attache au VPS $VMID."
  echo "            => le port $PORT/$PROTO n'est donc PAS bloque cote Hostinger."
  echo "            Le souci est ailleurs (ufw local, ou le service lui-meme)."
  echo "            (Creer + attacher un pare-feu de zero = 'tout bloque' = risque de"
  echo "             couper le SSH : ca demande un GO explicite de Mehdi.)"
  log "NO-FW-ATTACHED vm=$VMID port=$PORT/$PROTO"
  exit 3
fi

# Regles actuelles du pare-feu attache
R=$(api GET "/api/vps/v1/firewall/$FWID"); C=$(http_code "$R"); FW=$(http_body "$R")
[ "$C" = 200 ] || { echo "[hostinger] Lecture du pare-feu $FWID : HTTP $C"; log "FW-GET $FWID HTTP $C : $FW"; exit 1; }

# Garde-fou anti-lockout : le SSH (22/TCP) doit rester autorise.
SSH_OK=$(printf '%s' "$FW" | jq -r '((.rules // [])|any((.port|tostring)=="22")) // false' 2>/dev/null)
if [ "$SSH_OK" != "true" ]; then
  echo "[hostinger] PRUDENCE : aucune regle SSH (22) visible dans ce pare-feu."
  echo "            Pour ne pas risquer de te couper l'acces, je m'arrete ici."
  log "SSH-GUARD vm=$VMID fw=$FWID : pas de 22, abort $VERB $PORT/$PROTO"; exit 4
fi

# La regle visee existe-t-elle deja ?
RULE_ID=$(printf '%s' "$FW" | jq -r --arg p "$PORT" --arg pr "$PROTO" \
  '(.rules // [])[] | select(((.port|tostring)==$p) and ((.protocol|ascii_upcase)==$pr)) | (.id|tostring)' 2>/dev/null | head -1)

if [ "$VERB" = "open" ]; then
  if [ -n "$RULE_ID" ]; then
    echo "[hostinger] Deja ouvert : $PORT/$PROTO (regle $RULE_ID). Rien a faire."
    log "OPEN-NOOP vm=$VMID fw=$FWID $PORT/$PROTO"; exit 0
  fi
  BODY=$(printf '{"action":"accept","protocol":"%s","port":"%s","source":"custom","source_detail":"0.0.0.0/0"}' "$PROTO" "$PORT")
  R=$(api POST "/api/vps/v1/firewall/$FWID/rules" "$BODY"); C=$(http_code "$R"); B2=$(http_body "$R")
  if [ "$C" = 200 ] || [ "$C" = 201 ]; then
    api POST "/api/vps/v1/firewall/$FWID/sync/$VMID" >/dev/null 2>&1   # applique au VPS
    echo "[hostinger] OK : port $PORT/$PROTO OUVERT sur le pare-feu $FWID (VPS $VMID)."
    log "OPEN-OK vm=$VMID fw=$FWID $PORT/$PROTO"; exit 0
  fi
  echo "[hostinger] Echec ouverture $PORT/$PROTO : HTTP $C -- $(printf '%s' "$B2" | head -c 400)"
  log "OPEN-FAIL vm=$VMID fw=$FWID $PORT/$PROTO HTTP $C : $B2"; exit 1
fi

if [ "$VERB" = "close" ]; then
  if [ -z "$RULE_ID" ]; then
    echo "[hostinger] Rien a fermer : aucune regle $PORT/$PROTO trouvee."
    log "CLOSE-NOOP vm=$VMID fw=$FWID $PORT/$PROTO"; exit 0
  fi
  R=$(api DELETE "/api/vps/v1/firewall/$FWID/rules/$RULE_ID"); C=$(http_code "$R"); B2=$(http_body "$R")
  if [ "$C" = 200 ] || [ "$C" = 204 ]; then
    api POST "/api/vps/v1/firewall/$FWID/sync/$VMID" >/dev/null 2>&1
    echo "[hostinger] OK : port $PORT/$PROTO FERME (regle $RULE_ID retiree)."
    log "CLOSE-OK vm=$VMID fw=$FWID $PORT/$PROTO"; exit 0
  fi
  echo "[hostinger] Echec fermeture $PORT/$PROTO : HTTP $C -- $(printf '%s' "$B2" | head -c 300)"
  log "CLOSE-FAIL vm=$VMID fw=$FWID $PORT/$PROTO HTTP $C : $B2"; exit 1
fi
