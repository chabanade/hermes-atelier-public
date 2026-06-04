#!/usr/bin/env bash
#
# configurer-piper-tts.sh
# -----------------------
# À EXÉCUTER SUR LE VPS HERMÈS (là où /opt/data/config.yaml existe réellement).
# Ce script NE PEUT PAS être lancé depuis le bac à sable de l'ouvrier : les
# fichiers /opt/data/... et /opt/hermes/.venv/ n'y sont pas présents.
#
# Ce qu'il fait, exactement et rien d'autre :
#   1. Sauvegarde horodatée de /opt/data/config.yaml
#   2. tts.provider : "edge" -> "piper"
#   3. tts.piper.voice : -> "/opt/data/piper-voices/fr/fr_FR-upmc-medium.onnx"
#   4. Affiche le diff, puis (si --apply) écrit, sinon dry-run (aucune écriture)
#   5. Avec --apply : teste la voix française avec Piper
#
# Usage :
#   bash configurer-piper-tts.sh            # dry-run : montre le diff, n'écrit rien
#   bash configurer-piper-tts.sh --apply    # applique + teste
#
set -euo pipefail

CONFIG="/opt/data/config.yaml"
VOICE="/opt/data/piper-voices/fr/fr_FR-upmc-medium.onnx"
VENV_PY="/opt/hermes/.venv/bin/python"
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

# --- Vérifications préalables -------------------------------------------------
[[ -f "$CONFIG" ]]        || { echo "ERREUR : $CONFIG introuvable. À lancer sur le VPS Hermès." >&2; exit 1; }
[[ -f "$VOICE" ]]         || { echo "ERREUR : voix Piper introuvable : $VOICE" >&2; exit 1; }
[[ -f "$VOICE.json" ]]    || echo "ATTENTION : $VOICE.json introuvable (config de la voix manquante ?)" >&2

# --- 1. Sauvegarde -----------------------------------------------------------
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="${CONFIG}.bak.${STAMP}"
cp -p "$CONFIG" "$BACKUP"
echo "Sauvegarde : $BACKUP"

# --- 2 & 3. Modifications chirurgicales (édition ligne à ligne, sans reformater) ---
# On édite uniquement à l'intérieur du bloc top-level "tts:" pour ne rien casser ailleurs.
NEW="$(mktemp)"
python3 - "$CONFIG" "$VOICE" > "$NEW" <<'PY'
import re, sys
path, voice = sys.argv[1], sys.argv[2]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)

def indent(s):  # nb d'espaces en tête
    return len(s) - len(s.lstrip(" "))

# Repérer le bloc "tts:" de niveau 0
start = None
for i, l in enumerate(lines):
    if re.match(r"^tts:\s*(#.*)?$", l):
        start = i; break
if start is None:
    sys.stderr.write("ERREUR : section 'tts:' introuvable dans le config.yaml\n"); sys.exit(2)

# Fin du bloc tts = prochaine clé de niveau 0
end = len(lines)
for i in range(start+1, len(lines)):
    if lines[i].strip() and indent(lines[i]) == 0:
        end = i; break

block = range(start+1, end)
changed_provider = changed_voice = False

# 2. provider: edge -> piper  (première occurrence directement sous tts, indent minimal)
for i in block:
    m = re.match(r"^(\s*)provider:\s*(['\"]?)edge(['\"]?)\s*(#.*)?$", lines[i])
    if m:
        ind, q1, q2, cmt = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        sep = " " + cmt if cmt else "\n" if not cmt else ""
        lines[i] = f"{ind}provider: {q1}piper{q2}" + (f"  {cmt}" if cmt else "") + "\n"
        changed_provider = True
        break

# 3. trouver le sous-bloc "piper:" dans tts, puis sa clé "voice:"
piper_start = None
for i in block:
    if re.match(r"^\s+piper:\s*(#.*)?$", lines[i]):
        piper_start = i; break
if piper_start is not None:
    p_ind = indent(lines[piper_start])
    p_end = end
    for i in range(piper_start+1, end):
        if lines[i].strip() and indent(lines[i]) <= p_ind:
            p_end = i; break
    for i in range(piper_start+1, p_end):
        m = re.match(r"^(\s*)voice:\s*.*$", lines[i])
        if m:
            lines[i] = f'{m.group(1)}voice: "{voice}"\n'
            changed_voice = True
            break

if not changed_provider:
    sys.stderr.write("ATTENTION : 'provider: edge' non trouvé sous tts (déjà piper ? autre valeur ?)\n")
if not changed_voice:
    sys.stderr.write("ATTENTION : clé 'voice:' sous tts.piper non trouvée — à vérifier/ajouter à la main\n")

sys.stdout.write("".join(lines))
PY

echo "----- DIFF proposé -----"
diff -u "$CONFIG" "$NEW" || true
echo "------------------------"

if [[ "$APPLY" -ne 1 ]]; then
    echo "DRY-RUN : aucune écriture. Relancer avec --apply pour appliquer et tester."
    rm -f "$NEW"
    exit 0
fi

# --- Écriture ----------------------------------------------------------------
cat "$NEW" > "$CONFIG"
rm -f "$NEW"
echo "config.yaml mis à jour. (Restaurer si besoin : cp \"$BACKUP\" \"$CONFIG\")"

# --- 5. Test de la voix française avec Piper ---------------------------------
PHRASE="Mehdi, test de la voix française avec Piper. Est-ce que c'est compréhensible ?"
OUT="/tmp/hermes-piper-test-${STAMP}.wav"
echo "Test Piper -> $OUT"
if [[ -x "$VENV_PY" ]]; then
    printf '%s' "$PHRASE" | "$VENV_PY" -m piper --model "$VOICE" --output_file "$OUT"
else
    printf '%s' "$PHRASE" | piper --model "$VOICE" --output_file "$OUT"
fi
echo "Fichier audio généré : $OUT"
echo "Écoute : aplay \"$OUT\"   (ou ffplay/vlc)"
echo
echo "NB : ceci teste Piper en direct. Pour valider la boucle Hermès complète,"
echo "redémarrer le service Hermès afin qu'il recharge config.yaml, puis"
echo "lui faire prononcer la phrase via son canal habituel (text_to_speech)."
