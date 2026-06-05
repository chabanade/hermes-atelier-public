#!/bin/bash
# ============================================================================
#  GARDE DE NUIT  --  la veille PROACTIVE d'Hermes (façon JARVIS).
# ============================================================================
#  Surveille le serveur en continu et PREVIENT Mehdi sur Telegram TOUT SEUL :
#    - un service qui tombe (ou qui revient)        -> alerte sur CHANGEMENT
#    - une echeance qui approche (certificat HTTPS)  -> alerte (1x/jour max)
#    - le serveur qui sature (disque)                -> alerte (1x/jour max)
#    - l'ouvrier coince (tourne trop longtemps)      -> alerte sur CHANGEMENT
#    - un BRIEFING court le matin (08h) et le soir (20h), heure de Paris
#
#  Anti-spam : on n'alerte que sur TRANSITION (etat qui change), via un petit
#  fichier d'etat. Un service tombe = 1 seul message, pas un toutes les 5 min.
#
#  Le jeton du bot Telegram est lu depuis /root/.hermes/.env et n'est JAMAIS
#  affiche ni journalise. Tourne en root via un timer systemd.
#
#  Usage :  garde-nuit.sh check          (passe de surveillance + briefing si l'heure)
#           garde-nuit.sh brief matin    (envoie un briefing maintenant)
#           garde-nuit.sh brief soir
#           GN_DRYRUN=1 garde-nuit.sh ... (n'envoie rien, affiche -> pour tester)
# ============================================================================
set -u

ENVFILE=${GN_ENV:-/root/.hermes/.env}
CHAT=${GN_CHAT:-VOTRE_CHAT_ID_TELEGRAM}
STATE=${GN_STATE:-/root/.hermes/garde-nuit.state}
JOURNAL=${GN_JOURNAL:-/home/ouvrier/journal/garde-nuit.log}
DOMAINE=${GN_DOMAINE:-votre-domaine.example}
QUEUE=${GN_QUEUE:-/root/.hermes/claude-queue}
SERVICES=${GN_SERVICES:-aiguilleur webrtc-vocal webrtc-reply coturn caddy}
DISK_MAX=${GN_DISK_MAX:-85}                 # % disque au-dela duquel on alerte
CERT_MIN_J=${GN_CERT_MIN_J:-14}             # alerte si le cert expire dans moins de N jours
OUVRIER_MAX_MIN=${GN_OUVRIER_MAX_MIN:-70}   # ouvrier tournant depuis plus de N min = possible blocage
DRYRUN=${GN_DRYRUN:-0}                       # 1 = n'envoie rien, affiche

log(){ printf '%s\t%s\n' "$(date -Is)" "$1" >> "$JOURNAL" 2>/dev/null; }

# --- jeton (reste en variable, jamais affiche) ------------------------------
TOKEN=""
if [ -r "$ENVFILE" ]; then
  TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENVFILE" | head -1 | cut -d= -f2- | tr -d "\"' \r\n")
fi

# --- envoi Telegram ---------------------------------------------------------
envoyer(){   # $1 = texte
  local txt="$1" code
  if [ "$DRYRUN" = 1 ]; then
    echo "===== [DRYRUN] message Telegram ====="; printf '%s\n' "$txt"; echo "====================================="
    return 0
  fi
  if [ -z "$TOKEN" ]; then log "ENVOI IMPOSSIBLE (pas de jeton)"; return 1; fi
  code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' \
        "https://api.telegram.org/bot$TOKEN/sendMessage" \
        --data-urlencode "chat_id=$CHAT" \
        --data-urlencode "text=$txt")
  log "ENVOI Telegram HTTP $code"
  [ "$code" = 200 ]
}

# --- helpers etat -----------------------------------------------------------
OLD=$(cat "$STATE" 2>/dev/null || true)
oget(){ printf '%s\n' "$OLD" | grep -E "^$1=" | head -1 | cut -d= -f2-; }

PARIS_DATE=$(TZ=Europe/Paris date +%Y-%m-%d)
PARIS_H=$(TZ=Europe/Paris date +%H)

