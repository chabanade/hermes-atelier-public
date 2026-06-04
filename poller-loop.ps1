# ============================================================
#  Boucle du releveur PC (ouvrier Claude commande par Hermes via @pc).
#  Relance poller.ps1 toutes les 30 s. UNE seule instance (mutex).
#  SECURITE : chaque passe est surveillee ; si elle depasse 15 min
#  (releveur fige), le demon la TUE (tout l'arbre) et continue.
#  - Demarre au logon (raccourci dans le dossier Demarrage).
#  - Se met en pause si le fichier claude-relay\STOP existe.
# ============================================================
$created = $false
$mutex = New-Object System.Threading.Mutex($true, "OuvrierPC-Releveur-Loop", [ref]$created)
if (-not $created) { exit }   # une autre instance tourne deja -> on sort

try {
    while ($true) {
        if (-not (Test-Path "C:\Users\VOTRE_USER\claude-relay\STOP")) {
            $p = Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Users\VOTRE_USER\claude-relay\poller.ps1' `
                 -WindowStyle Hidden -PassThru -RedirectStandardOutput "C:\Users\VOTRE_USER\claude-relay\poller-loop.log"
            if (-not $p.WaitForExit(4800000)) {       # garde-fou : une passe ne doit JAMAIS depasser ~80 min (ouvrier 60 min + marge)
                try { & taskkill /PID $p.Id /T /F 2>$null | Out-Null } catch { }
            }
        }
        Start-Sleep -Seconds 30
    }
} finally {
    $mutex.ReleaseMutex()
}
