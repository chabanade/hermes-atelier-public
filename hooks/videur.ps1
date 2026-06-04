<#
================================================================================
  VIDEUR  --  Garde-fou DETERMINISTE (hook PreToolUse) de l'ouvrier Claude.
================================================================================

  ROLE (analogie) : le videur a l'entree d'une boite de nuit.
  Il voit CHAQUE action que l'ouvrier veut faire, AVANT qu'elle se produise,
  et il decide : on laisse passer (allow), on bloque (deny), ou on demande (ask).

  POURQUOI c'est un VRAI rempart (et pas une simple consigne) :
  - Ce code est applique par le HARNAIS de Claude Code, pas par le modele.
  - L'ouvrier (le modele) NE PEUT PAS desactiver ce videur lui-meme.
  - Une injection ("ignore tes regles") ne change rien : c'est le code qui tranche.

  SEULE faille connue : le mode "--dangerously-skip-permissions" desactive TOUT
  (hooks compris). DONC : l'ouvrier ne doit JAMAIS etre lance avec ce mode,
  et ce videur bloque lui-meme toute commande qui tenterait de le remettre.

  PRINCIPE : DENY prime sur ALLOW. En cas de doute -> ASK (echec sur, pas passage).
  Si le videur lui-meme plante -> on BLOQUE (fail-closed).

  Format d'entree (stdin, JSON)  : { tool_name, tool_input{...}, cwd, ... }
  Format de sortie (stdout, JSON): { hookSpecificOutput:{ hookEventName,
                                     permissionDecision, permissionDecisionReason } }
================================================================================
#>

$ErrorActionPreference = 'Stop'

# --- Zone de travail autorisee a l'ecriture (le "bac a sable" de l'ouvrier) ---
$SCRATCH  = 'C:\Users\VOTRE_USER\claude-relay\workspace'
$JOURNAL  = 'C:\Users\VOTRE_USER\claude-relay\journal\videur.log'

# ------------------------------------------------------------------ Journal ---
function Write-Journal([string]$decision, [string]$tool, [string]$detail) {
    try {
        $dir = Split-Path -Parent $JOURNAL
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        $ts = (Get-Date).ToString('o')
        Add-Content -Path $JOURNAL -Value "$ts`t$decision`t$tool`t$detail" -Encoding UTF8
    } catch { }   # le journal ne doit jamais bloquer la decision
}

# ------------------------------------------------- Emission de la decision ---
function Emit([string]$decision, [string]$tool, [string]$reason) {
    Write-Journal $decision $tool $reason
    $out = @{
        hookSpecificOutput = @{
            hookEventName            = 'PreToolUse'
            permissionDecision       = $decision           # allow | deny | ask
            permissionDecisionReason = $reason
        }
    }
    [Console]::Out.Write(($out | ConvertTo-Json -Compress -Depth 6))
    exit 0
}
function Allow([string]$tool, [string]$r) { Emit 'allow' $tool "OK : $r" }
function Deny ([string]$tool, [string]$r) { Emit 'deny'  $tool "BLOQUE : $r" }
function Ask  ([string]$tool, [string]$r) { Emit 'ask'   $tool "A VALIDER : $r" }

# ============================================================================
#  LISTES DE POLITIQUE  (faciles a ajuster -- c'est le coeur de la securite)
# ============================================================================

