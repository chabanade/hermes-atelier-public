#!/usr/bin/env bash
# Orchestration : installe coturn (TURN Option A) PUIS vérifie de bout en bout.
# À LANCER EN ROOT — déclaré au « sas root » via .root-request (l'ouvrier n'a pas
# sudo dans sa cage : no_new_privs). Réutilise install-coturn.sh TEL QUEL, puis
# redémarre l'appli de prod (en lui rendant la main — PAS en root) et contrôle que
# le relais TURN est bien annoncé par /ice et /diag.
#
# Pourquoi ce wrapper : toute la vérif (systemctl/ss/curl :8686) ne s'observe que
# SUR l'hôte, et le sas root est le seul canal vers l'hôte → on installe ET on
# vérifie d'un seul passage root, et le résultat complet revient dans la réponse.
set -uo pipefail   # PAS de -e : on veut un bilan complet même si une étape rate.

HERE="$(cd "$(dirname "$0")" && pwd)"   # …/webrtc-vocal/deploy
REPO="$(cd "$HERE/.." && pwd)"          # …/webrtc-vocal
DOMAIN="${TURN_DOMAIN:-votre-domaine.example}"
BASE="http://127.0.0.1:8686"
ok=0; ko=0
pass(){ echo "  ✓ $*"; ok=$((ok+1)); }
fail(){ echo "  ✗ $*"; ko=$((ko+1)); }

echo "════════ 1/5 — Installation coturn (install-coturn.sh) ════════"
if ! bash "$HERE/install-coturn.sh"; then
  echo
  echo "✗ install-coturn.sh a échoué (voir ci-dessus — typiquement : certificat"
  echo "  Caddy introuvable, donc coturn refuse de démarrer). On s'arrête : rien à"
  echo "  vérifier tant que coturn n'est pas installé. Corriger Caddy puis relancer."
  exit 1
fi

echo
echo "════════ 2/5 — Service coturn ════════"
systemctl --no-pager --full status coturn 2>&1 | head -n 10 || true
if systemctl is-active --quiet coturn; then pass "coturn actif (running)"; else fail "coturn n'est PAS actif (running)"; fi

echo
echo "════════ 3/5 — Écoute TLS sur 5349 ════════"
if ss -tlnp 2>/dev/null | grep -E ':5349\b'; then
  pass "un process écoute sur 5349/TCP"
else
  fail "rien en écoute sur 5349/TCP"
fi

echo
echo "════════ 4/5 — Redémarrage de l'appli (prise en compte de turn.env) ════════"
# install-coturn.sh vient de (ré)écrire le secret dans $REPO/deploy/turn.env.
# Choix du dépôt de PROD : /opt/data prioritaire s'il existe sur l'hôte (cf.
# mission), sinon l'atelier monté (cf. CLAUDE.md : « c'est cette copie qui est
# servie en prod »). On garantit au dépôt retenu le MÊME turn.env, puis on
# redémarre SON serveur — en rendant la main à son propriétaire (jamais en root,
# sinon server.pid/logs deviendraient root et casseraient les restart suivants).
PROD="$REPO"
if [[ -x /opt/data/webrtc-vocal/restart.sh ]]; then PROD=/opt/data/webrtc-vocal; fi
echo "Dépôt de prod retenu : $PROD"
src_id="$(stat -c '%d:%i' "$REPO/deploy/turn.env" 2>/dev/null || echo src)"
dst_id="$(stat -c '%d:%i' "$PROD/deploy/turn.env" 2>/dev/null || echo dst)"
if [[ "$src_id" != "$dst_id" ]]; then
  echo "turn.env propagé vers $PROD/deploy/ (dépôt distinct de l'atelier)."
  install -D -m 0640 "$REPO/deploy/turn.env" "$PROD/deploy/turn.env"
  chown "$(stat -c '%U:%G' "$PROD")" "$PROD/deploy/turn.env" 2>/dev/null || true
fi
owner="$(stat -c '%U' "$PROD")"
echo "Redémarrage de $PROD/restart.sh en tant que « $owner »…"
if runuser -u "$owner" -- bash "$PROD/restart.sh"; then
  pass "appli redémarrée"
elif su -s /bin/bash -c "bash '$PROD/restart.sh'" "$owner"; then
  pass "appli redémarrée (via su)"
else
  fail "restart.sh a échoué (voir $PROD/logs/server.log)"
fi

echo
echo "════════ 5/5 — Vérification /ice et /diag ════════"
ice=""
for _ in $(seq 1 25); do
  ice="$(curl -fsS "$BASE/ice" 2>/dev/null || true)"
  [[ -n "$ice" ]] && break
  sleep 1
done
if [[ -z "$ice" ]]; then
  fail "le serveur ne répond pas sur $BASE/ice (a-t-il démarré ? $PROD/logs/server.log)"
else
  echo "  /ice  = $ice"
  if printf '%s' "$ice" | grep -q "turns:${DOMAIN}:5349"; then
    pass "TURN annoncé via /ice (turns:${DOMAIN}:5349)"
  else
    fail "TURN ABSENT de /ice"
  fi
  n="$(printf '%s' "$ice" | grep -o '"urls"' | wc -l)"
  if [[ "$n" -ge 2 ]]; then pass "ice_servers = $n (STUN + TURN)"; else fail "ice_servers = $n (< 2 attendu)"; fi
fi
diag="$(curl -fsS "$BASE/diag" 2>/dev/null || true)"
if [[ -n "$diag" ]]; then
  nd="$(printf '%s' "$diag" | grep -o '"urls"' | wc -l)"
  if [[ "$nd" -ge 2 ]]; then pass "/diag : ice_servers = $nd (≥ 2)"; else fail "/diag : ice_servers = $nd (< 2)"; fi
fi

echo
echo "════════ BILAN ════════"
echo "  $ok contrôle(s) OK / $ko KO"
echo
echo "⚠ ÉTAPE MANUELLE RESTANTE (hors de ma portée) : ouvrir 5349/TCP — et la plage"
echo "  relais 49160-49200/UDP — dans le PARE-FEU HOSTINGER (hPanel ▸ VPS ▸ Firewall)."
echo "  L'UDP entrant y est bloqué : sans 5349/TCP ouvert côté Hostinger, le TURN/TLS"
echo "  reste injoignable depuis l'extérieur même si coturn tourne ici."
if [[ "$ko" -eq 0 ]]; then
  echo "✅ coturn installé et TURN annoncé (reste l'ouverture pare-feu Hostinger ci-dessus)."
else
  echo "⚠ Des contrôles ont échoué — voir les ✗ ci-dessus."
fi
exit 0
