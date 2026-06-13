#!/bin/bash
# ============================================================================
#  AIGUILLEUR  --  le pont entre Hermes et l'ouvrier Claude (VPS).
# ============================================================================
#  Script DETERMINISTE (pas une IA). Tourne en root sur l'hote.
#  1. Releve la boite aux lettres (les ordres deposes par Hermes).
#  2. Lance l'ouvrier DANS SA CAGE (bubblewrap + copilote + Opus/xhigh).
#     -> l'ordre est passe comme PROMPT (jamais execute comme commande shell).
#  3. Annexe les drapeaux du copilote a la reponse, depose pour Hermes, archive.
#  Appele periodiquement (cron / boucle). Une passe = tous les ordres presents.
#
#  Securite : l'ouvrier tourne TOUJOURS sous cage+copilote, donc meme un ordre
#  malveillant (injection) ne peut RIEN casser. L'aiguilleur, lui, ne fait que
#  passer le texte a claude -- il ne l'interprete jamais.
# ============================================================================
set -u
# --- Chemins. Surchargeables par AIG_* UNIQUEMENT pour les tests a blanc.
#     En production aucune AIG_* n'est definie -> valeurs identiques a avant. ---
QUEUE=${AIG_QUEUE:-/root/.hermes/claude-queue}
CAGE=${AIG_CAGE:-/home/ouvrier/cage.sh}
SETTINGS=${AIG_SETTINGS:-/home/ouvrier/hooks/ouvrier-settings.json}
ALERTES=${AIG_ALERTES:-/home/ouvrier/journal/alertes.jsonl}   # drapeaux du copilote (vides avant chaque ordre, relus apres)
FORMAT=${AIG_FORMAT:-/home/ouvrier/format-drapeaux.py}        # met les drapeaux en forme lisible pour Hermes
FORMATLIVE=${AIG_FORMATLIVE:-/home/ouvrier/format-live.py}    # transforme le flux de l'ouvrier en avancement EN DIRECT (.live) pour Hermes
RUNAS=${AIG_RUNAS-sudo -u ouvrier}                            # qui execute l'ouvrier (chaine vide en test)
LOG="$QUEUE/aiguilleur.log"
HERMES_UID=${AIG_HERMES_UID:-10000}          # uid d'Hermes : les reponses doivent lui appartenir
TIMEOUT=${AIG_TIMEOUT:-3600}                 # duree max d'un ordre en secondes (60 min)
BEAT_TICKS=${AIG_BEAT_TICKS:-15}             # battement de coeur tous les BEAT_TICKS x 2s (= 30s)
PCQUEUE=${AIG_PCQUEUE:-/root/claude-queue-pc} # file de transfert vers le PC (ordres @pc)
TRAVAUX=${AIG_TRAVAUX:-/home/ouvrier/travaux}        # atelier de l'ouvrier (ses fichiers produits)
ART_MAX_MO=${AIG_ART_MAX_MO:-20}                     # taille totale max d'un colis d'artefacts (Mo)
ART_MAX_FICHIER_MO=${AIG_ART_MAX_FICHIER_MO:-5}      # taille max d'un fichier d'artefact (Mo)
ROOTGW=${AIG_ROOTGW:-/home/ouvrier/root-gateway.sh}  # sas root : execute (validees) les actions root demandees par l'ouvrier
HOSTGW=${AIG_HOSTGW:-/home/ouvrier/hostinger-gateway.sh}  # sas Hostinger : actions pare-feu (via API officielle) demandees par l'ouvrier
CONSIGNES_PATRON=${AIG_CONSIGNES:-/root/.hermes/consignes-patron.txt}  # consignes permanentes de Mehdi, injectees en tete de CHAQUE mission (canal direct patron)
ETATGW=${AIG_ETATGW:-/home/ouvrier/etat-global.sh}   # genere le tableau de bord permanent (vue d'ensemble) pour Hermes
ETAT_OUT=${AIG_ETAT_OUT:-/root/.hermes/etat-systeme.md}

