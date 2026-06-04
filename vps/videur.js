#!/usr/bin/env node
/*
 * ============================================================================
 *  VIDEUR (Linux / Node) -- 2e ceinture (hook PreToolUse) de l'ouvrier VPS.
 * ============================================================================
 *  La 1re ceinture (le vrai rempart) est la CAGE bubblewrap (cage.sh) :
 *  pas de /root, pas de secrets Hermes, ecriture limitee a ~/travaux.
 *  Ce videur reproduit la politique du videur Windows (videur.ps1) : il bloque
 *  PAR CODE les actes irreversibles que la cage ne couvre pas (git push, MCP
 *  d'ecriture, save_memory AGEA, exfiltration...). DENY > ALLOW > sinon ASK.
 *  Fail-closed : toute erreur du videur => on BLOQUE.
 *
 *  Entree (stdin, JSON)  : { tool_name, tool_input{...}, cwd, ... }
 *  Sortie (stdout, JSON) : { hookSpecificOutput:{ hookEventName,
 *                            permissionDecision, permissionDecisionReason } }
 * ============================================================================
 */
'use strict';
const fs = require('fs');
const path = require('path');

const SCRATCH = '/home/ouvrier/travaux';                 // bac a sable (ecriture)
const JOURNAL = '/home/ouvrier/journal/videur.log';      // journal append-only

function journal(decision, tool, detail) {
  try { fs.appendFileSync(JOURNAL, `${new Date().toISOString()}\t${decision}\t${tool}\t${detail}\n`); } catch (e) {}
}
function emit(decision, tool, reason) {
  journal(decision, tool, reason);
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: decision, permissionDecisionReason: reason }
  }));
  process.exit(0);
}
const allow = (t, r) => emit('allow', t, 'OK : ' + r);
const deny  = (t, r) => emit('deny',  t, 'BLOQUE : ' + r);
const ask   = (t, r) => emit('ask',   t, 'A VALIDER : ' + r);

