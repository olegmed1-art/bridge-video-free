#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set}"

PSQL=(psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -A -t)
# Native Windows psql writes CRLF even when Git Bash reads LF scripts.
# Only line framing is normalized. Values fetched below are base64-encoded in
# SQL so a data-level CR cannot be mistaken for the line-ending CR.
psql_value() { "${PSQL[@]}" "$@" | sed $'s/\r$//'; }
decode_value() {
  local value
  value="$(printf '%s' "$1" | base64 --decode)" || return 1
  if [[ "$(printf '%s' "$value" | base64 --wrap=0)" != "$1" ]]; then
    echo 'Migration history contains a value that cannot be read without loss' >&2
    return 1
  fi
  printf '%s' "$value"
}
MIGRATIONS_DIR="${MIGRATIONS_DIR:-database/migrations}"
EXTERNAL_HISTORY_FILE="${EXTERNAL_HISTORY_FILE:-database/external_migration_history.tsv}"
MIGRATION_CHECKS_DIR="${MIGRATION_CHECKS_DIR:-$(dirname "$MIGRATIONS_DIR")/migration_checks}"

verify_migration_postcondition() {
  local key="$1" checksum="$2"
  local check="$MIGRATION_CHECKS_DIR/$key.sql"
  if [[ ! -f "$check" ]]; then
    if [[ "$key" == "0500_books_authority_core" ]]; then
      echo "Required Books authority postcondition missing" >&2
      return 1
    fi
    return 0
  fi
  "${PSQL[@]}" -v migration_checksum="$checksum" \
    -v books_expected_database="${BOOKS_AUTHORITY_EXPECTED_DATABASE:-__UNCONFIRMED__}" \
    -v books_expected_owner="${BOOKS_AUTHORITY_EXPECTED_OWNER:-__UNCONFIRMED__}" -f "$check"
}


if [[ ! -d "$MIGRATIONS_DIR" ]]; then
  echo "Migration directory not found: $MIGRATIONS_DIR" >&2
  exit 1
fi

assert_unique_numeric_prefixes() {
  local directory="$1"
  local label="$2"
  local -A seen=()
  local file prefix

  [[ -d "$directory" ]] || return 0

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    prefix="${file%%_*}"
    if [[ -n "${seen[$prefix]:-}" ]]; then
      echo "Duplicate ${label} sequence prefix ${prefix}: ${seen[$prefix]} and ${file}" >&2
      echo "Sequence numbers are unique release identities; renumber the newer unpromoted file before continuing." >&2
      exit 1
    fi
    seen[$prefix]="$file"
  done < <(
    find "$directory" -maxdepth 1 -type f -name '[0-9][0-9][0-9][0-9]_*.sql' -printf '%f\n' | sort
  )
}

