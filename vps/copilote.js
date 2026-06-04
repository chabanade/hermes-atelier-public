#!/usr/bin/env node
/*
 * ============================================================================
 *  COPILOTE  --  hook PreToolUse de l'ouvrier Claude (mode "F1 surveillee").
 * ============================================================================
 *  Philosophie (cf. Mehdi) : on ne BRIDE pas la puissance, on la SURVEILLE.
 *  Ce n'est pas une douane. C'est un copilote + des capteurs (ABS, antiderapage).
 *
 *   - LAISSE PASSER par defaut (pleine puissance : python, node, git, builds...).
 *   - BOITE NOIRE : journalise CHAQUE action (copilote.log).
 *   - DRAPEAUX : leve une alerte (alertes.jsonl) sur les actions a risque
 *     (lecture d'un secret, sortie reseau, effacement de masse) -- SANS bloquer.
 *     Ces alertes remontent a Hermes (le copilote humain/IA) qui peut reagir.
 *   - ABS (seul reflexe automatique) : FREINE uniquement le cas mortel-irreversible
 *     -> exfiltration averee (reseau + secret dans la meme action), ou tentative
 *        de debrancher le copilote lui-meme (sinon la surveillance ne vaut rien).
 *
 *  Le vrai rempart d'isolation reste la CAGE (bubblewrap) : l'ouvrier ne voit
 *  que son perimetre (Dev + Documents + travaux), jamais les secrets systeme.
 *  Fail-OPEN : si le copilote plante, on LAISSE PASSER (on ne bride pas) + on note.
 * ============================================================================
 */
'use strict';
const fs = require('fs');

const JOURNAL = '/home/ouvrier/journal/copilote.log';    // boite noire : TOUT
const ALERTES = '/home/ouvrier/journal/alertes.jsonl';   // drapeaux -> remontes a Hermes

// --- Canaux de SORTIE reseau (un secret ne doit pas partir par la) ----------
const RESEAU = [
  '\\bcurl\\b', '\\bwget\\b', '\\biwr\\b', '\\birm\\b', '\\bnc\\b', '\\bncat\\b', '\\btelnet\\b',
  '\\bssh\\b', '\\bscp\\b', '\\bsftp\\b', '\\brsync\\b', '\\bftp\\b', '\\bgetent\\b',
  '\\bnslookup\\b', '\\bdig\\b', '\\bhost\\b', '\\btshark\\b',
  'git\\s+(push|fetch|pull|clone)', '[\\\\/]dev[\\\\/](tcp|udp)[\\\\/]',
  'Invoke-RestMethod', 'Invoke-WebRequest', 'DownloadString', 'DownloadFile', 'UploadString', 'UploadFile',
  '\\bsocket\\b', '\\burllib\\b', '\\brequests\\b', 'net\\.connect', 'http\\.request', 'httpx', 'aiohttp'
];
// --- SECRETS (chemins/fichiers sensibles) -----------------------------------
const SECRET = [
  '(^|[\\\\/])\\.ssh', 'id_ed25519', 'id_rsa', 'id_ecdsa', 'id_dsa',
  '(^|[^\\w])\\.claude([\\\\/.]|$)', 'credentials', '\\.env\\b', '\\.mcp\\.json',
  '\\.npmrc', '\\.netrc', 'git-credentials', '[\\\\/]\\.aws([\\\\/]|$)',
  '\\.pem\\b', '\\.ppk\\b', '\\.pfx\\b', '\\.p12\\b',
  '[\\\\/]proc[\\\\/][^\\\\/]+[\\\\/]environ', '[\\\\/]etc[\\\\/]shadow',
  '(^|[\\\\/])root([\\\\/]|$)', '[\\\\/]\\.hermes'
];
// --- VALEURS de secret averees (si ca apparait, le secret est en clair) ------
const TOKEN = ['sk-ant-', 'claudeAiOauth', '-----BEGIN', 'eyJ[A-Za-z0-9_\\-]{12}'];
// --- DESTRUCTION de masse ----------------------------------------------------
const DESTRUCT = [
  '\\brm\\b[^|;&\\n]*-[a-zA-Z]*[rf]', '\\bshred\\b', '\\bmkfs', '\\bdd\\b[^|;&\\n]*of=',
  '\\bFormat-', 'Remove-Item[^|;&\\n]*-Recurse', '[\\\\/]dev[\\\\/](sd[a-z]|nvme|mem)'
];
// --- NEUTRALISATION du copilote/cage (debrancher l'ABS = interdit) -----------
const NEUTRAL = [
  'dangerously-skip-permissions', 'disableAllHooks', '--permission-mode', 'setup-token',
  'ouvrier[\\\\/]hooks', 'cage\\.sh', 'copilote\\.js', 'videur\\.js', 'ouvrier-settings',
  '\\bclaude\\b[^|;&\\n]*\\s-p\\b'
];