// ===========================================================================
//  POLITIQUE (identique au videur Windows, adaptee Linux)
// ===========================================================================
const DENY_CMD = [
  // 1) Auto-elevation
  'dangerously-skip-permissions', '--permission-mode', '--setting-sources', '--settings\\b',
  'disableAllHooks', 'setup-token', '\\bclaude\\b.*\\s-p\\b',
  // 2) Toucher au videur / reglages / hooks
  '\\.claude[\\\\/](settings|hooks)', 'ouvrier[\\\\/]hooks', 'cage\\.sh',
  // 3) Git sortant / destructeur
  'git\\s+push', 'git\\s+remote', 'git\\s+reset\\s+--hard', 'git\\s+clean\\b',
  'git\\s+config\\b', 'git\\s+filter-branch', 'git\\s+update-ref',
  // 4) Suppression / ecrasement (+ alias + cmdlets PowerShell au cas ou)
  '\\brm\\b', '\\brmdir\\b', '\\bRemove-Item\\b', '\\bdel\\b', '\\berase\\b',
  '\\bri\\b', '\\brd\\b', '\\bmv\\b', '\\bmove\\b', '\\bshred\\b', '\\btruncate\\b',
  '\\bFormat-', '\\bmkfs', '\\bOut-File\\b', '\\bSet-Content\\b', '\\bAdd-Content\\b',
  '\\bClear-Content\\b', '\\bTee-Object\\b',
  // 4bis) Redirection vers un fichier (ecrit/ecrase) -- pas 2>&1 ni >/dev/null
  '(?<![0-9&])>>?\\s*(?!&|/dev/null|nul\\b)["\'$\\w./\\\\:-]',
  // 5) Reseau sortant / exfiltration
  'Invoke-RestMethod', 'Invoke-WebRequest', 'Net\\.WebClient', 'Start-BitsTransfer', 'bitsadmin',
  '\\bcurl\\b', '\\bwget\\b', '\\biwr\\b', '\\birm\\b', '\\bnc\\b', '\\bncat\\b', '\\btelnet\\b',
  'DownloadString', 'DownloadFile', 'UploadString', 'UploadFile',
  'Resolve-DnsName', '\\bnslookup\\b', '\\bdig\\b', '\\bhost\\b', 'Test-NetConnection',
  '\\bscp\\b', '\\bsftp\\b', '\\bssh\\b', '\\brsync\\b',
  // 6) Execution dynamique / encodee
  '\\bInvoke-Expression\\b', '\\biex\\b', '\\[scriptblock\\]', 'FromBase64String',
  '-e(nc|ncodedcommand)\\b', '-Encoded', '\\bStart-Process\\b', '\\bbase64\\b\\s+-d',
  // 7) Elevation de privileges
  '-Verb\\s+RunAs', '\\brunas\\b', '\\bsudo\\b', '\\bsu\\b\\s', '\\bchmod\\b.*\\bu\\+s', '\\bsetcap\\b',
  // 8) Secrets via shell
  '\\.env\\b', '(^|[\\\\/])\\.ssh', 'id_ed25519', 'id_rsa', 'id_ecdsa', 'id_dsa',
  '\\.root_password', 'mcp-tokens', 'git-credentials', '\\.npmrc', '\\.netrc',
  'credentials', 'CREDENTIALS', 'settings\\.local\\.json', '\\.mcp\\.json',
  '\\.pem\\b', '\\.ppk\\b', '\\.pfx\\b', '\\.p12\\b', '[\\\\/]\\.aws[\\\\/]',
  '[\\\\/]\\.hermes', '[\\\\/]root[\\\\/]',
  // 9) Lecture/exfiltration DETOURNEE du seul secret interne a la cage (~/.claude) :
  //    tout acces au home hors du bac a sable, au dossier d'auth, ou par ratissage recursif.
  '(^|[^\\w])\\.claude([\\\\/.]|$)',                       // .claude/  .claude.json  ~/.claude (auth)
  '/home(?![\\\\/]ouvrier[\\\\/]travaux)',                  // home hors du sandbox (/home, /home/ouvrier, .claude...)
  '(^|[^\\w])~(?![\\\\/]travaux)',                          // ~ ou ~/x hors ~/travaux
  '\\$\\{?HOME\\}?(?![\\\\/]travaux)',                      // $HOME / ${HOME} hors sandbox
  '-exec(dir)?\\b', '-delete\\b', '\\bcpio\\b',             // find -exec/-delete , cpio (ratissage/exec)
  '\\b(find|grep|egrep|fgrep|rg|ls|tar|cp)\\b[^|;&\\n]*\\s/(\\s|$)',  // ratissage depuis la racine /
  // 10) Trous identifies par le red-team (exfil reseau detournee, exec via outil sur-liste, lecture env, socket)
  'git\\s+(fetch|pull|clone)\\b',                            // git fetch/pull/clone = canal reseau sortant
  '-fprintf?\\b', '-fprint0\\b', '-fls\\b',                  // find -fprintf/-fls : ecriture hors redirection
  '\\bsystem\\s*\\(', '\\bpopen\\b', '\\bENVIRON\\b',         // exec arbitraire (awk/perl system) / lecture env
  '[\\\\/]proc[\\\\/][^\\\\/]+[\\\\/]environ',               // /proc/self|PID/environ = secrets en variables d env
  '[\\\\/]dev[\\\\/](tcp|udp)[\\\\/]', '\\bexec\\s+[0-9]+',   // socket bash /dev/tcp + exec sur descripteur
  '[\\\\/]dev[\\\\/](sd[a-z]|mem|kmem|port|nvme|mtd)',       // disque / RAM brute
  '\\b(node|deno|bun|python[0-9]*|perl|ruby|php)\\b(?!\\s+(--version|-v\\b))',  // interpreteurs (exec arbitraire) sauf --version
  '\\bln\\b', '\\btee\\b', '\\bgetent\\b', '\\bnohup\\b', '\\bcrontab\\b'  // liens, tee, resolveur DNS, persistance
];

