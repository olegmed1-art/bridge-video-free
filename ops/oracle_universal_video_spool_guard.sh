#!/usr/bin/env bash
set -Eeuo pipefail

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $# -eq 5 && "$1" == verify ]] \
  || fail 'usage: oracle_universal_video_spool_guard.sh verify BASE CHAIN_OWNER WORKER_OWNER WORKER_GROUP'

base="$2"
chain_owner="$3"
worker_owner="$4"
worker_group="$5"
[[ "$base" == /* && "$base" != / ]] || fail 'base must be a bounded absolute path'

chain_uid="$(id -u "$chain_owner")"
worker_uid="$(id -u "$worker_owner")"
worker_gid="$(getent group "$worker_group" | cut -d: -f3)"
[[ -n "$worker_gid" ]] || fail 'worker group is unavailable'

real_dir(){
  local path="$1"
  [[ -d "$path" && ! -L "$path" ]] || fail "unsafe or missing directory: $path"
}

exact_dir(){
  local path="$1" uid="$2" gid="$3" mode="$4" actual
  real_dir "$path"
  actual="$(stat -c '%u:%g:%a' "$path")"
  [[ "$actual" == "$uid:$gid:$mode" ]] || fail "unexpected directory ownership/mode: $path ($actual)"
}

parent="${base%/*}"
real_dir "$parent"
[[ "$(stat -c '%u' "$parent")" == "$chain_uid" ]] || fail 'base parent is not chain-owner controlled'
parent_mode="$(stat -c '%a' "$parent")"
(( (8#$parent_mode & 0022) == 0 )) || fail 'base parent is group/world writable'

exact_dir "$base" "$chain_uid" "$worker_gid" 750
exact_dir "$base/spool" "$chain_uid" "$worker_gid" 750
for leaf in inbox running done failed results progress; do
  exact_dir "$base/spool/$leaf" "$worker_uid" "$worker_gid" 750
done

echo UNIVERSAL_VIDEO_SPOOL_LAYOUT_PASS