# --- DENY commande : motifs INTERDITS dans une commande shell (insensible casse)
$DENY_CMD = @(
    # 1) Auto-elevation : empecher l'ouvrier de retirer son propre videur
    'dangerously-skip-permissions', '--permission-mode', '--setting-sources',
    '--settings\b', 'disableAllHooks', 'setup-token', '\bclaude\b.*\s-p\b',
    # 2) Toucher au videur / aux reglages / aux hooks
    '\.claude[\\/](settings|hooks)', 'claude-relay[\\/](hooks|ouvrier-settings)',
    # 3) Git qui sort de la machine ou detruit l'historique
    'git\s+push', 'git\s+remote', 'git\s+reset\s+--hard', 'git\s+clean\b',
    'git\s+config\b', 'git\s+filter-branch', 'git\s+update-ref',
    # 4) Suppression / ecrasement (commandes + alias + cmdlets d'ecriture)
    '\brm\b', '\brmdir\b', '\bRemove-Item\b', '\bdel\b', '\berase\b',
    '\bri\b', '\brd\b', '\bmv\b', '\bmove\b',
    '\bFormat-', '\bmkfs', '\bRemove-ItemProperty\b',
    '\bOut-File\b', '\bSet-Content\b', '\bAdd-Content\b', '\bClear-Content\b',
    '\bTee-Object\b', '\bSet-ItemProperty\b', '\bNew-Item\b',
    # 4bis) Redirection vers un fichier (ecrit/ecrase hors outil Write) -- pas 2>&1 ni >$null
    '(?<![0-9&])>>?\s*(?!&|\$null|/dev/null|nul\b)["''$\w./\\:-]',
    # 5) Reseau sortant / exfiltration (telechargement, upload, DNS, tunnels)
    'Invoke-RestMethod', 'Invoke-WebRequest', 'Net\.WebClient', 'Start-BitsTransfer',
    'bitsadmin', '\bcurl\b', '\bwget\b', '\biwr\b', '\birm\b',
    'DownloadString', 'DownloadFile', 'UploadString', 'UploadFile', 'UploadValues',
    'Resolve-DnsName', '\bnslookup\b', 'Test-NetConnection',
    '\bscp\b', '\bsftp\b', '\bssh\b', '\bnc\b', '\bncat\b', '\btelnet\b',
    # 6) Execution dynamique / encodee (le videur ne voit pas l'interieur)
    '\bInvoke-Expression\b', '\biex\b', '\[scriptblock\]', 'FromBase64String',
    '-e(nc|ncodedcommand)\b', '-Encoded', '\bStart-Process\b',
    # 7) Elevation de privileges
    '-Verb\s+RunAs', '\brunas\b', '\bsudo\b',
    # 8) Lecture de SECRETS via le shell (voir aussi $SECRET pour Read/Grep/Write)
    '\.env\b', '(^|[\\/])\.ssh', 'id_ed25519', 'id_rsa', 'id_ecdsa', 'id_dsa',
    '\.root_password', 'mcp-tokens', 'git-credentials', '\.npmrc', '\.netrc',
    'credentials', 'CREDENTIALS', 'settings\.local\.json', '\.mcp\.json',
    '\.pem\b', '\.ppk\b', '\.pfx\b', '\.p12\b', '[\\/]\.aws[\\/]'
)

# --- DENY MCP : outils MCP d'ECRITURE / actes irreversibles (motif sur tool_name)
$DENY_MCP = @(
    'mcp__.*__save_memory', 'mcp__.*__correct_fact', 'mcp__.*__lexia_alert',
    'mcp__.*authenticat',                 # couvre authenticate ET complete_authentication
    'mcp__.*Gmail.*',
    'mcp__.*Slack__slack_(send|schedule|create_canvas|update_canvas)',
    'mcp__.*Notion__notion-(create|update|move|duplicate)',
    'mcp__.*Google_Drive__(create|copy)_file',
    'mcp__.*google-forms__(create_form|batch_update_form)',
    'mcp__.*Docusign.*', 'mcp__.*Uber__publish',
    # filet generique : un VERBE d'ecriture present comme MOT (borne par _ - ou bord)
    '(^|[_-])(create|update|delete|remove|send|write|insert|post|publish|move|duplicate|schedule|save|set|upload|patch|revoke|grant|approve|cancel)([_-]|$)'
)

# --- ALLOW MCP : outils MCP de LECTURE seule -- un VERBE de lecture comme MOT
$ALLOW_MCP = @(
    '(^|[_-])(search|get|list|read|find|lookup|fetch|download|query|resolve|veille|recommend|status|catalog|estimates)([_-]|$)'
)

# --- ALLOW outils internes inoffensifs (organisation / lecture web / chargement d'outils)
#     ToolSearch = charge les DEFINITIONS d'outils (inoffensif) ; l'APPEL reel reste filtre.
$ALLOW_TOOL = @('TodoWrite','WebSearch','WebFetch','BashOutput','Task','ToolSearch','ExitPlanMode')

# --- SECRET : marqueurs de fichiers sensibles (jamais lus NI ecrits par l'ouvrier)
#     (volontairement LARGE : trop bloquer une LECTURE est sans danger, juste genant)
$SECRET = @(
    '\.env\b', '(^|[\\/])\.ssh', 'id_ed25519', 'id_rsa', 'id_ecdsa', 'id_dsa',
    '\.root_password', 'mcp-tokens', 'git-credentials', '\.npmrc', '\.netrc',
    'credentials', 'CREDENTIALS', 'settings\.local\.json', '\.mcp\.json',
    '\.pem\b', '\.key\b', '\.ppk\b', '\.pfx\b', '\.p12\b',
    '[\\/]\.aws([\\/]|$)', '[\\/]creds?([\\/]|$)', '[\\/]secrets?([\\/]|$)', '[\\/]vault([\\/]|$)'
)

# --- SAFE commande : segments shell de LECTURE/inspection sans danger (ancres ^)
$SAFE_CMD = @(
    '^\s*git\s+(status|diff|log|show|branch|add\b|commit|stash|fetch|rev-parse|ls-files|blame|tag\b|describe)',
    '^\s*(ls|dir|pwd|cd|Get-ChildItem|Get-Location|Set-Location|tree)\b',
    '^\s*(cat|type|Get-Content|head|tail|more|less|Select-String|findstr|grep|rg)\b',
    '^\s*(echo|Write-Output|Write-Host|printf)\b',
    '^\s*(Test-Path|Resolve-Path|Get-Item|Measure-Object|Sort-Object|Where-Object|ForEach-Object|Select-Object)\b'
)