log(){ printf '%s\t%s\n' "$(date -Is)" "$1" >> "$LOG"; }

# --- SUIVI DE COLIS ----------------------------------------------------------
#  Ecrit un fichier .status qu'Hermes peut lire A TOUT MOMENT pendant le travail.
#  Le coeur du systeme = le BATTEMENT : un numero qui s'incremente tant que
#  l'ouvrier est vivant. Hermes n'a pas a raisonner sur l'heure (piege du fuseau) :
#  il relit le fichier, si le numero a monte -> vivant, s'il est fige -> probleme.
#  Le .status existe pendant le traitement et DISPARAIT a la livraison du .out.
ecrire_statut(){   # $1=fichier .status  $2=numero de battement  $3=heure de prise en charge
  printf '%s\n' \
"[EN COURS] L'ouvrier Claude traite cet ordre -- il TRAVAILLE, ce n'est ni perdu ni plante." \
"" \
"  >>> BATTEMENT DE COEUR : $2 <<<" \
"" \
"Tant que ce numero AUGMENTE entre deux lectures, l'ouvrier est VIVANT : patiente, ne dis JAMAIS qu'il est mort ou down." \
"S'il reste FIGE au meme numero plus de 3 minutes, alors seulement quelque chose cloche : previens Mehdi." \
"" \
"  (pris en charge a : $3)" > "$1"
  chown "$HERMES_UID:$HERMES_UID" "$1" 2>/dev/null
  chmod 644 "$1"
}

