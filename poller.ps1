# ============================================================
# Releveur de boite aux lettres - Ouvrier Claude pilote a distance (PC VOTRE_USER)
# Le PC va CHERCHER les ordres @pc deposes sur le VPS (connexion SORTANTE,
# le PC ne s'ouvre jamais), les execute avec l'ouvrier Claude DANS SA CAGE WSL
# (Linux isole par bubblewrap + videur), et renvoie le resultat dans la
# boite d'Hermes (out/), au meme endroit que les reponses du VPS.
#
# La CAGE : l'ouvrier tourne dans Ubuntu WSL, sous bubblewrap (ne voit qu'un
# bac a sable + le videur), en tant qu'utilisateur 'ouvrier' (jamais root,
# jamais les fichiers Windows). C'est l'equivalent exact du VPS.
# ============================================================
$ErrorActionPreference = "Continue"   # ne PAS planter sur un avertissement stderr
$Key  = "C:\Users\VOTRE_USER\.ssh\id_ed25519_ouvrier"
$Vps  = "root@VOTRE_IP_VPS"
$Base   = "/root/claude-queue-pc"            # boite d'ENTREE du PC, HORS de /root/.hermes (invisible d'Hermes)
$OutBox = "/root/.hermes/claude-queue/out"   # les REPONSES vont dans la boite d'Hermes
$Opt  = @("-i", $Key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3")
# ServerAliveInterval/CountMax : un SSH dont la connexion se fige (micro-coupure
# reseau, veille) est COUPE apres ~30s au lieu de pendre a l'infini et bloquer le poller.

# --- SUIVI DE COLIS : ecrit un fichier .status dans la boite d'Hermes (out/) pendant
#     le travail. Le BATTEMENT (un numero qui monte) dit a Hermes que l'ouvrier est vivant.
function Push-Status($name, $beat) {
    $txt = "[EN COURS] L'ouvrier PC (VOTRE_USER) traite cet ordre -- il TRAVAILLE, ce n'est ni perdu ni plante.`n`n  >>> BATTEMENT DE COEUR : $beat <<<`n`nTant que ce numero AUGMENTE entre deux lectures, l'ouvrier est VIVANT : patiente, ne dis JAMAIS qu'il est mort ou down.`nS'il reste FIGE au meme numero plus de 3 minutes, alors seulement previens Mehdi."
    $b = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($txt))
    try { ssh @Opt $Vps "echo $b | base64 -d > $OutBox/$name.status; chown 10000:10000 $OutBox/$name.status 2>/dev/null; chmod 644 $OutBox/$name.status" 2>$null | Out-Null } catch { }
}

