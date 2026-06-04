#!/bin/bash
# ============================================================================
#  ROOT-GATEWAY (VPS)  --  l'ouvrier execute ses actions ROOT lui-meme, en surete.
# ============================================================================
#  L'ouvrier (dans sa cage, SANS root) prepare un script .sh dans son atelier et
#  ecrit son chemin dans  ~/travaux/.root-request . L'aiguilleur (deja root, HORS
#  cage) appelle ce gateway APRES la tache : il SCANNE le script contre les seules
#  CATASTROPHES IRREVERSIBLES, puis l'execute en root. Tout le reste passe.
#
#  Philosophie (choix Mehdi) : rendre le DANGER mecaniquement impossible, laisser
#  TOUT le reste 100% libre. Pas de sur-blocage. apt/systemctl/ufw allow/install =
#  AUTORISES. Seules les vraies catastrophes (effacement systeme, desarmement des
#  garde-fous, backdoor) sont refusees -> elles, c'est GO humain a la main.
#
#  $1 = chemin du script (doit etre sous /home/ouvrier/travaux, un .sh).
# ============================================================================
set -u
SCRIPT="${1:-}"
TRAVAUX=${RG_TRAVAUX:-/home/ouvrier/travaux}
JOURNAL=${RG_JOURNAL:-/home/ouvrier/journal/root-gateway.log}
TIMEOUT=${RG_TIMEOUT:-900}
log(){ printf '%s\t%s\n' "$(date -Is)" "$1" >> "$JOURNAL" 2>/dev/null; }

# --- 1. Perimetre : un .sh sous travaux/, sans echappement (../) ------------
case "$SCRIPT" in
  "$TRAVAUX"/*.sh) ;;
  *) echo "[root-gateway] REFUSE : doit etre un .sh sous travaux/ -> $SCRIPT"; log "REFUSE perimetre $SCRIPT"; exit 1 ;;
esac
[ -f "$SCRIPT" ] || { echo "[root-gateway] script introuvable : $SCRIPT"; log "ABSENT $SCRIPT"; exit 1; }
RP=$(realpath -m "$SCRIPT" 2>/dev/null)
case "$RP" in "$TRAVAUX"/*) ;; *) echo "[root-gateway] REFUSE : echappement de travaux/ ($RP)"; log "REFUSE traversee $SCRIPT"; exit 1 ;; esac

# --- 2. Scan ANTI-CATASTROPHE (minimal : irreversible + desarmement) --------
BLOB=$(cat "$RP" 2>/dev/null)
d=""
add(){ d="${d:+$d ; }$1"; }
# (a) effacement massif disque / systeme (chemins systeme EXACTS, pas un sous-dossier projet)
printf '%s' "$BLOB" | grep -qiE 'rm[[:space:]]+-[a-z]*[rf][a-z]*[[:space:]]+(/|/\*|/etc|/boot|/usr|/bin|/sbin|/lib[0-9]*|/var|/home|/root|/opt)([[:space:]]|/\*|$)' && add "effacement systeme (rm -rf chemin critique)"
printf '%s' "$BLOB" | grep -qiE '(\bmkfs|\bwipefs|\bshred[[:space:]]+/|\bdd[[:space:]].*[[:space:]]of=/dev/|>[[:space:]]*/dev/(sd|nvme|vd)|\bfdisk[[:space:]]+/dev|\bparted[[:space:]]+/dev)' && add "destruction de disque/partition"
printf '%s' "$BLOB" | grep -qE ':[[:space:]]*\(\)[[:space:]]*\{[[:space:]]*:[[:space:]]*\|[[:space:]]*:' && add "fork bomb"
# (b) desarmement des garde-fous (copilote / cage / aiguilleur / gateway / sudoers)
printf '%s' "$BLOB" | grep -qiE '(copilote|videur|ouvrier-settings|/home/ouvrier/cage|/home/ouvrier/hooks|aiguilleur\.sh|root-gateway|deploy-gateway|/etc/sudoers|visudo)' && add "touche aux garde-fous (copilote/cage/aiguilleur/sudoers)"
printf '%s' "$BLOB" | grep -qiE '(ufw[[:space:]]+disable|iptables[[:space:]]+-F|nft[[:space:]]+flush[[:space:]]+ruleset)' && add "desactive completement le pare-feu"
# (c) backdoor : cle ssh autorisee / nouvel utilisateur / mot de passe root
printf '%s' "$BLOB" | grep -qiE '(authorized_keys|\buseradd\b|\badduser\b|\bchpasswd\b|usermod[[:space:]].*-aG[[:space:]]*[^[:space:]]*sudo|\bpasswd[[:space:]]+root)' && add "backdoor (cle ssh autorisee / user / passwd root)"
# (d) reverse-shell ou exfil reseau brute d'un secret
printf '%s' "$BLOB" | grep -qiE '(nc[[:space:]].*-e[[:space:]]|/dev/tcp/|bash[[:space:]]+-i[[:space:]]*>&|(curl|wget)[[:space:]].*(/etc/shadow|id_ed25519|id_rsa|\.env|CREDENTIALS))' && add "reverse-shell ou exfil de secret vers le reseau"

if [ -n "$d" ]; then
  echo ">>> ACTION ROOT REFUSEE par le sas (catastrophe potentielle) : $d"
  echo "    Script concerne : $RP"
  echo "    Ce cas precis demande un GO humain de Mehdi (a la main). Tout le reste passe seul."
  log "REFUSE-CATASTROPHE $RP : $d"
  exit 2
fi

# --- 3. Execution en ROOT (l'aiguilleur nous a deja en root) ----------------
log "EXEC-ROOT $RP"
echo "=========== ACTION ROOT (sas, hors cage) ==========="
echo "Script : $RP"
echo "----------------------------------------------------"
timeout "$TIMEOUT" bash "$RP" 2>&1
STATUS=$?
echo "----------------------------------------------------"
[ "$STATUS" = 124 ] && echo "[root-gateway] interrompu : depassement du delai ($TIMEOUT s)."
echo "[root-gateway] Action root terminee (code $STATUS)."
echo "===================================================="
log "FIN $RP (code $STATUS)"
exit $STATUS