# --- RECUPERATION DES ARTEFACTS ---------------------------------------------
#  Le .out ne transporte que du TEXTE. Cette fonction recupere les FICHIERS que
#  l'ouvrier vient de produire dans son atelier et les livre a Hermes dans un
#  "casier" out/<ordre>.colis/ + une archive .tar.gz.
#  SECURITE (Loi Zero) : on EXCLUT tout secret (.env/.key/.pem/...token...) et
#  les dossiers lourds reconstructibles (.venv/__pycache__/node_modules/.git).
#  On ne prend QUE les fichiers modifies PENDANT l'ordre (plus recents que $1).
#  Renvoie un resume texte sur stdout (vide si rien a livrer).
recuperer_artefacts(){   # $1=marqueur temporel  $2=nom de l'ordre
  local marqueur="$1" nom="$2"
  local colis="$QUEUE/out/$nom.colis"
  local archive="$QUEUE/out/$nom.tar.gz"
  local maxf=$((ART_MAX_FICHIER_MO * 1024 * 1024))
  local maxt=$((ART_MAX_MO * 1024 * 1024))
  local copies=0 sensibles=0 exclus=0 octets=0 liste_copies="" liste_sensibles="" liste_exclus=""
  local f base rel sz secret
  shopt -s nocasematch
  while IFS= read -r -d '' f; do
    base=$(basename "$f")
    # Fichier de config/secret ? On le LIVRE quand meme (la cle voyage avec la
    # machine), mais on le MARQUE pour qu'Hermes ne recopie jamais sa valeur en clair.
    secret=0
    case "$base" in
      .env|*.env|*.key|*.pem|*.pfx|*.p12|id_rsa|id_ed25519|*secret*|*credential*|*token*|*password*) secret=1;;
    esac
    sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
    if [ "$sz" -gt "$maxf" ]; then
      exclus=$((exclus + 1)); liste_exclus="$liste_exclus
     - ${f#$TRAVAUX/} (ignore : > ${ART_MAX_FICHIER_MO} Mo)"; continue
    fi
    octets=$((octets + sz))
    if [ "$octets" -gt "$maxt" ]; then
      liste_exclus="$liste_exclus
     - (...colis tronque : total > ${ART_MAX_MO} Mo)"; break
    fi
    rel=${f#$TRAVAUX/}
    mkdir -p "$colis/$(dirname "$rel")" 2>/dev/null
    if cp -p "$f" "$colis/$rel" 2>/dev/null; then
      copies=$((copies + 1))
      if [ "$secret" = 1 ]; then
        sensibles=$((sensibles + 1)); liste_sensibles="$liste_sensibles
     - $rel"
      else
        liste_copies="$liste_copies
     - $rel"
      fi
    fi
  done < <(find "$TRAVAUX" \
             \( -path '*/.venv' -o -path '*/__pycache__' -o -path '*/node_modules' -o -path '*/.git' -o -path '*/.claude' \) -prune -o \
             -type f ! -name '.root-request' ! -name '.hostinger-request' -newer "$marqueur" -print0 2>/dev/null)
  shopt -u nocasematch

  if [ "$copies" -eq 0 ]; then
    rm -rf "$colis" 2>/dev/null
    [ "$exclus" -gt 0 ] && printf '%s\n' "(Note : $exclus fichier(s) trop lourd(s) non inclus ; aucun autre livrable produit.)"
    return
  fi

  tar -czf "$archive" -C "$colis" . 2>/dev/null
  chown -R "$HERMES_UID:$HERMES_UID" "$colis" 2>/dev/null
  chown "$HERMES_UID:$HERMES_UID" "$archive" 2>/dev/null
  chmod -R u+rwX,go+rX "$colis" 2>/dev/null
  chmod 644 "$archive" 2>/dev/null

  printf '%s\n' ">>> ARTEFACTS LIVRES : $copies fichier(s) produits par l'ouvrier <<<" \
    "Recupere-les dans le casier  out/$nom.colis/  (a cote de cette reponse)," \
    "ou desarchive l'archive prete  out/$nom.tar.gz"
  [ -n "$liste_copies" ] && printf '%s\n' "Fichiers:$liste_copies"
  if [ "$sensibles" -gt 0 ]; then
    printf '%s\n' "" \
      "/!\\ Ce colis contient AUSSI $sensibles fichier(s) de config/secret (cle/jeton) necessaires pour faire tourner le code :$liste_sensibles" \
      "    Ils sont livres dans le casier, SUR LE SERVEUR (la cle voyage avec la machine)." \
      "    NE recopie JAMAIS leur contenu en clair dans Telegram ou un message : si tu dois reutiliser une cle, lis-la sur le serveur, ne la colle pas sur une carte postale."
  fi
  if [ "$exclus" -gt 0 ]; then
    printf '%s\n' "" "($exclus fichier(s) trop lourd(s) non inclus :$liste_exclus)"
  fi
}

# --- Kill-switch d'urgence : si le fichier STOP existe, on ne traite rien -----
if [ -f "$QUEUE/STOP" ]; then log "STOP present -- aiguilleur en pause"; exit 0; fi

# --- Verrou : une seule instance a la fois (evite les doublons) --------------
exec 9>"$QUEUE/.aiguilleur.lock"
flock -n 9 || { log "deja en cours -- on saute cette passe"; exit 0; }

# Filet de securite : nettoie les fichiers temporaires de l'ordre en cours quoi
# qu'il arrive (interruption, kill du script) -- evite l'accumulation de /tmp/tmp.*.
MARQUEUR=""; TMPRESULT=""
trap 'rm -f "$MARQUEUR" "$TMPRESULT" 2>/dev/null' EXIT

# --- TABLEAU DE BORD : vue d'ensemble permanente pour Hermes (rafraichi a chaque passe) ---
[ -x "$ETATGW" ] && "$ETATGW" "$ETAT_OUT" "$HERMES_UID" 2>/dev/null

shopt -s nullglob
for f in "$QUEUE/in/"*; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  ORDRE=$(cat "$f")
  if [ -z "$ORDRE" ]; then
    printf '%s' "[AIGUILLEUR] Ordre vide ou illisible -- rien a traiter (souci de transmission, pas un blocage de l'ouvrier)." > "$QUEUE/out/$name.out"
    chown "$HERMES_UID:$HERMES_UID" "$QUEUE/out/$name.out" 2>/dev/null
    chmod 644 "$QUEUE/out/$name.out"
    mv "$f" "$QUEUE/done/$name"
    log "VIDE $name (ordre vide) -> out"
    continue
  fi

  # --- ROUTAGE : si l'ordre cible le PC (@pc en tete) -> on le transfere a la file PC
  #     (relevee par le poller du PC VOTRE_USER). L'aiguilleur VPS ne le traite PAS lui-meme.
  if printf '%s' "$ORDRE" | head -1 | grep -qiE '^@pc([[:space:]]|$)'; then
    mkdir -p "$PCQUEUE/in"
    mv "$f" "$PCQUEUE/in/$name"
    chown 10000:10000 "$PCQUEUE/in/$name" 2>/dev/null
    log "ROUTE $name -> file PC (@pc)"
    continue
  fi

  # --- EFFORT + MODELE : tags en tete (poses par la salle de controle).
  #     @effort=low|medium|high|xhigh|max  (ou @max, alias historique de "max")
  #     @model=claude-opus-4-8|claude-sonnet-4-6|claude-fable-5|claude-haiku-4-5
  #     On les EPLUCHE de la 1re ligne dans N'IMPORTE QUEL ORDRE (boucle robuste :
  #     que la salle ou un humain tape @effort puis @model ou l'inverse, ca marche),
  #     le reste de la ligne = la mission. A defaut, niveaux PERMANENTS ecrits par
  #     la salle (effort-defaut-vps / modele-defaut-vps), sinon rien (comme avant).
  NIV_EFFORT=""
  MODELE_ID=""
  n=0
  while [ "$n" -lt 6 ]; do            # cap a 6 : garde-fou anti-boucle (2 tags max attendus)
    n=$((n + 1))
    PREMIERE=$(printf '%s' "$ORDRE" | head -1)
    if printf '%s' "$PREMIERE" | grep -qiE '^@effort=(low|medium|high|xhigh|max)([[:space:]]|$)'; then
      NIV_EFFORT=$(printf '%s' "$PREMIERE" | sed -E '1s/^@effort=([A-Za-z]+).*/\1/' | tr 'A-Z' 'a-z')
      ORDRE=$(printf '%s' "$ORDRE" | sed -E '1s/^@effort=[A-Za-z]+[[:space:]]*//')
    elif printf '%s' "$PREMIERE" | grep -qiE '^@max([[:space:]]|$)'; then
      NIV_EFFORT="max"
      ORDRE=$(printf '%s' "$ORDRE" | sed '1s/^@max[[:space:]]*//')
    elif printf '%s' "$PREMIERE" | grep -qiE '^@model=[A-Za-z0-9._-]+([[:space:]]|$)'; then
      MODELE_ID=$(printf '%s' "$PREMIERE" | sed -E '1s/^@model=([A-Za-z0-9._-]+).*/\1/')
      ORDRE=$(printf '%s' "$ORDRE" | sed -E '1s/^@model=[A-Za-z0-9._-]+[[:space:]]*//')
    else
      break
    fi
  done
  # Defauts permanents si aucun tag correspondant n'a ete fourni.
  if [ -z "$NIV_EFFORT" ]; then
    DEF=$(cat /root/.hermes/effort-defaut-vps 2>/dev/null | tr -d '[:space:]')
    case "$DEF" in low|medium|high|xhigh|max) NIV_EFFORT="$DEF";; esac
  fi
  if [ -z "$MODELE_ID" ]; then
    DEFM=$(cat /root/.hermes/modele-defaut-vps 2>/dev/null | tr -d '[:space:]')
    case "$DEFM" in claude-opus-4-8|claude-sonnet-4-6|claude-fable-5|claude-haiku-4-5) MODELE_ID="$DEFM";; esac
  fi
  # Garde-fou : les efforts xhigh/max n'existent que sur Opus. Avec un autre
  # modele, on plafonne a high (sinon claude renverrait une erreur 400).
  if [ -n "$MODELE_ID" ] && ! printf '%s' "$MODELE_ID" | grep -qi '^claude-opus'; then
    case "$NIV_EFFORT" in xhigh|max) NIV_EFFORT="high";; esac
  fi
  # Drapeaux finaux passes a claude
  EFFORT_FLAG=""; [ -n "$NIV_EFFORT" ] && EFFORT_FLAG="--effort $NIV_EFFORT"
  MODELE_FLAG=""; [ -n "$MODELE_ID" ] && MODELE_FLAG="--model $MODELE_ID"

  # --- ULTRACODE PAR DEFAUT (VPS) : regle dans la salle (fichier present = actif).
  #     Si actif ET que la mission ne demande pas deja "ultracode", on prefixe le
  #     mot-cle -> l'ouvrier passe en mode multi-agents pour CET ordre aussi.
  if [ -f /root/.hermes/ultracode-defaut-vps ] && ! printf '%s' "$ORDRE" | head -1 | grep -qiw ultracode; then
    ORDRE="ultracode $ORDRE"
  fi

  # --- CONSIGNES DU PATRON (Mehdi) : injectees en tete de CHAQUE mission ---------
  #     Mehdi depose ses consignes permanentes dans consignes-patron.txt ; tout
  #     ouvrier VPS les recoit AVANT sa mission, sans passer par Hermes (canal direct).
  if [ -s "$CONSIGNES_PATRON" ]; then
    ORDRE="=== CONSIGNES PERMANENTES DU PATRON (Mehdi) -- a respecter pour CETTE tache ===
