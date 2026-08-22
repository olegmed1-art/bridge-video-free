#!/usr/bin/env bash
set -Eeuo pipefail

REGION="${REGION:-eu-frankfurt-1}"
INSTANCE_NAME="${INSTANCE_NAME:-bridge-school-dds3-frankfurt}"
BRANCH="ops-oracle-dds3-bootstrap"
BASE="https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/${BRANCH}/ops"
export OCI_CLI_REGION="$REGION"

command -v oci >/dev/null 2>&1 || { echo 'ERROR: run this in Oracle Cloud Shell' >&2; exit 1; }
ROOT_JSON="$(oci iam compartment list --include-root --all --output json)"
TENANCY_ID="$(printf '%s' "$ROOT_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("data",[]); print(next((x.get("id","") for x in d if str(x.get("id","")).startswith("ocid1.tenancy.")), ""))')"
[[ -n "$TENANCY_ID" ]] || { echo 'ERROR: tenancy not found' >&2; exit 1; }
COMPARTMENT_ID="${COMPARTMENT_ID:-$TENANCY_ID}"

EXISTING="$(oci compute instance list -c "$COMPARTMENT_ID" --display-name "$INSTANCE_NAME" --all --output json | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); xs=[x for x in xs if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]; print("yes" if xs else "")')"

if [[ "$EXISTING" == "yes" ]]; then
  echo 'Existing DDS3 VM found: running autonomous repair/validation path.'
  curl -fsSL "$BASE/oracle_dds3_repair.sh" -o "$HOME/oracle_dds3_repair.sh"
  chmod 700 "$HOME/oracle_dds3_repair.sh"
  exec bash "$HOME/oracle_dds3_repair.sh"
fi

echo 'No existing DDS3 VM found: running fixed first-time bootstrap.'
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
curl -fsSL "$BASE/oracle_dds3_bootstrap.sh" -o "$TMP"
# Fix: runtime Dockerfile intentionally starts FROM the local bridge-school-dds3 image;
# --pull makes Docker ignore that local stage and try Docker Hub, causing pull denied.
sed -i 's#docker build --pull -f dds3_runtime/Dockerfile -t bridge-school-dds3-runtime \.#docker build -f dds3_runtime/Dockerfile -t bridge-school-dds3-runtime .#' "$TMP"
exec bash "$TMP"
