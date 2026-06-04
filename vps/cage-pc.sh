#!/bin/bash
# ============================================================================
#  CAGE bubblewrap de l'ouvrier Claude -- version PC (VOTRE_USER / WSL).
# ============================================================================
#  Identique au VPS, MAIS le bac a sable s'ouvre sur les VRAIS dossiers de
#  travail de Mehdi (acces complet, c'est le but du PC) :
#    - travaux           (rw) : scratch interne de l'ouvrier
#    - travaux/Dev       (rw) : C:\Users\VOTRE_USER\Dev  -> tous les projets
#    - travaux/Documents (rw) : C:\Users\VOTRE_USER\Documents
#  Le RESTE du PC (secrets systeme, .ssh Windows, AppData, C:\ complet) reste
#  DEHORS. La surveillance (copilote) gere les risques, pas un mur.
# ============================================================================
exec bwrap \
  --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /lib /lib \
  $( [ -d /lib64 ] && echo --ro-bind /lib64 /lib64 ) \
  --ro-bind /etc /etc \
  $( RC=$(readlink -f /etc/resolv.conf 2>/dev/null); [ -n "$RC" ] && [ "$RC" != /etc/resolv.conf ] && echo --ro-bind "$RC" "$RC" ) \
  --proc /proc --dev /dev --tmpfs /tmp \
  --bind /home/ouvrier/travaux /home/ouvrier/travaux \
  $( [ -d /mnt/c/Users/VOTRE_USER/Dev ]       && echo --bind /mnt/c/Users/VOTRE_USER/Dev /home/ouvrier/travaux/Dev ) \
  $( [ -d /mnt/c/Users/VOTRE_USER/Documents ] && echo --bind /mnt/c/Users/VOTRE_USER/Documents /home/ouvrier/travaux/Documents ) \
  --ro-bind /home/ouvrier/hooks /home/ouvrier/hooks \
  --bind /home/ouvrier/journal /home/ouvrier/journal \
  $( [ -d /home/ouvrier/.claude ] && echo --bind /home/ouvrier/.claude /home/ouvrier/.claude ) \
  $( [ -f /home/ouvrier/.claude.json ] && echo --bind /home/ouvrier/.claude.json /home/ouvrier/.claude.json ) \
  --setenv HOME /home/ouvrier \
  --unshare-pid --unshare-ipc --unshare-uts --die-with-parent \
  --chdir /home/ouvrier/travaux \
  "$@"
