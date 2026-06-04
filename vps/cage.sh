#!/bin/bash
# ============================================================================
#  CAGE bubblewrap de l'ouvrier Claude (VPS).
# ============================================================================
#  FS visible LIMITE : systeme en lecture seule + SEULEMENT :
#    - travaux  (rw)  : le bac a sable de l'ouvrier
#    - hooks    (ro)  : le videur
#    - journal  (rw)  : la trace du videur
#    - .claude  (rw)  : l'auth (credentials + refresh) + sessions
#    - .claude.json (rw) : la config (evite le warning, ecrite par claude)
#  PAS de /root, PAS de /root/.hermes, PAS de secrets Hermes. Reseau partage.
# ============================================================================
exec bwrap \
  --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /lib /lib \
  $( [ -d /lib64 ] && echo --ro-bind /lib64 /lib64 ) \
  --ro-bind /etc /etc \
  $( RC=$(readlink -f /etc/resolv.conf 2>/dev/null); [ -n "$RC" ] && [ "$RC" != /etc/resolv.conf ] && echo --ro-bind "$RC" "$RC" ) \
  --proc /proc --dev /dev --tmpfs /tmp \
  --bind /home/ouvrier/travaux /home/ouvrier/travaux \
  --ro-bind /home/ouvrier/hooks /home/ouvrier/hooks \
  --bind /home/ouvrier/journal /home/ouvrier/journal \
  $( [ -d /home/ouvrier/.claude ] && echo --bind /home/ouvrier/.claude /home/ouvrier/.claude ) \
  $( [ -f /home/ouvrier/.claude.json ] && echo --bind /home/ouvrier/.claude.json /home/ouvrier/.claude.json ) \
  --setenv HOME /home/ouvrier \
  --unshare-pid --unshare-ipc --unshare-uts --die-with-parent \
  --chdir /home/ouvrier/travaux \
  "$@"