$(cat "$CONSIGNES_PATRON")

=== TA MISSION ===
$ORDRE"
  fi

  log "TRAITE $name (effort: ${NIV_EFFORT:-xhigh par defaut}, modele: ${MODELE_ID:-config ouvrier})"

  # --- Drapeaux du copilote : on repart d'une ardoise vierge pour CET ordre ---
  #     (le copilote ecrit en tant qu'ouvrier ; on vide donc en tant qu'ouvrier)
  $RUNAS bash -c ": > $ALERTES" 2>/dev/null
  # Sas root : ardoise vierge aussi pour les demandes d'action root de CET ordre.
  $RUNAS bash -c ": > $TRAVAUX/.root-request" 2>/dev/null
  # Sas Hostinger : ardoise vierge aussi pour les demandes pare-feu de CET ordre.
  $RUNAS bash -c ": > $TRAVAUX/.hostinger-request" 2>/dev/null

  # --- SUIVI EN DIRECT : l'ouvrier emet un FLUX (stream-json) de TOUT ce qu'il fait.
  #     format-live le transforme AU FUR ET A MESURE en un journal lisible dans
  #     out/<ordre>.live -> Hermes lit ce fichier et voit le travail action par action,
  #     EXACTEMENT comme Mehdi voit CC travailler. (remplace l'ancien battement de coeur)
  #     Pipe en avant-plan : python (root) alimente le .live en temps reel ;
  #     PIPESTATUS[0] = code de claude (124 = timeout).
  LIVE="$QUEUE/out/$name.live"
  # Fiche telemetrie de CET ordre (cout/duree/tokens) : posee a cote du .out et
  # CONSERVEE (contrairement au .live qu'on efface). modele/effort = ce qui a ete
  # demande ; le cout/duree viennent du flux de l'ouvrier (null si interrompu).
  META="$QUEUE/out/$name.meta"
  # Repere temporel : tout fichier modifie APRES = produit pendant l'ordre (artefacts).
  MARQUEUR=$(mktemp)
  TMPRESULT=$(mktemp)
  timeout "$TIMEOUT" $RUNAS "$CAGE" claude -p "$ORDRE" \
             --settings "$SETTINGS" --permission-mode dontAsk $EFFORT_FLAG $MODELE_FLAG \
             --output-format stream-json --verbose --max-turns 120 </dev/null 2>&1 \
    | python3 "$FORMATLIVE" "$LIVE" "$TMPRESULT" "$HERMES_UID" "$META" "$MODELE_ID" "$NIV_EFFORT" vps
  STATUS=${PIPESTATUS[0]}
  RESULT=$(cat "$TMPRESULT" 2>/dev/null); rm -f "$TMPRESULT"
  [ "$STATUS" = 124 ] && RESULT="[AIGUILLEUR] Ordre interrompu : depassement du delai de 60 min."
  [ -z "$RESULT" ] && RESULT="[AIGUILLEUR] (l'ouvrier n'a pas renvoye de reponse finale)"

  # --- Annexer les drapeaux leves par le copilote (pour qu'Hermes les voie) ---
  #     Ordre normal = aucun drapeau -> rien d'ajoute. Sinon Hermes voit l'alerte.
  if [ -s "$ALERTES" ]; then
    DRAPEAUX=$($RUNAS python3 "$FORMAT" < "$ALERTES" 2>/dev/null)
    [ -n "$DRAPEAUX" ] && RESULT="$RESULT