# Mesures partagees (servent aux alertes ET au briefing)
USEP=$(df --output=pcent / 2>/dev/null | tail -1 | tr -dc '0-9'); [ -n "$USEP" ] || USEP=0
CERT_FIN=$(echo | timeout 8 openssl s_client -connect "$DOMAINE:443" -servername "$DOMAINE" 2>/dev/null \
             | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
CERT_J=""
if [ -n "$CERT_FIN" ]; then
  ee=$(date -d "$CERT_FIN" +%s 2>/dev/null); now=$(date +%s)
  [ -n "$ee" ] && CERT_J=$(( (ee - now) / 86400 ))
fi

etat_service(){ local r; r=$(systemctl is-active "$1" 2>/dev/null); printf '%s' "${r:-inconnu}"; }

# --- BRIEFING ---------------------------------------------------------------
construire_briefing(){   # $1 = "matin"|"soir"
  local quand="$1" s cur txt nday enatt ouvrier
  txt="🌙 Garde de nuit — briefing du $quand ($(TZ=Europe/Paris date '+%d/%m %H:%M'))"$'\n'
  txt="$txt"$'\n'"Services :"$'\n'
  for s in $SERVICES; do
    cur=$(etat_service "$s")
    if [ "$cur" = active ]; then txt="$txt✅ $s"$'\n'; else txt="$txt❌ $s ($cur)"$'\n'; fi
  done
  nday=$(find "$QUEUE/done" -type f -newermt "$PARIS_DATE 00:00:00" 2>/dev/null | wc -l | tr -d ' ')
  enatt=$(ls "$QUEUE/in" 2>/dev/null | wc -l | tr -d ' ')
  pgrep -u ouvrier -f claude >/dev/null 2>&1 && ouvrier="occupé" || ouvrier="libre"
  txt="$txt"$'\n'"Ouvrier : $ouvrier — $nday ordre(s) traités aujourd'hui, $enatt en attente."$'\n'
  [ -n "$CERT_J" ] && txt="$txt""Certificat HTTPS : $CERT_J j restants."$'\n'
  txt="$txt""Disque : ${USEP}% utilisé."
  printf '%s' "$txt"
}

VERB="${1:-check}"
if [ "$VERB" = "brief" ]; then
  envoyer "$(construire_briefing "${2:-}")"
  exit 0
fi

# ============================================================================
#  CHECK : passe de surveillance (alertes sur transition) + briefing si l'heure
# ============================================================================
declare -A SVCNEW
ALERTES=""
addalerte(){ ALERTES="$ALERTES$1"$'\n'; }

# 1) services (transition)
for s in $SERVICES; do
  cur=$(etat_service "$s")
  prev=$(oget "svc_$s"); [ -n "$prev" ] || prev=active   # 1er run : on suppose "ok" sauf si reellement down
  SVCNEW[$s]="$cur"
  if [ "$cur" != "$prev" ]; then
    if [ "$cur" = active ]; then addalerte "✅ Le service \"$s\" est revenu en ligne."
    else addalerte "⚠️ Le service \"$s\" est TOMBÉ (état : $cur). Je surveille."; fi
  fi
done

# 2) disque (1x/jour max tant que sature)
da=$(oget disk_alert); NEW_disk_alert="$da"
if [ "$USEP" -ge "$DISK_MAX" ]; then
  [ "$da" != "$PARIS_DATE" ] && addalerte "⚠️ Le disque du serveur est à ${USEP}% — ça commence à saturer."
  NEW_disk_alert="$PARIS_DATE"
else
  NEW_disk_alert=""
fi

# 3) certificat HTTPS (1x/jour max si proche)
ca=$(oget cert_alert); NEW_cert_alert="$ca"
if [ -n "$CERT_J" ] && [ "$CERT_J" -lt "$CERT_MIN_J" ]; then
  [ "$ca" != "$PARIS_DATE" ] && addalerte "⚠️ Le certificat HTTPS expire dans $CERT_J jour(s)."
  NEW_cert_alert="$PARIS_DATE"
else
  NEW_cert_alert=""
fi

# 4) ouvrier coince (transition)
ovieux=$(pgrep -u ouvrier -f claude -o 2>/dev/null || true)
nstuck=0
if [ -n "$ovieux" ]; then
  age=$(ps -o etimes= -p "$ovieux" 2>/dev/null | tr -d ' ')
  [ -n "$age" ] && [ "$age" -gt $((OUVRIER_MAX_MIN*60)) ] && nstuck=1
fi
ostuck=$(oget ouvrier_stuck); [ -n "$ostuck" ] || ostuck=0
[ "$nstuck" = 1 ] && [ "$ostuck" != 1 ] && addalerte "⚠️ L'ouvrier tourne depuis plus de ${OUVRIER_MAX_MIN} min sur la même tâche — possible blocage."

# 5) certifs metier (echeances listees dans un fichier ; 1x/jour max si une approche/expire)
CERTIF_FILE=${GN_CERTIF_FILE:-/root/.hermes/certifs-metier.txt}
CERTIF_ALERT_J=${GN_CERTIF_ALERT_J:-60}     # commence a alerter N jours avant l'echeance
cma=$(oget certif_metier_alert); NEW_certif_metier_alert="$cma"
if [ -f "$CERTIF_FILE" ]; then
  proches=""
  while IFS='|' read -r dte lib; do
    dte=$(printf '%s' "$dte" | tr -d ' ')
    case "$dte" in ''|'#'*) continue;; esac          # ignore lignes vides et commentaires
    secs=$(date -d "$dte" +%s 2>/dev/null) || continue
    jours=$(( (secs - $(date +%s)) / 86400 ))
    if [ "$jours" -lt 0 ]; then
      proches="${proches}- ⛔ « ${lib} » est EXPIRÉE depuis $(( -jours )) j (${dte})"$'\n'
    elif [ "$jours" -le "$CERTIF_ALERT_J" ]; then
      proches="${proches}- ⏳ « ${lib} » expire dans ${jours} j (${dte})"$'\n'
    fi
  done < "$CERTIF_FILE"
  if [ -n "$proches" ]; then
    [ "$cma" != "$PARIS_DATE" ] && addalerte "📋 Certifs métier à surveiller :"$'\n'"${proches}"
    NEW_certif_metier_alert="$PARIS_DATE"
  else
    NEW_certif_metier_alert=""
  fi
