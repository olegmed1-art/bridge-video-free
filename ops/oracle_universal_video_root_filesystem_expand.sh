#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HOSTNAME="${EXPECTED_HOSTNAME:?EXPECTED_HOSTNAME is required}"
EXPECTED_MIN_DISK_BYTES="${EXPECTED_MIN_DISK_BYTES:-90000000000}"
EXPECTED_MAX_DISK_BYTES="${EXPECTED_MAX_DISK_BYTES:-105000000000}"
MIN_FREE_KB="${MIN_FREE_KB:-5242880}"
CHECKPOINT_ONLY="${CHECKPOINT_ONLY:-0}"
LOCK_FILE=/opt/bridge-school/universal-video/spool/.workload.lock

exec 9>"$LOCK_FILE"
flock --exclusive --nonblock 9 || { echo 'UV_ROOT_EXPAND_BUSY'; exit 75; }

actual_hostname="$(hostname -s)"
[[ "$actual_hostname" == "$EXPECTED_HOSTNAME" ]] || {
  echo "UV_ROOT_EXPAND_IDENTITY_MISMATCH expected=$EXPECTED_HOSTNAME actual=$actual_hostname"
  exit 64
}

root_source="$(findmnt -n -o SOURCE --target /)"
root_fstype="$(findmnt -n -o FSTYPE --target /)"
root_source="$(readlink -f "$root_source")"
[[ -b "$root_source" ]] || { echo 'UV_ROOT_EXPAND_ROOT_NOT_BLOCK'; exit 65; }
[[ "$root_fstype" == ext4 || "$root_fstype" == xfs ]] || {
  echo "UV_ROOT_EXPAND_UNSUPPORTED_FS type=$root_fstype"
  exit 66
}

root_type="$(lsblk -dnro TYPE "$root_source")"
part_number="$(lsblk -dnro PARTN "$root_source")"
parent_name="$(lsblk -dnro PKNAME "$root_source")"
[[ "$root_type" == part && "$part_number" =~ ^[1-9][0-9]*$ && -n "$parent_name" ]] || {
  echo 'UV_ROOT_EXPAND_AMBIGUOUS_PARTITION'
  exit 67
}
disk="/dev/$parent_name"
[[ -b "$disk" && "$(lsblk -dnro TYPE "$disk")" == disk ]] || {
  echo 'UV_ROOT_EXPAND_PARENT_NOT_DISK'
  exit 68
}

disk_bytes="$(lsblk -bdnro SIZE "$disk")"
partition_bytes_before="$(lsblk -bdnro SIZE "$root_source")"
filesystem_bytes_before="$(findmnt -bn -o SIZE --target /)"
free_kb_before="$(df -Pk / | awk 'NR==2 {print $4}')"
[[ "$disk_bytes" =~ ^[0-9]+$ && "$disk_bytes" -ge "$EXPECTED_MIN_DISK_BYTES" && "$disk_bytes" -le "$EXPECTED_MAX_DISK_BYTES" ]] || {
  echo "UV_ROOT_EXPAND_DISK_SIZE_REJECTED bytes=$disk_bytes"
  exit 69
}

echo "UV_ROOT_EXPAND_BEFORE host=$actual_hostname root=$root_source disk=$disk part=$part_number fs=$root_fstype disk_bytes=$disk_bytes partition_bytes=$partition_bytes_before filesystem_bytes=$filesystem_bytes_before free_kb=$free_kb_before"
lsblk -f "$disk"
findmnt /
df -h /

command -v growpart >/dev/null || { echo 'UV_ROOT_EXPAND_GROWPART_MISSING'; exit 70; }
case "$root_fstype" in
  ext4) command -v resize2fs >/dev/null || { echo 'UV_ROOT_EXPAND_RESIZE2FS_MISSING'; exit 71; } ;;
  xfs) command -v xfs_growfs >/dev/null || { echo 'UV_ROOT_EXPAND_XFS_GROWFS_MISSING'; exit 72; } ;;
esac

if [[ "$CHECKPOINT_ONLY" == 1 ]]; then
  command -v sfdisk >/dev/null || { echo 'UV_ROOT_EXPAND_SFDISK_MISSING'; exit 76; }
  checkpoint_dir=/var/lib/bridge-school/root-partition-recovery
  checkpoint="$checkpoint_dir/issue-881-before-expand.sfdisk"
  install -d -o root -g root -m 0700 "$checkpoint_dir"
  sfdisk --verify "$disk"
  sfdisk --dump "$disk" > "$checkpoint"
  chmod 0600 "$checkpoint"
  checkpoint_sha256="$(sha256sum "$checkpoint" | awk '{print $1}')"
  echo "UV_ROOT_CHECKPOINT_PASS disk_bytes=$disk_bytes checkpoint=$checkpoint sha256=$checkpoint_sha256"
  exit 0
fi

minimum_expected_bytes=$((disk_bytes * 90 / 100))
if [[ "$partition_bytes_before" -lt "$minimum_expected_bytes" ]]; then
  growpart_output=''
  if ! growpart_output="$(growpart "$disk" "$part_number" 2>&1)"; then
    partition_bytes_now="$(lsblk -bdnro SIZE "$root_source")"
    [[ "$partition_bytes_now" -ge "$minimum_expected_bytes" ]] || {
      printf '%s\n' "$growpart_output" >&2
      echo 'UV_ROOT_EXPAND_GROWPART_FAILED' >&2
      exit 77
    }
  fi
fi

case "$root_fstype" in
  ext4)
    resize2fs "$root_source"
    ;;
  xfs)
    xfs_growfs /
    ;;
esac

partition_bytes_after="$(lsblk -bdnro SIZE "$root_source")"
filesystem_bytes_after="$(findmnt -bn -o SIZE --target /)"
free_kb_after="$(df -Pk / | awk 'NR==2 {print $4}')"
[[ "$partition_bytes_after" -ge "$minimum_expected_bytes" ]] || { echo 'UV_ROOT_EXPAND_PARTITION_TOO_SMALL'; exit 73; }
[[ "$filesystem_bytes_after" -ge "$minimum_expected_bytes" ]] || { echo 'UV_ROOT_EXPAND_FILESYSTEM_TOO_SMALL'; exit 74; }
[[ "$free_kb_after" -gt "$MIN_FREE_KB" ]] || { echo 'UV_ROOT_EXPAND_FREE_SPACE_INSUFFICIENT'; exit 75; }

lsblk -f "$disk"
findmnt /
df -h /
echo "UV_ROOT_EXPAND_PASS host=$actual_hostname root=$root_source disk=$disk part=$part_number fs=$root_fstype disk_bytes=$disk_bytes partition_bytes_before=$partition_bytes_before partition_bytes_after=$partition_bytes_after filesystem_bytes_before=$filesystem_bytes_before filesystem_bytes_after=$filesystem_bytes_after free_kb_before=$free_kb_before free_kb_after=$free_kb_after"