$DRAPEAUX"
  fi

  # --- SAS ROOT : executer les actions root demandees par l'ouvrier -------------
  #     L'ouvrier a pu ecrire des chemins de scripts dans travaux/.root-request.
  #     root-gateway les valide (refuse les catastrophes) et les execute EN ROOT.
  #     Avant les artefacts : une install root peut produire des fichiers/configs.
  if [ -s "$TRAVAUX/.root-request" ]; then
    RGOUT=$(while IFS= read -r req; do [ -n "$req" ] && "$ROOTGW" "$req"; done < "$TRAVAUX/.root-request" 2>&1)
    : > "$TRAVAUX/.root-request"
    [ -n "$RGOUT" ] && RESULT="$RESULT

=== SAS ROOT (actions root executees pour l'ouvrier) ===
$RGOUT"
  fi

  # --- SAS HOSTINGER : actions pare-feu Hostinger demandees par l'ouvrier --------
  #     L'ouvrier (sans root, sans jeton) ecrit des demandes simples dans
  #     travaux/.hostinger-request (une par ligne : "diag" | "open <port> <tcp|udp>"
  #     | "close <port> <tcp|udp>"). Le guichet (root) lit le jeton du coffre,
  #     valide l'action et l'execute via l'API officielle Hostinger -- rien d'autre.
  #     => l'ouvrier gere le pare-feu tout seul ; Mehdi n'a plus a toucher hPanel.
  if [ -s "$TRAVAUX/.hostinger-request" ]; then
    HGOUT=$(while IFS= read -r v p pr _; do
              [ -n "$v" ] && "$HOSTGW" "$v" "$p" "$pr"
            done < "$TRAVAUX/.hostinger-request" 2>&1)
    : > "$TRAVAUX/.hostinger-request"
    [ -n "$HGOUT" ] && RESULT="$RESULT

=== SAS HOSTINGER (pare-feu gere pour l'ouvrier) ===
$HGOUT"
  fi

  # --- ARTEFACTS : recuperer les FICHIERS produits par l'ouvrier pendant l'ordre
  #     et les joindre (casier + archive) ; le resume part dans le compte-rendu.
  ART=$(recuperer_artefacts "$MARQUEUR" "$name")
  rm -f "$MARQUEUR"
  [ -n "$ART" ] && RESULT="$RESULT

$ART"

  # --- COLIS LIVRE : on pose la reponse finale, PUIS on retire le suivi -------
  #     Ordre important : le .out apparait AVANT que le .status disparaisse, donc
  #     Hermes ne voit jamais un trou (il voit soit "en cours", soit "livre").
  printf '%s' "$RESULT" > "$QUEUE/out/$name.out"
  chown "$HERMES_UID:$HERMES_UID" "$QUEUE/out/$name.out" 2>/dev/null
  chmod 644 "$QUEUE/out/$name.out"
  rm -f "$LIVE"

  # --- Archiver l'ordre traite ------------------------------------------------
  mv "$f" "$QUEUE/done/$name"
  log "FINI $name (status $STATUS) -> out/$name.out"
done