# --- MISE A JOUR EN DIRECT (un seul appel SSH par tick) :
#     1. ecrit le battement (.status) -> garde l'ouvrier present dans le panneau PC ;
#     2. pousse le DETAIL action-par-action (.live-pc) lu dans la cage WSL (journal
#        current.live alimente par format-live) -> la salle l'affiche comme pour le VPS ;
#     3. rapporte une eventuelle demande de COUPURE (la salle ecrit le nom de la
#        tache dans le fichier KILL ; on renvoie son contenu pour comparaison).
function Tick-Update($name, $beat) {
    $stat = "[EN COURS] L'ouvrier PC (VOTRE_USER) traite cet ordre -- il TRAVAILLE, ce n'est ni perdu ni plante.`n`n  >>> BATTEMENT DE COEUR : $beat <<<`n`nTant que ce numero AUGMENTE entre deux lectures, l'ouvrier est VIVANT : patiente.`nS'il reste FIGE plus de 3 minutes, previens Mehdi."
    $bs = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($stat))
    $cmd = "echo $bs | base64 -d > $OutBox/$name.status; chown 10000:10000 $OutBox/$name.status 2>/dev/null; chmod 644 $OutBox/$name.status; "
    $live = "$(wsl -d Ubuntu -u ouvrier -- bash -c 'tail -c 4000 /home/ouvrier/journal/current.live 2>/dev/null')"
    if ($live.Trim()) {
        $bl = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($live))
        $cmd += "echo $bl | base64 -d > $OutBox/$name.live-pc; chown 10000:10000 $OutBox/$name.live-pc 2>/dev/null; chmod 644 $OutBox/$name.live-pc; "
    }
    $cmd += "cat $Base/KILL 2>/dev/null"
    $k = $null
    try { $k = ssh @Opt $Vps $cmd 2>$null } catch { }
    return ("$($k -join "`n")").Trim()
}

# Kill-switch local : si ce fichier existe, le releveur se met en pause (rien n'est execute).
if (Test-Path "C:\Users\VOTRE_USER\claude-relay\STOP") { Write-Host "[releveur] STOP present -- en pause."; return }

$orders = ssh @Opt $Vps "ls $Base/in/ 2>/dev/null"
if (-not $orders) { Write-Host "[releveur] Aucune commande en attente."; return }

foreach ($f in $orders) {
    $f = "$f".Trim()
    if (-not $f) { continue }
    Write-Host "[releveur] Commande recue : $f"
    $order = (ssh @Opt $Vps "cat $Base/in/$f") -join "`n"
    $order = $order -replace '(?i)^@pc\s*', ''   # retirer le tag de routage @pc avant de le donner a l'ouvrier

    # --- DEFAUTS PC : si l'ordre n'a pas de tag @effort/@model, on injecte le
    #     defaut choisi dans la salle de controle (lu sur le VPS). Les tags poses
    #     explicitement dans l'ordre priment toujours (on n'injecte que s'ils manquent).
    #     run-order.sh (cote WSL) parse ces tags et les transforme en --effort/--model.
    $firstLine = ($order -split "`n", 2)[0]
    if ($firstLine -notmatch '(?i)^@model=') {
        $defMdl = "$(ssh @Opt $Vps 'cat /root/.hermes/modele-defaut-pc 2>/dev/null')".Trim()
        if ($defMdl -match '^claude-(opus-4-8|sonnet-4-6|fable-5|haiku-4-5)$') { $order = "@model=$defMdl " + $order }
    }
    if ($firstLine -notmatch '(?i)^@effort=' -and $firstLine -notmatch '(?i)^@max(\s|$)') {
        $defEff = "$(ssh @Opt $Vps 'cat /root/.hermes/effort-defaut-pc 2>/dev/null')".Trim()
        if ($defEff -match '^(low|medium|high|xhigh|max)$') { $order = "@effort=$defEff " + $order }
    }
    # --- ULTRACODE PAR DEFAUT (PC) : si l'interrupteur de la salle est actif (fichier
    #     present sur le VPS), on passe le drapeau "ultra" a run-order.sh (qui prefixera
    #     le mot-cle "ultracode" si l'ordre ne le demande pas deja). Sinon drapeau vide.
    $ultraFlag = "$(ssh @Opt $Vps 'test -f /root/.hermes/ultracode-defaut-pc && echo ultra')".Trim()
    if ($ultraFlag -ne "ultra") { $ultraFlag = "" }
    Write-Host "[releveur] Execution par l'ouvrier Claude (cage WSL + videur)..."

    # --- L'ordre est encode en base64 : transport SUR vers WSL (aucun souci
    #     d'accents ni de quoting). run-order.sh le decode et le passe a claude
    #     comme PROMPT (jamais reinterprete comme commande shell). ---
    $b64  = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($order))
    # Journal + drapeaux vierges avant de lancer (le 1er battement ne doit pas
    # montrer l'ordre precedent). run-order les (re)cree ; on double par securite.
    wsl -d Ubuntu -u ouvrier -- bash -c ": > /home/ouvrier/journal/current.live; rm -f /home/ouvrier/journal/current.done /home/ouvrier/journal/current.result" 2>$null | Out-Null

    # --- LANCEMENT DANS UN JOB DE FOND (fiable) : on execute run-order.sh via un
    #     `wsl` BLOQUANT a l'interieur d'un Start-Job. Pourquoi ce detour :
    #       * `Start-Process wsl` ne demarrait PAS toujours la commande -> un process
    #         fantome que Windows ne voyait jamais finir, et le releveur tournait a
    #         vide jusqu'au faux "fige 75 min".
    #       * un `nohup &` detache se fait RAMASSER par WSL des que le wsl.exe lanceur
    #         sort (le distro ne garde pas un process de fond sans session).
    #     Ici le `wsl` reste au PREMIER PLAN tout le temps de l'ordre (le distro garde
    #     l'ouvrier vivant) et le job rend la boucle libre de suivre/couper. La FIN se
    #     lit sur l'ETAT DU JOB (Running -> Completed), fiable, et la reponse = sa sortie.
    $job = Start-Job -ScriptBlock {
        param($b, $u)
        wsl -d Ubuntu -u ouvrier -- /home/ouvrier/run-order.sh $b $u
    } -ArgumentList $b64, $ultraFlag

    # --- SUIVI EN DIRECT : accuse de reception immediat, puis a chaque tick (12s)
    #     on pousse le battement + le detail action-par-action (.live-pc) et on
    #     regarde une eventuelle COUPURE (fichier KILL). 1 seul appel SSH/tick.
    Push-Status $f 0
    $beat = 0; $fini = ""
    while ($job.State -eq 'Running') {
        Start-Sleep -Seconds 12
        $beat++
        $killReq = Tick-Update $f $beat
        if ($killReq -eq $f) {                       # coupure demandee pour CETTE tache
            try { & wsl -d Ubuntu -u root -- pkill -9 -u ouvrier 2>$null | Out-Null } catch { }
            try { ssh @Opt $Vps "rm -f $Base/KILL" 2>$null | Out-Null } catch { }
            $fini = "kill"; break
        }
        if ($beat -ge 375) {                         # garde-fou : 375 x 12s = 75 min max
            try { & wsl -d Ubuntu -u root -- pkill -9 -u ouvrier 2>$null | Out-Null } catch { }
            $fini = "fige"; break
        }
    }
    if ($fini -eq "kill") {
        $raw = "[releveur PC] Ordre COUPE depuis la salle de controle (process de l'ouvrier arrete)."
    } elseif ($fini -eq "fige") {
        $raw = "[releveur PC] Ordre interrompu : l'ouvrier WSL a depasse le delai (75 min, process arrete)."
    } else {
        # Job termine normalement : la reponse de l'ouvrier = la sortie standard de
        # run-order.sh. Filet : si vide, on lit le FICHIER resultat ecrit par run-order.
        $null = Wait-Job $job -Timeout 20
        $raw = (Receive-Job $job -ErrorAction SilentlyContinue) -join "`n"
        if (-not "$raw".Trim()) { $raw = "$(wsl -d Ubuntu -u ouvrier -- bash -c 'cat /home/ouvrier/journal/current.result 2>/dev/null')" }
    }
    Stop-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    $result = "$raw".Trim()
    if (-not $result) { $result = "[releveur PC] (aucune sortie de l'ouvrier)" }

    # --- Remontee des drapeaux du COPILOTE a Hermes (joints a la reponse) ---
    #     Le copilote a journalise les actions a risque pendant cet ordre.
    #     Hermes lit ces drapeaux et peut reagir (prevenir Mehdi, couper l'ouvrier).
    $alRaw = wsl -d Ubuntu -u root -- bash -c "cat /home/ouvrier/journal/alertes.jsonl 2>/dev/null"
    $alertes = @()
    foreach ($ln in ("$alRaw" -split "`n")) {
        if (-not $ln.Trim()) { continue }
        try { $j = $ln | ConvertFrom-Json; $alertes += ("  - [{0}] {1} : {2}" -f $j.niveau, $j.tool, $j.raison) } catch { }
    }
    if ($alertes.Count -gt 0) {
        $mortels = @($alertes | Where-Object { $_ -match '\[MORTEL' }).Count
        $entete = if ($mortels -gt 0) { "ALERTE COPILOTE ($mortels action(s) BLOQUEE(S) par l'ABS)" } else { "COPILOTE -- drapeaux a surveiller" }
        $result += "`n`n--- $entete : $($alertes.Count) au total ---`n" + ($alertes -join "`n")
    }

    # --- Telemetrie : la cage a ecrit une fiche current.meta (cout/duree/tokens
    #     de l'ordre, via format-live). On la pousse a cote du .out. Si elle est
    #     absente/vide (ordre coupe avant la fin), on ne pousse rien -- la salle
    #     affichera le resultat sans chiffres plutot qu'une valeur inventee.
    $metaRaw = "$(wsl -d Ubuntu -u ouvrier -- bash -c 'cat /home/ouvrier/journal/current.meta 2>/dev/null')".Trim()
    $metaCmd = ""
    if ($metaRaw) {
        $b64meta = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($metaRaw))
        $metaCmd = "echo $b64meta | base64 -d > $OutBox/$f.meta; chown 10000:10000 $OutBox/$f.meta 2>/dev/null; chmod 644 $OutBox/$f.meta; "
    }

    # --- Deposer la reponse dans la boite d'Hermes (base64 = transport sur) ---
    $b64out = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($result))
    # Livraison en UN SEUL appel SSH (atomicite logique) : le mv vers done/ n'a
    # lieu QUE si l'ecriture du .out a reussi. En cas de coupure SSH, l'ordre
    # reste dans in/ et sera retente au prochain passage -- jamais perdu.
    # NB : on n'efface PAS le .meta (il accompagne le .out, comme la telemetrie VPS).
    $deliver = "echo $b64out | base64 -d > $OutBox/$f.out && { chown 10000:10000 $OutBox/$f.out 2>/dev/null; chmod 644 $OutBox/$f.out; ${metaCmd}rm -f $OutBox/$f.status $OutBox/$f.live-pc; mv $Base/in/$f $Base/done/$f; }"
    ssh @Opt $Vps $deliver
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[releveur] ATTENTION : livraison SSH echouee (code $LASTEXITCODE) pour $f -- l'ordre sera retente au prochain passage (verifie la cle SSH / le reseau)."
    } else {
        Write-Host "[releveur] Resultat depose : out/$f.out"
    }
}
Write-Host "[releveur] Termine."