const DENY_MCP = [
  'mcp__.*__save_memory', 'mcp__.*__correct_fact', 'mcp__.*__lexia_alert', 'mcp__.*authenticat',
  'mcp__.*Gmail.*', 'mcp__.*Slack__slack_(send|schedule|create_canvas|update_canvas)',
  'mcp__.*Notion__notion-(create|update|move|duplicate)', 'mcp__.*Google_Drive__(create|copy)_file',
  'mcp__.*google-forms__(create_form|batch_update_form)', 'mcp__.*Docusign.*', 'mcp__.*Uber__publish',
  '(^|[_-])(create|update|delete|remove|send|write|insert|post|publish|move|duplicate|schedule|save|set|upload|patch|revoke|grant|approve|cancel)([_-]|$)'
];
const ALLOW_MCP = [
  '(^|[_-])(search|get|list|read|find|lookup|fetch|download|query|resolve|veille|recommend|status|catalog|estimates)([_-]|$)'
];
// WebSearch / WebFetch / Task RETIRES : canaux de sortie reseau (exfil) ou sous-agents non filtres.
const ALLOW_TOOL = ['TodoWrite', 'BashOutput', 'ToolSearch', 'ExitPlanMode'];
const SECRET = [
  '\\.env\\b', '(^|[\\\\/])\\.ssh', 'id_ed25519', 'id_rsa', 'id_ecdsa', 'id_dsa',
  '\\.root_password', 'mcp-tokens', 'git-credentials', '\\.npmrc', '\\.netrc',
  'credentials', 'CREDENTIALS', 'settings\\.local\\.json', '\\.mcp\\.json',
  '\\.pem\\b', '\\.key\\b', '\\.ppk\\b', '\\.pfx\\b', '\\.p12\\b',
  '[\\\\/]\\.aws([\\\\/]|$)', '[\\\\/]creds?([\\\\/]|$)', '[\\\\/]secrets?([\\\\/]|$)',
  '[\\\\/]vault([\\\\/]|$)', '[\\\\/]\\.hermes', '(^|[\\\\/])root([\\\\/]|$)',
  '(^|[\\\\/])\\.claude([\\\\/.]|$)',          // ~/.claude (auth) + .claude.json
  '/home(?![\\\\/]ouvrier[\\\\/]travaux)',     // home hors du bac a sable travaux
  '[\\\\/]proc[\\\\/][^\\\\/]+[\\\\/]environ'   // /proc/self|PID/environ (variables d env)
];
const SAFE_CMD = [
  // git fetch RETIRE (canal reseau) ; awk / sed RETIRES (exec arbitraire via system()/commande e)
  '^\\s*git\\s+(status|diff|log|show|branch|add\\b|commit|stash|rev-parse|ls-files|blame|tag\\b|describe|switch\\s+-c|checkout\\s+-b)',
  '^\\s*(ls|dir|pwd|cd|tree|stat|file|wc|sort|uniq|cut|date|whoami|hostname|uname|id|basename|dirname|realpath|which|type|seq|sleep|clear)\\b',
  '^\\s*(cat|head|tail|less|more|grep|rg|find)\\b',
  '^\\s*(echo|printf|true|test|node\\s+--version|npm\\s+--version)\\b'
];

const rx = p => new RegExp(p, 'i');

