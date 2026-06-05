#!/bin/bash
# ============================================================================
#  ROTER-TOKEN  --  remplace EN SECURITE le token Telegram du bot @ermes_Voc_bot
# ----------------------------------------------------------------------------
#  Le NOUVEAU token n'est JAMAIS passe en argument (il resterait dans
#  l'historique du shell) ni affiche a l'ecran. Il est lu depuis un fichier
#  .new-token que tu deposes, VALIDE aupres de Telegram AVANT d'etre applique
#  (si tu t'es trompe, on ne casse rien), puis efface en fin.
#
#  Usage (en root sur le VPS) :  bash roter-cle-bot.sh
#  Prerequis : avoir revoque l'ancien token via @BotFather et depose le nouveau
#  (une seule ligne, rien d'autre) dans :
#     /home/ouvrier/travaux/hermes-voc-bot/.new-token
# ============================================================================
set -u
ENV=/home/ouvrier/travaux/hermes-voc-bot/.env
NEW=/home/ouvrier/travaux/hermes-voc-bot/.new-token
SVC=hermes-voc-bot

[ -f "$NEW" ] || { echo "Depose d'abord le nouveau token dans $NEW (une ligne, rien d'autre)."; exit 1; }
TOKEN=$(tr -d ' \r\n' < "$NEW")
[ -n "$TOKEN" ] || { echo "Le fichier $NEW est vide."; exit 1; }

echo "1) Verification du nouveau token aupres de Telegram (AVANT d'appliquer)..."
USERNAME=$(curl -s --max-time 10 "https://api.telegram.org/bot${TOKEN}/getMe" | grep -o '"username":"[^"]*"' | head -1)
if [ -z "$USERNAME" ]; then
  echo "   ECHEC : Telegram refuse ce token. RIEN n'a ete change. Verifie le token et reessaie."
  exit 1
fi
echo "   OK : token valide pour le bot ($USERNAME)."

echo "2) Sauvegarde du .env..."
cp -a "$ENV" "$ENV.bak-$(date +%Y%m%d-%H%M%S)"

echo "3) Remplacement de la valeur de TELEGRAM_TOKEN (le reste du .env est preserve)..."
tmp=$(mktemp)
sed -E "s|^TELEGRAM_TOKEN=.*|TELEGRAM_TOKEN=${TOKEN}|" "$ENV" > "$tmp" && cat "$tmp" > "$ENV" && rm -f "$tmp"
chown ouvrier:ouvrier "$ENV"; chmod 600 "$ENV"

echo "4) Redemarrage du bot..."
systemctl restart "$SVC"
sleep 3
if systemctl is-active "$SVC" | grep -q '^active'; then
  echo "   OK : bot actif."
else
  echo "   ECHEC : le bot ne demarre pas. Restaure le dernier .env.bak-* puis relance le service."
  exit 1
fi

echo "5) Effacement du token en clair (il ne doit pas trainer)..."
shred -u "$NEW" 2>/dev/null || rm -f "$NEW"
echo "TERMINE : ancien token revoque (par BotFather), nouveau en place et teste."
echo "Confirme en envoyant un petit vocal au bot depuis ton telephone."
