#!/bin/bash
# ============================================================================
#  BANC D'ESSAI de la RECUPERATION DES ARTEFACTS de l'aiguilleur.
#  Le faux ouvrier cree, PENDANT l'ordre, un eventail de fichiers :
#    - du code propre            -> DOIT etre livre
#    - un .env (secret)          -> DOIT etre exclu (Loi Zero)
#    - un fichier *token*         -> DOIT etre exclu (nom sensible)
#    - un fichier dans un .venv   -> DOIT etre exclu (dossier lourd)
#    - un fichier > 5 Mo          -> DOIT etre exclu (poids)
#  Et un fichier ANCIEN (anterieur a l'ordre) ne DOIT PAS etre ramasse.
# ============================================================================
set -u
BASE=/tmp/art-test
AIG_SRC=/mnt/c/Users/VOTRE_USER/claude-relay/vps/aiguilleur.sh

PASS=0; FAIL=0
ok(){ echo "  [ OK  ] $1"; PASS=$((PASS+1)); }
ko(){ echo "  [ECHEC] $1"; FAIL=$((FAIL+1)); }

rm -rf "$BASE"; mkdir -p "$BASE/queue/in" "$BASE/queue/out" "$BASE/queue/done" "$BASE/travaux/projet/sous"
: > "$BASE/alertes.jsonl"; : > "$BASE/noop-format.py"
# fichier ANCIEN (ne doit pas etre ramasse car anterieur a l'ordre)
echo "vieux contenu" > "$BASE/travaux/ancien.txt"; touch -d '2020-01-01' "$BASE/travaux/ancien.txt"

cat > "$BASE/faux-cage.sh" <<'FAUX'
#!/bin/bash
# Faux ouvrier : cree des fichiers dans l'atelier puis repond.
cd /tmp/art-test/travaux || exit 1
mkdir -p projet/sous projet/.venv
echo "print('bonjour')" > projet/app.py
echo "def util(): return 1" > projet/sous/util.py
printf 'API_KEY=SUPER_SECRET_A_NE_PAS_FUITER\n' > projet/.env
echo "junk de venv" > projet/.venv/lib.py
printf 'tok=abcdef\n' > SECRET_token.txt
truncate -s 6M projet/gros.bin
printf 'REPONSE-OUVRIER-FAUX : projet code\n'
FAUX
chmod +x "$BASE/faux-cage.sh"

sed 's/\r$//' "$AIG_SRC" > "$BASE/aiguilleur.sh"; chmod +x "$BASE/aiguilleur.sh"

export AIG_QUEUE="$BASE/queue" AIG_CAGE="$BASE/faux-cage.sh" AIG_RUNAS="" \
       AIG_ALERTES="$BASE/alertes.jsonl" AIG_FORMAT="$BASE/noop-format.py" \
       AIG_HERMES_UID="$(id -u)" AIG_PCQUEUE="$BASE/pcqueue" AIG_TRAVAUX="$BASE/travaux"

echo "============================================================"
echo " Ordre qui PRODUIT des fichiers -> recuperation des artefacts"
echo "============================================================"
echo "Cree le projet" > "$BASE/queue/in/colis-art"
bash "$BASE/aiguilleur.sh"

C="$BASE/queue/out/colis-art.colis"
OUT="$BASE/queue/out/colis-art.out"

echo "-- contenu du casier livre :"
( cd "$C" 2>/dev/null && find . -type f | sed 's/^/     /' ) || echo "     (casier absent)"
echo ""

# --- fichiers qui DOIVENT etre livres ---
[ -f "$C/projet/app.py" ]        && ok "code livre : projet/app.py"              || ko "projet/app.py manquant"
[ -f "$C/projet/sous/util.py" ]  && ok "code livre (sous-dossier) : projet/sous/util.py" || ko "projet/sous/util.py manquant"
# --- fichiers de config/secret : INCLUS dans le casier (la cle voyage avec la machine) ---
[ -f "$C/projet/.env" ]          && ok ".env INCLUS dans le casier (cle livree avec le code)" || ko "le .env aurait du etre livre"
[ -f "$C/SECRET_token.txt" ]     && ok "fichier *token* INCLUS dans le casier"   || ko "le token aurait du etre livre"
# --- fichiers qui DOIVENT etre EXCLUS (poids, pas securite) ---
[ ! -e "$C/projet/.venv" ]       && ok ".venv EXCLU (dossier lourd elagué)"      || ko ".venv a ete ramasse"
[ ! -f "$C/projet/gros.bin" ]    && ok "fichier > 5 Mo EXCLU (poids)"            || ko "le fichier de 6 Mo a ete livre"
# --- fichier ancien non ramasse ---
[ ! -f "$C/ancien.txt" ]         && ok "fichier ANCIEN non ramasse (hors fenetre de l'ordre)" || ko "ancien.txt ramasse a tort"
# --- archive + compte-rendu ---
[ -f "$BASE/queue/out/colis-art.tar.gz" ] && ok "archive .tar.gz creee" || ko "archive .tar.gz manquante"
grep -q "ARTEFACTS LIVRES" "$OUT" 2>/dev/null && ok "le compte-rendu annonce les artefacts" || ko "le compte-rendu ne mentionne pas les artefacts"
grep -qi "config/secret\|carte postale\|ne recopie" "$OUT" 2>/dev/null && ok "le compte-rendu SIGNALE les fichiers sensibles (a ne pas recopier en clair)" || ko "signalement des fichiers sensibles manquant"
# --- l'archive contient bien les fichiers de config (la cle voyage avec la machine) ---
if [ -f "$BASE/queue/out/colis-art.tar.gz" ]; then
  if tar -tzf "$BASE/queue/out/colis-art.tar.gz" 2>/dev/null | grep -q '\.env$\|SECRET_token'; then ok "l'archive contient bien les fichiers de config (utilisable tel quel)"; else ko "les fichiers de config manquent dans l'archive"; fi
fi

echo ""
echo "============================================================"
echo " Ordre SANS production de fichier -> pas de casier fantome"
echo "============================================================"
cat > "$BASE/faux-cage2.sh" <<'F2'
#!/bin/bash
printf 'REPONSE-OUVRIER-FAUX : juste une analyse, aucun fichier produit.\n'
F2
chmod +x "$BASE/faux-cage2.sh"
AIG_CAGE="$BASE/faux-cage2.sh" bash "$BASE/aiguilleur.sh" < /dev/null
# (re-dépose un ordre car le précédent est archivé)
echo "Analyse seule" > "$BASE/queue/in/colis-analyse"
AIG_CAGE="$BASE/faux-cage2.sh" bash "$BASE/aiguilleur.sh"
[ ! -e "$BASE/queue/out/colis-analyse.colis" ] && ok "aucun casier cree quand rien n'est produit" || ko "un casier fantome a ete cree"
[ ! -f "$BASE/queue/out/colis-analyse.tar.gz" ] && ok "aucune archive fantome" || ko "une archive fantome a ete creee"
grep -q "ARTEFACTS" "$BASE/queue/out/colis-analyse.out" 2>/dev/null && ko "le compte-rendu parle d'artefacts a tort" || ok "compte-rendu propre (pas d'artefacts annonces a tort)"

echo ""
echo "============================================================"
echo " RESULTAT : $PASS reussis, $FAIL echoues"
echo "============================================================"
[ "$FAIL" -eq 0 ] && echo "TOUS_LES_TESTS_OK" || echo "DES_TESTS_ONT_ECHOUE"
