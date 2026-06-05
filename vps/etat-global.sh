#!/bin/bash
# ============================================================================
#  ETAT-GLOBAL  --  tableau de bord PERMANENT du systeme, pour Hermes.
# ============================================================================
#  Genere (sur l'HOTE = la verite) un fichier d'etat qu'Hermes lit d'un coup
#  d'oeil au lieu de redecouvrir l'etat a la main (15 commandes a tatons).
#  Appele a chaque passe de l'aiguilleur -> rafraichi en continu.
#  Ecrit dans /root/.hermes/etat-systeme.md (= /opt/data/etat-systeme.md cote Hermes).
#  $1 = fichier de sortie  $2 = uid d'Hermes
# ============================================================================
OUT=${1:-/root/.hermes/etat-systeme.md}
HUID=${2:-10000}
DOMAINE=votre-domaine.example
Q=/root/.hermes/claude-queue

act(){ systemctl is-active "$1" 2>/dev/null || echo inconnu; }
diag=$(curl -s -m 3 http://127.0.0.1:8686/diag 2>/dev/null)
ice=$(curl -s -m 3 http://127.0.0.1:8686/ice 2>/dev/null)
g(){ printf '%s' "$diag" | grep -oE "\"$1\": *[0-9]+" | grep -oE '[0-9]+' | head -1; }
frames=$(g frames_in_total); sessions=$(g sessions_total)
turn=$(printf '%s' "$ice" | grep -qi 'turns:' && echo "OUI" || echo "NON (STUN seul)")
voc=$(act webrtc-vocal); cot=$(act coturn); cad=$(act caddy)
nin=$(ls "$Q/in/" 2>/dev/null | wc -l | tr -d ' ')
npc=$(ls /root/claude-queue-pc/in/ 2>/dev/null | wc -l | tr -d ' ')
ouv=$(pgrep -u ouvrier -f 'claude' >/dev/null 2>&1 && echo "OUI, il bosse" || echo "non, libre")
livef=$(ls "$Q/out/"*.live 2>/dev/null | head -1)

{
  echo "# TABLEAU DE BORD SYSTEME (rafraichi tout seul) -- $(date -u +%H:%M:%SZ) UTC"
  echo ""
  echo "_Lis CE fichier en premier pour savoir ou on en est, avant de lancer des commandes._"
  echo ""
  echo "## Services (etat REEL sur l'hote)"
  echo "| service | etat |"
  echo "|---|---|"
  echo "| Serveur vocal (webrtc-vocal) | $voc |"
  echo "| coturn (relais TURN)         | $cot |"
  echo "| Caddy (HTTPS)                | $cad |"
  echo ""
  echo "## Bot vocal (https://$DOMAINE)"
  [ -z "$diag" ] && echo "- ⚠️ serveur vocal **injoignable** sur l'hote (pas de reponse a /diag) -- il est peut-etre arrete."
  echo "- TURN annonce au navigateur (/ice) : **$turn**"
  echo "- Sessions connectees (total) : ${sessions:-?}"
  echo "- Audio recu (frames_in)      : ${frames:-?}   _(>0 = l'audio passe ; 0 = il faut le TURN)_"
  echo ""
  echo "## Ouvrier & files d'ordres"
  echo "- Ouvrier VPS : $ouv"
  if [ -n "$livef" ]; then
    echo "- **Suivi EN DIRECT actif** : \`out/$(basename "$livef")\` -> lis-le pour voir le travail en cours."
  fi
  echo "- Ordres en attente : $nin (VPS) / $npc (PC)"
  echo ""
  echo "## Rappels (pour ne pas te perdre)"
  echo "- LA copie de prod = \`/home/ouvrier/travaux/<projet>\` ; le serveur est lance par le **service systemd** \`webrtc-vocal\`, PAS a la main. Ne lance/kill pas de serveur toi-meme : si le serveur doit changer, confie-le a l'ouvrier (sas root)."
  echo "- Pour TESTER un service : toujours **https://$DOMAINE/...**, jamais \`localhost\`/127.0.0.1 (= ton conteneur, un mirage)."
} > "$OUT" 2>/dev/null
chown "$HUID:$HUID" "$OUT" 2>/dev/null
chmod 644 "$OUT" 2>/dev/null
