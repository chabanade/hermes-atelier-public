#!/bin/bash
# ============================================================================
#  MENAGE-ATELIER  --  retention de la boite aux lettres de l'atelier.
# ----------------------------------------------------------------------------
#  La file accumulait SANS LIMITE : les resultats (.out / .colis / .tar.gz), les
#  ordres archives (done/) et parfois des .live orphelins (ouvrier coupe). Ce
#  script applique une retention DOUCE et SURE, lance par un minuteur quotidien.
#  Rien d'urgent n'est supprime : on ne touche qu'a ce qui est nettement ancien.
#  Surchargeable par MA_* (pour les tests). Usage : bash menage-atelier.sh
# ============================================================================
set -u
QUEUE=${MA_QUEUE:-/root/.hermes/claude-queue}
OUT="$QUEUE/out"
DONE="$QUEUE/done"
JOURS_OUT=${MA_JOURS_OUT:-10}      # resultats gardes 10 jours (Hermes les a lus bien avant)
JOURS_DONE=${MA_JOURS_DONE:-21}    # ordres archives gardes 21 jours
HEURES_LIVE=${MA_HEURES_LIVE:-3}   # un .live de +3 h = orphelin (ouvrier coupe sans livrer)
LOG=${MA_LOG:-/root/.hermes/menage-atelier.log}

n=0
# 1) .live orphelins : un suivi en direct ne doit vivre que le temps d'un ordre.
while IFS= read -r f; do [ -n "$f" ] && rm -f "$f" && n=$((n+1)); done < <(
  find "$OUT" -maxdepth 1 -name '*.live' -mmin +$((HEURES_LIVE*60)) 2>/dev/null)
# 2) resultats anciens (texte, casiers, archives, vieux statuts).
while IFS= read -r f; do [ -n "$f" ] && rm -rf "$f" && n=$((n+1)); done < <(
  find "$OUT" -maxdepth 1 \( -name '*.out' -o -name '*.tar.gz' -o -name '*.status' -o -name '*.colis' \) -mtime +"$JOURS_OUT" 2>/dev/null)
# 3) ordres archives anciens.
while IFS= read -r f; do [ -n "$f" ] && rm -f "$f" && n=$((n+1)); done < <(
  find "$DONE" -maxdepth 1 -type f -mtime +"$JOURS_DONE" 2>/dev/null)

# 4) boite vocale partagee (inbox/outbox du pont Hermes) : un message vocal vit
#    quelques secondes -> tout .json de +1 h est un orphelin (consommateur parti).
VOCDATA=${MA_VOCDATA:-/home/ouvrier/travaux/webrtc-vocal/data}
for sub in inbox outbox; do
  while IFS= read -r f; do [ -n "$f" ] && rm -f "$f" && n=$((n+1)); done < <(
    find "$VOCDATA/$sub" -maxdepth 1 -type f -name '*.json' -mmin +60 2>/dev/null)
done

printf '%s\tmenage : %s element(s) supprime(s) (regle : out>%sj, done>%sj, live>%sh)\n' \
  "$(date -Is)" "$n" "$JOURS_OUT" "$JOURS_DONE" "$HEURES_LIVE" >> "$LOG" 2>/dev/null
echo "menage termine : $n element(s) supprime(s)."