const rx = p => new RegExp(p, 'i');
const now = () => new Date().toISOString();
function log(kind, tool, info) { try { fs.appendFileSync(JOURNAL, `${now()}\t${kind}\t${tool}\t${info}\n`); } catch (e) {} }
function alerte(niveau, tool, raison, extrait) {
  try { fs.appendFileSync(ALERTES, JSON.stringify({ ts: now(), niveau, tool, raison, extrait: String(extrait).slice(0, 240) }) + '\n'); } catch (e) {}
  log('ALERTE-' + niveau, tool, raison);
}
function emit(decision, tool, reason) {
  process.stdout.write(JSON.stringify({ hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: decision, permissionDecisionReason: reason } }));
  process.exit(0);
}

try {
  const raw = fs.readFileSync(0, 'utf8');
  if (!raw || !raw.trim()) emit('allow', '?', 'copilote: entree vide -- laisse passer');
  const call = JSON.parse(raw);
  const tool = String(call.tool_name || '');
  const tin = call.tool_input || {};

  const cmd = String(tin.command || '');
  const paths = `${tin.file_path || ''} ${tin.path || ''} ${tin.notebook_path || ''} ${tin.pattern || ''} ${tin.url || ''} ${tin.query || ''} ${tin.prompt || ''}`;
  const blob = (cmd + ' ' + paths).trim();
  const norm = blob.replace(/["'`^\\]/g, '');                 // anti-obfuscation (r\m -> rm, etc.)
  const test = arr => arr.some(p => rx(p).test(blob) || rx(p).test(norm));

  const isWeb = (tool === 'WebFetch' || tool === 'WebSearch');  // navigation : sortie "douce"
  const hasReseauBrut = test(RESEAU);                           // curl/nc/ssh/git push/socket : canal "brut"
  const hasReseau = isWeb || hasReseauBrut;                     // pour l'ABS : toute sortie compte
  // Cible LOCALE (Speaches & co sur localhost) : sortie reseau INTERNE, pas une exfil -> pas de drapeau.
  // NB : on ne touche PAS a hasReseau (l'ABS reste complet) : un secret vers localhost reste freine.
  const isLocal = /\b(localhost|127\.0\.0\.1|0\.0\.0\.0|::1|\[::1\])\b/i.test(blob);
  const hasToken = TOKEN.some(p => rx(p).test(blob));
  const hasSecret = hasToken || test(SECRET);
  const hasDestruct = test(DESTRUCT);
  const hasNeutral = test(NEUTRAL);
  // Outil MCP d'ecriture = action externe irreversible
  const isMcpWrite = tool.startsWith('mcp__') && /(^|[_-])(create|update|delete|remove|send|write|insert|post|publish|move|save|set|upload|patch|revoke|grant|approve|cancel)([_-]|$)/i.test(tool);

  // ================= ABS : les 2 SEULS freinages automatiques ================
  if (hasReseau && hasSecret) {
    alerte('MORTEL', tool, 'exfiltration : reseau + secret dans la meme action', blob);
    emit('deny', tool, 'ABS (copilote) : un secret part vers le reseau -- FREINAGE D URGENCE. Action bloquee, alerte envoyee.');
  }
  if (hasNeutral) {
    alerte('MORTEL', tool, 'tentative de debrancher le copilote / la cage', blob);
    emit('deny', tool, 'ABS (copilote) : on ne debranche pas les capteurs. Action bloquee, alerte envoyee.');
  }

  // ================= Sinon : ON LAISSE PASSER + on leve les drapeaux =========
  if (hasSecret)     alerte('RISQUE', tool, 'acces a un secret (laisse passer, surveille)', blob);
  if (hasReseauBrut && !isLocal) alerte('RISQUE', tool, 'sortie reseau brute curl/nc/ssh/git-push/socket (laisse passer, surveille)', blob);
  if (hasDestruct)   alerte('RISQUE', tool, 'effacement de masse (laisse passer, surveille)', blob);
  if (isMcpWrite)    alerte('RISQUE', tool, 'ecriture externe via MCP (laisse passer, surveille)', tool);
  // (la navigation web sans secret n'est pas une alerte -- juste tracee dans la boite noire)

  const flagged = hasSecret || (hasReseauBrut && !isLocal) || hasDestruct || isMcpWrite;
  log(flagged ? 'PASS-RISQUE' : 'PASS', tool, blob.slice(0, 160));
  emit('allow', tool, flagged ? 'copilote: action a risque LAISSEE PASSER + alerte au copilote' : 'copilote: action normale');
} catch (e) {
  // Fail-OPEN : on ne bride pas la F1 si le copilote bug -- mais on le note.
  log('ERREUR', 'copilote', e.message);
  emit('allow', 'copilote', 'copilote: erreur interne -- laisse passer (mode F1) : ' + e.message);
}
