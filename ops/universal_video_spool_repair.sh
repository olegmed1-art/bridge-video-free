#!/usr/bin/env bash
set -Eeuo pipefail

[[ $(id -u) -eq 0 ]] || { echo 'ERROR: must run as root' >&2; exit 1; }
id universal-video >/dev/null 2>&1 || { echo 'ERROR: universal-video user missing' >&2; exit 1; }

readonly ROOT=/opt/bridge-school/universal-video/spool
for d in inbox running done failed results; do
  /usr/bin/install -d -o universal-video -g universal-video -m 0750 "$ROOT/$d"
  /usr/bin/chown universal-video:universal-video "$ROOT/$d"
  /usr/bin/chmod 0750 "$ROOT/$d"
done

/usr/sbin/runuser -u universal-video -- /bin/sh -ceu '
  root=/opt/bridge-school/universal-video/spool
  for d in inbox running done failed results; do
    p="$root/$d/.operator-write-check-$$"
    : > "$p"
    rm -f "$p"
  done
'

echo UNIVERSAL_VIDEO_SPOOL_RUNTIME_REPAIR_PASS