fi

# 6) briefing si c'est l'heure (08h / 20h Paris), 1x/jour
bm=$(oget brief_morning); be=$(oget brief_evening)
NEW_bm="$bm"; NEW_be="$be"; BRIEF=""
if [ "$PARIS_H" = "08" ] && [ "$bm" != "$PARIS_DATE" ]; then BRIEF=matin; NEW_bm="$PARIS_DATE"; fi
if [ "$PARIS_H" = "20" ] && [ "$be" != "$PARIS_DATE" ]; then BRIEF=soir;  NEW_be="$PARIS_DATE"; fi

# --- ecrire le nouvel etat --------------------------------------------------
{
  for s in $SERVICES; do echo "svc_$s=${SVCNEW[$s]}"; done
  echo "disk_alert=$NEW_disk_alert"
  echo "cert_alert=$NEW_cert_alert"
  echo "certif_metier_alert=$NEW_certif_metier_alert"
  echo "ouvrier_stuck=$nstuck"
  echo "brief_morning=$NEW_bm"
  echo "brief_evening=$NEW_be"
} > "$STATE" 2>/dev/null

# --- envoyer ce qu'il y a a envoyer -----------------------------------------
if [ -n "$ALERTES" ]; then
  envoyer "$ALERTES"
  log "ALERTES envoyees"
fi
if [ -n "$BRIEF" ]; then
  envoyer "$(construire_briefing "$BRIEF")"
  log "BRIEFING $BRIEF envoye"
fi
[ -z "$ALERTES" ] && [ -z "$BRIEF" ] && log "RAS (rien a signaler)"
exit 0