// ===========================================================================
//  DECISION (tout est enveloppe : la moindre erreur => BLOQUE)
// ===========================================================================
try {
  const raw = fs.readFileSync(0, 'utf8');
  if (!raw || !raw.trim()) deny('?', 'entree vide (videur fail-closed)');
  const call = JSON.parse(raw);
  const tool = String(call.tool_name || '');
  const tin = call.tool_input || {};
  let cwd = String(call.cwd || '') || SCRATCH;

  // 1) Outils MCP
  if (tool.startsWith('mcp__')) {
    for (const p of DENY_MCP) if (rx(p).test(tool)) deny(tool, `outil MCP d'ecriture/irreversible (motif '${p}')`);
    for (const p of ALLOW_MCP) if (rx(p).test(tool)) allow(tool, 'outil MCP de lecture seule');
    ask(tool, 'outil MCP non classe -- validation requise');
  }

  // 2) Lecture de fichiers : interdite sur les fichiers sensibles
  if (['Read', 'Grep', 'Glob', 'NotebookRead'].includes(tool)) {
    const paths = `${tin.file_path || ''} ${tin.path || ''} ${tin.notebook_path || ''} ${tin.pattern || ''}`;
    for (const s of SECRET) if (rx(s).test(paths)) deny(tool, `lecture d'un fichier sensible (motif '${s}')`);
    allow(tool, 'lecture / recherche de fichiers (hors fichiers sensibles)');
  }

  // 2b) Outils internes inoffensifs
  if (ALLOW_TOOL.includes(tool)) allow(tool, 'outil interne de lecture/organisation');

  // 2c) Web (WebSearch / WebFetch) : AUTORISE pour recherche/doc, mais REFUSE si l'URL
  //     ou la requete pointe vers un chemin sensible ou contient une chaine type secret
  //     (un secret cache dans une URL = exfiltration ; une recherche/page normale passe).
  if (tool === 'WebFetch' || tool === 'WebSearch') {
    const u = `${tin.url || ''} ${tin.query || ''} ${tin.prompt || ''}`;
    for (const s of SECRET) if (rx(s).test(u)) deny(tool, `web : URL/requete vers un chemin sensible (motif '${s}')`);
    const LEAK = ['sk-ant-', 'claudeAiOauth', 'eyJ[A-Za-z0-9_\\-]{12}', '[A-Za-z0-9+/]{60,}={0,2}', '\\b[0-9a-f]{64,}\\b'];
    for (const p of LEAK) if (rx(p).test(u)) deny(tool, `web : chaine type secret dans l'URL/requete (exfiltration ? motif '${p}')`);
    allow(tool, 'web (recherche/recuperation) sans secret apparent dans l URL');
  }

  // 3) Ecritures de fichier : bac a sable, jamais un fichier sensible
  if (['Edit', 'Write', 'NotebookEdit'].includes(tool)) {
    let fp = String(tin.file_path || tin.notebook_path || '');
    if (!fp.trim()) ask(tool, 'ecriture sans chemin -- validation requise');
    for (const s of SECRET) if (rx(s).test(fp)) deny(tool, `ecriture sur un fichier sensible (motif '${s}')`);
    if (!path.isAbsolute(fp)) fp = path.join(cwd, fp);
    const full = path.resolve(fp);
    const root = path.resolve(SCRATCH);
    if (full === root || full.startsWith(root + path.sep)) allow(tool, `ecriture dans le bac a sable (${full})`);
    else ask(tool, `ecriture HORS bac a sable (${full}) -- validation requise`);
  }

  // 4) Commandes shell
  if (tool === 'Bash') {
    const cmd = String(tin.command || '');
    if (!cmd.trim()) ask(tool, 'commande vide');
    const cmdNorm = cmd.replace(/["'`^\\]/g, '');                     // anti-obfuscation (+ backslash : r\m -> rm)
    for (const p of DENY_CMD) if (rx(p).test(cmd) || rx(p).test(cmdNorm)) deny(tool, `commande dangereuse (motif '${p}')`);
    const segments = cmd.split(/\||&&|;|\n/).map(s => s.trim()).filter(s => s && !/^(\||&&|;)$/.test(s));
    let allSafe = segments.length > 0;
    for (const s of segments) { if (!SAFE_CMD.some(p => rx(p).test(s))) { allSafe = false; break; } }
    if (allSafe) allow(tool, 'commande de lecture/inspection sure');
    ask(tool, 'commande non classee -- validation requise');
  }

  // 5) Tout le reste : on demande (refus auto en mode dontAsk)
  ask(tool, 'outil non classe -- validation requise');
} catch (e) {
  deny('videur', `erreur interne du videur (${e.message}) -- blocage par securite`);
}