# ============================================================================
#  DECISION  (tout est enveloppe : la moindre erreur => on BLOQUE)
# ============================================================================
try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { Deny '?' 'entree vide (videur fail-closed)' }
    $call  = $raw | ConvertFrom-Json
    $tool  = [string]$call.tool_name
    $tin   = $call.tool_input
    $cwd   = [string]$call.cwd
    if ([string]::IsNullOrWhiteSpace($cwd)) { $cwd = $SCRATCH }

    # ---- 1) Outils MCP -----------------------------------------------------
    if ($tool -like 'mcp__*') {
        foreach ($p in $DENY_MCP)  { if ($tool -imatch $p) { Deny $tool "outil MCP d'ecriture/irreversible (motif '$p')" } }
        foreach ($p in $ALLOW_MCP) { if ($tool -imatch $p) { Allow $tool 'outil MCP de lecture seule' } }
        Ask $tool 'outil MCP non classe -- validation requise'
    }

    # ---- 2) Lecture de fichiers : INTERDITE sur les fichiers sensibles -----
    if ($tool -in @('Read','Grep','Glob','NotebookRead')) {
        $paths = "$($tin.file_path) $($tin.path) $($tin.notebook_path) $($tin.pattern)"
        foreach ($s in $SECRET) { if ($paths -imatch $s) { Deny $tool "lecture d'un fichier sensible (motif '$s')" } }
        Allow $tool 'lecture / recherche de fichiers (hors fichiers sensibles)'
    }

    # ---- 2b) Outils internes inoffensifs ----------------------------------
    if ($ALLOW_TOOL -contains $tool) { Allow $tool 'outil interne de lecture/organisation' }

    # ---- 3) Ecritures de fichier : bac a sable, jamais un fichier sensible -
    if ($tool -in @('Edit','Write','NotebookEdit')) {
        $fp = [string]$tin.file_path
        if ([string]::IsNullOrWhiteSpace($fp)) { $fp = [string]$tin.notebook_path }
        if ([string]::IsNullOrWhiteSpace($fp)) { Ask $tool 'ecriture sans chemin -- validation requise' }
        foreach ($s in $SECRET) { if ($fp -imatch $s) { Deny $tool "ecriture sur un fichier sensible (motif '$s')" } }
        if (-not [System.IO.Path]::IsPathRooted($fp)) { $fp = Join-Path $cwd $fp }
        try { $full = [System.IO.Path]::GetFullPath($fp) } catch { $full = $fp }
        # IMPORTANT : exiger un separateur apres la racine, sinon "workspace_evil"
        # passerait le test (il commence par "workspace"). On compare avec "\" final.
        $rootN = [System.IO.Path]::GetFullPath($SCRATCH).TrimEnd('\').ToLower()
        $fullN = $full.TrimEnd('\').ToLower()
        if ($fullN -eq $rootN -or $fullN.StartsWith($rootN + '\')) {
            Allow $tool "ecriture dans le bac a sable ($full)"
        } else {
            Ask $tool "ecriture HORS bac a sable ($full) -- validation requise"
        }
    }

    # ---- 4) Commandes shell -----------------------------------------------
    if ($tool -eq 'Bash') {
        $cmd = [string]$tin.command
        if ([string]::IsNullOrWhiteSpace($cmd)) { Ask $tool 'commande vide' }
        # Anti-obfuscation : on retire guillemets, backticks et carets, puis on scanne
        # l'original ET le normalise (ex: git "pu""sh" -> git push ; r^m -> rm).
        $cmdNorm = $cmd.Replace('"','').Replace("'",'').Replace('`','').Replace('^','')
        # (a) DENY : un seul motif dangereux n'importe ou (dans l'un OU l'autre) => bloque
        foreach ($p in $DENY_CMD) { if (($cmd -imatch $p) -or ($cmdNorm -imatch $p)) { Deny $tool "commande dangereuse (motif '$p')" } }
        # (b) ALLOW : seulement si CHAQUE segment est une lecture sure
        $segments = ($cmd -split '(\||&&|;|`r?`n)') |
                    Where-Object { $_ -and ($_ -notmatch '^(\||&&|;)$') -and $_.Trim() }
        $allSafe = $segments.Count -gt 0
        foreach ($s in $segments) {
            $ok = $false
            foreach ($p in $SAFE_CMD) { if ($s -imatch $p) { $ok = $true; break } }
            if (-not $ok) { $allSafe = $false; break }
        }
        if ($allSafe) { Allow $tool 'commande de lecture/inspection sure' }
        Ask $tool 'commande non classee -- validation requise'
    }

    # ---- 5) Tout le reste : on demande (echec sur en mode automatique) -----
    Ask $tool 'outil non classe -- validation requise'
}
catch {
    # Le videur a rencontre une erreur : par securite, on BLOQUE.
    Deny 'videur' "erreur interne du videur ($($_.Exception.Message)) -- blocage par securite"
}
