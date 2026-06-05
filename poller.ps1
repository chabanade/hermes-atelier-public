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
    Write-Host "[releveur] Execution par l'ouvrier Claude (cage WSL + videur)..."

    # --- L'ordre est encode en base64 : transport SUR vers WSL (aucun souci
    #     d'accents ni de quoting). run-order.sh le decode et le passe a claude
    #     comme PROMPT (jamais reinterprete comme commande shell). ---
    $b64  = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($order))
    $outF = Join-Path $env:TEMP ("ouvrier-wsl-" + [guid]::NewGuid().ToString('N') + ".out")
    $errF = "$outF.err"
    $p = Start-Process wsl -ArgumentList @('-d','Ubuntu','-u','ouvrier','--','/home/ouvrier/run-order.sh',$b64) `
         -NoNewWindow -PassThru -RedirectStandardOutput $outF -RedirectStandardError $errF

    # --- SUIVI DE COLIS : accuse de reception immediat, puis battement toutes les 30s ---
    Push-Status $f 0
    $beat = 0; $fige = $false
    while (-not $p.HasExited) {
        if ($p.WaitForExit(30000)) { break }       # attend 30s OU la fin de l'ouvrier
        $beat++
        if ($beat -ge 150) {                         # garde-fou : 150 x 30s = 75 min max
            try { & wsl -d Ubuntu -u root -- pkill -9 -u ouvrier 2>$null | Out-Null } catch { }
            try { $p.Kill() } catch { }
            $fige = $true; break
        }
        Push-Status $f $beat                         # le battement monte -> Hermes voit que ca avance
    }
    if ($fige) {
        $raw = "[releveur PC] Ordre interrompu : l'ouvrier WSL a depasse le delai (75 min, process arrete)."
    } else {
        $raw = (Get-Content $outF -Raw -Encoding UTF8 -ErrorAction SilentlyContinue)
    }
    Remove-Item $outF, $errF -Force -ErrorAction SilentlyContinue
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

    # --- Deposer la reponse dans la boite d'Hermes (base64 = transport sur) ---
    $b64out = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($result))
    # Livraison en UN SEUL appel SSH (atomicite logique) : le mv vers done/ n'a
    # lieu QUE si l'ecriture du .out a reussi. En cas de coupure SSH, l'ordre
    # reste dans in/ et sera retente au prochain passage -- jamais perdu.
    $deliver = "echo $b64out | base64 -d > $OutBox/$f.out && { chown 10000:10000 $OutBox/$f.out 2>/dev/null; chmod 644 $OutBox/$f.out; rm -f $OutBox/$f.status; mv $Base/in/$f $Base/done/$f; }"
    ssh @Opt $Vps $deliver
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[releveur] ATTENTION : livraison SSH echouee (code $LASTEXITCODE) pour $f -- l'ordre sera retente au prochain passage (verifie la cle SSH / le reseau)."
    } else {
        Write-Host "[releveur] Resultat depose : out/$f.out"
    }
}
Write-Host "[releveur] Termine."