validate_external_history() {
  [[ -f "$EXTERNAL_HISTORY_FILE" ]] || return 0
  local -A seen=()
  local key checksum version provenance identity
  local legacy_0060=c2960f3b78a34985d1afd78930f896f38eebeae2cbd90f83c44a011fe7d779a7
  local candidate_0060=cfd5bf6088761dcc1d38a4d0febb6fb24f3d4f396fd67ca08568ff44994c44bc
  local count_0060=0
  while IFS=$'\t' read -r key checksum version provenance; do
    [[ -z "$key" || "$key" == \#* ]] && continue
    if [[ ! "$key" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
      echo "Unsafe external migration key in $EXTERNAL_HISTORY_FILE: $key" >&2
      exit 1
    fi
    if [[ ! "$checksum" =~ ^[0-9a-f]{64}$ ]]; then
      echo "Invalid external migration SHA-256 for $key" >&2
      exit 1
    fi
    if [[ ! "$version" =~ ^[a-z][a-z0-9-]*$ || -z "$provenance" ]]; then
      echo "Invalid external migration version/provenance for $key" >&2
      exit 1
    fi
    identity="$key"
    if [[ "$key" == 0060_fixed_subset_admission ]]; then
      case "$version:$checksum" in
        "legacy-744d068a:$legacy_0060"|"new-scoped-claim:$candidate_0060") ;;
        *) echo "Unsupported 0060 external migration version: $version" >&2; exit 1 ;;
      esac
      identity="$key:$version"
      count_0060=$((count_0060+1))
    fi
    if [[ -n "${seen[$identity]:-}" ]]; then
      echo "Duplicate external migration history key/version: $identity" >&2
      exit 1
    fi
    seen[$identity]="$checksum"
  done < "$EXTERNAL_HISTORY_FILE"
  if [[ "$count_0060" -ne 0 && "$count_0060" -ne 2 ]]; then
    echo 'Incomplete 0060 external migration version matrix' >&2
    exit 1
  fi
}

external_versions_for() {
  local requested_key="$1"
  [[ -f "$EXTERNAL_HISTORY_FILE" ]] || return 0
  awk -F '\t' -v wanted="$requested_key" '
    $0 !~ /^#/ && $1 == wanted { print $3 }
  ' "$EXTERNAL_HISTORY_FILE"
}

external_version_for() {
  local requested_key="$1" requested_checksum="$2"
  [[ -f "$EXTERNAL_HISTORY_FILE" ]] || return 0
  awk -F '\t' -v wanted="$requested_key" -v checksum="$requested_checksum" '
    $0 !~ /^#/ && $1 == wanted && $2 == checksum { print $3; exit }
  ' "$EXTERNAL_HISTORY_FILE"
}

migration_checksum() {
  local migration="$1"
  local key parts_dir unexpected part_count
  key="$(basename "$migration" .sql)"
  parts_dir="$MIGRATIONS_DIR/$key"

  if [[ ! -d "$parts_dir" ]]; then
    sha256sum "$migration" | awk '{print $1}'
    return
  fi

  unexpected="$(find "$parts_dir" -maxdepth 1 -type f ! -name '*.sql' -print -quit)"
  if [[ -n "$unexpected" ]]; then
    echo "Composite migration contains an unsupported component: $unexpected" >&2
    exit 1
  fi
  part_count="$(find "$parts_dir" -maxdepth 1 -type f -name '*.sql' -printf '.' | wc -c)"
  if [[ "$part_count" -eq 0 ]]; then
    echo "Composite migration has no SQL components: $parts_dir" >&2
    exit 1
  fi

  {
    printf 'FILE:%s\n' "$(basename "$migration")"
    cat "$migration"
    while IFS= read -r part; do
      printf '\nFILE:%s\n' "${part#"$MIGRATIONS_DIR/"}"
      cat "$part"
    done < <(find "$parts_dir" -maxdepth 1 -type f -name '*.sql' -print | LC_ALL=C sort)
  } | sha256sum | awk '{print $1}'
}

# Catch sequence collisions before connecting migration ordering to durable state. This
# also checks SQL regression-test numbering when the tests directory is present in the
# checkout, because duplicate test identities make evidence and failure attribution
# ambiguous even though PostgreSQL itself could execute both files.
assert_unique_numeric_prefixes "$MIGRATIONS_DIR" "migration"
TESTS_DIR="$(dirname "$MIGRATIONS_DIR")/tests"
assert_unique_numeric_prefixes "$TESTS_DIR" "database-test"
validate_external_history

schema_table_exists() {
  [[ "$(psql_value -c "SELECT to_regclass('public.schema_migration') IS NOT NULL;")" == "t" ]]
}

# Once migration history exists, every recorded migration must either still exist on
# disk or be an explicitly audited historical exception with an exact immutable
# checksum. External-history entries are never executed; a numbered forward migration
# must restore fresh-database schema parity.
if schema_table_exists; then
  while IFS=$'\t' read -r applied_key_b64 stored_checksum_b64; do
    applied_key="$(decode_value "$applied_key_b64")"
    stored_checksum="$(decode_value "$stored_checksum_b64")"
    [[ -z "$applied_key" ]] && continue

    if [[ "$applied_key" =~ ^[A-Za-z0-9_]+$ && -f "$MIGRATIONS_DIR/${applied_key}.sql" ]]; then
      continue
    fi

    external_versions="$(external_versions_for "$applied_key")"
    if [[ -n "$external_versions" ]]; then
      if [[ -z "$stored_checksum" ]]; then
        echo "External migration history lacks a stored database checksum: $applied_key" >&2
        exit 1
      fi
      external_version="$(external_version_for "$applied_key" "$stored_checksum")"
      if [[ -z "$external_version" ]]; then
        echo "External migration checksum mismatch: $applied_key" >&2
        exit 1
      fi
      echo "Verified audited external migration history: $applied_key [$external_version]"
      continue
    fi

    if [[ ! "$applied_key" =~ ^[A-Za-z0-9_]+$ ]]; then
      echo "Invalid migration key stored in database: $applied_key" >&2
      exit 1
    fi
    echo "Applied migration is missing from repository: ${applied_key}.sql" >&2
    exit 1
  done < <(psql_value -F $'\t' -c "SELECT replace(encode(convert_to(migration_key,'UTF8'),'base64'), E'\\n',''), replace(encode(convert_to(COALESCE(checksum,''),'UTF8'),'base64'), E'\\n','') FROM schema_migration ORDER BY migration_key;")
fi

for migration in "$MIGRATIONS_DIR"/*.sql; do
  [[ -e "$migration" ]] || { echo "No migration files found" >&2; exit 1; }
  key="$(basename "$migration" .sql)"
  checksum="$(migration_checksum "$migration")"

  [[ "$key" =~ ^[A-Za-z0-9_]+$ ]] || { echo "Unsafe migration key: $key" >&2; exit 1; }
  [[ "$checksum" =~ ^[0-9a-f]{64}$ ]] || { echo "Invalid SHA-256 for $key" >&2; exit 1; }

  if schema_table_exists; then
    applied="$(psql_value -c "SELECT EXISTS (SELECT 1 FROM schema_migration WHERE migration_key = '$key');")"
  else
    applied="f"
  fi

  if [[ "$applied" == "t" ]]; then
    # Opt-in metadata postconditions run before legacy NULL-checksum bootstrapping.
    verify_migration_postcondition "$key" "$checksum"
    stored="$(decode_value "$(psql_value -c "SELECT replace(encode(convert_to(COALESCE(checksum,''),'UTF8'),'base64'), E'\\n','') FROM schema_migration WHERE migration_key = '$key';")")"
    if [[ -z "$stored" ]]; then
      echo "Bootstrapping checksum for already-applied migration $key"
      "${PSQL[@]}" -c "UPDATE schema_migration SET checksum = '$checksum' WHERE migration_key = '$key' AND checksum IS NULL;" >/dev/null
    elif [[ "$stored" != "$checksum" ]]; then
      echo "Historical migration checksum mismatch: $key" >&2
      echo "Historical migrations are immutable; create a new migration instead of editing an applied one." >&2
      exit 1
    else
      echo "Skipping already-applied migration $key"
    fi
    continue
  fi

  echo "Applying migration $key"
  # Serialize migration application at the database level. The session-level advisory
  # lock survives BEGIN/COMMIT statements inside migration files and is released
  # automatically if psql exits on an error. Re-checking the registry after acquiring
  # the lock prevents two independent CI runners from applying the same migration.
  psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 \
    -v migration_key="$key" \
    -v migration_checksum="$checksum" \
    -v books_expected_database="${BOOKS_AUTHORITY_EXPECTED_DATABASE:-__UNCONFIRMED__}" \
    -v books_expected_owner="${BOOKS_AUTHORITY_EXPECTED_OWNER:-__UNCONFIRMED__}" \
    -v migration_file="$migration" <<'PSQL'
SET statement_timeout='15s';
SELECT pg_advisory_lock(hashtext(current_database()), hashtext('bridge_school_schema_migrations'));
RESET statement_timeout;
SELECT to_regclass('public.schema_migration') IS NOT NULL AS registry_exists \gset
\if :registry_exists
  SELECT EXISTS (
      SELECT 1 FROM public.schema_migration WHERE migration_key = :'migration_key'
  ) AS migration_already_applied \gset
\else
  \set migration_already_applied false
\endif
\if :migration_already_applied
  \echo 'Migration' :migration_key 'was applied by another runner while waiting for the database lock; skipping.'
\else
  \i :migration_file
\endif
SELECT pg_advisory_unlock(hashtext(current_database()), hashtext('bridge_school_schema_migrations'));
PSQL

  if ! schema_table_exists; then
    echo "Migration $key did not create schema_migration" >&2
    exit 1
  fi

  registered="$(psql_value -c "SELECT EXISTS (SELECT 1 FROM schema_migration WHERE migration_key = '$key');")"
  if [[ "$registered" != "t" ]]; then
    echo "Migration $key committed without registering itself in schema_migration" >&2
    exit 1
  fi

  "${PSQL[@]}" -c "UPDATE schema_migration SET checksum = '$checksum' WHERE migration_key = '$key' AND checksum IS NULL;" >/dev/null
  stored="$(decode_value "$(psql_value -c "SELECT replace(encode(convert_to(checksum,'UTF8'),'base64'), E'\\n','') FROM schema_migration WHERE migration_key = '$key';")")"
  if [[ "$stored" != "$checksum" ]]; then
    echo "Failed to persist checksum for migration $key" >&2
    exit 1
  fi
  verify_migration_postcondition "$key" "$checksum"
done

echo "Migration history verified."
