#!/usr/bin/env bash
# Prospective command for one separately admitted standard Ubuntu runner.
# Not run here. The local directory intentionally has no enabled probe.py.
set -euo pipefail
umask 077
ulimit -c 0
entry="$PWD/entry.py"
test -f "$entry"
set +e
sudo -n /usr/bin/timeout --signal=KILL 17s \
  /usr/bin/unshare --pid --fork --kill-child=KILL --mount-proc -- \
  /usr/bin/setpriv --reuid="$(id -u)" --regid="$(id -g)" --clear-groups \
  --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs --pdeathsig=KILL \
  /usr/bin/env -i LC_ALL=C.UTF-8 /usr/bin/python3 -I -S -B \
  "$entry" </dev/null 2>/dev/null
result=$?
set -e
printf '{"schema":"stdlib-tls-launch-v1","returncode":%d}\n' "$result"
exit "$result"

