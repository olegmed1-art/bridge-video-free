#!/usr/bin/env bash
set -Eeuo pipefail

# Build a known_hosts file containing only host keys whose SHA-256 fingerprint
# exactly matches the out-of-band value committed in the trusted workflow.

host="${1:-}"
expected_fingerprint="${2:-}"
output="${3:-}"

[[ "$host" =~ ^[A-Za-z0-9.:_-]+$ ]] || {
  echo "invalid Oracle SSH host" >&2
  exit 2
}
[[ "$expected_fingerprint" =~ ^SHA256:[A-Za-z0-9+/=]+$ ]] || {
  echo "invalid Oracle SSH fingerprint" >&2
  exit 2
}
[[ -n "$output" ]] || {
  echo "known_hosts output path is required" >&2
  exit 2
}

umask 077
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
scanned="$work/scanned"
candidate="$work/candidate"
: > "$scanned"

for attempt in 1 2 3; do
  ssh-keyscan -T 10 -t ed25519,ecdsa,rsa "$host" >> "$scanned" 2>/dev/null || true
  [[ -s "$scanned" ]] && break
  sleep $((attempt * 2))
done
[[ -s "$scanned" ]] || {
  echo "Oracle SSH host key scan returned no keys" >&2
  exit 3
}

: > "$output"
sort -u "$scanned" | while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  printf '%s\n' "$line" > "$candidate"
  fingerprint="$(ssh-keygen -lf "$candidate" 2>/dev/null | awk 'NR == 1 {print $2}')"
  if [[ "$fingerprint" == "$expected_fingerprint" ]]; then
    printf '%s\n' "$line" >> "$output"
  fi
done

sort -u -o "$output" "$output"
[[ -s "$output" ]] || {
  echo "Oracle SSH fingerprint mismatch" >&2
  exit 4
}
while IFS= read -r fingerprint; do
  [[ "$fingerprint" == "$expected_fingerprint" ]] || {
    echo "unverified Oracle SSH key reached known_hosts" >&2
    exit 5
  }
done < <(ssh-keygen -lf "$output" | awk '{print $2}')
chmod 0600 "$output"
