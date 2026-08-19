#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set}"

PSQL=(psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -A -t)
MIGRATIONS_DIR="${MIGRATIONS_DIR:-database/migrations}"
EXTERNAL_HISTORY_FILE="${EXTERNAL_HISTORY_FILE:-database/external_migration_history.tsv}"

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
  local key checksum provenance
  while IFS=$'\t' read -r key checksum provenance; do
    [[ -z "$key" || "$key" == \#* ]] && continue
    if [[ ! "$key" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
      echo "Unsafe external migration key in $EXTERNAL_HISTORY_FILE: $key" >&2
      exit 1
    fi
    if [[ ! "$checksum" =~ ^[0-9a-f]{64}$ ]]; then
      echo "Invalid external migration SHA-256 for $key" >&2
      exit 1
    fi
    if [[ -n "${seen[$key]:-}" ]]; then
      echo "Duplicate external migration history key: $key" >&2
      exit 1
    fi
    seen[$key]="$checksum"
  done < "$EXTERNAL_HISTORY_FILE"
}

external_checksum_for() {
  local requested_key="$1"
  [[ -f "$EXTERNAL_HISTORY_FILE" ]] || return 0
  awk -F '\t' -v wanted="$requested_key" '
    $0 !~ /^#/ && $1 == wanted { print $2; exit }
  ' "$EXTERNAL_HISTORY_FILE"
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
  [[ "$("${PSQL[@]}" -c "SELECT to_regclass('public.schema_migration') IS NOT NULL;")" == "t" ]]
}

# Once migration history exists, every recorded migration must either still exist on
# disk or be an explicitly audited historical exception with an exact immutable
# checksum. External-history entries are never executed; a numbered forward migration
# must restore fresh-database schema parity.
if schema_table_exists; then
  while IFS=$'\t' read -r applied_key stored_checksum; do
    [[ -z "$applied_key" ]] && continue

    if [[ "$applied_key" =~ ^[A-Za-z0-9_]+$ && -f "$MIGRATIONS_DIR/${applied_key}.sql" ]]; then
      continue
    fi

    external_checksum="$(external_checksum_for "$applied_key")"
    if [[ -n "$external_checksum" ]]; then
      if [[ -z "$stored_checksum" ]]; then
        echo "External migration history lacks a stored database checksum: $applied_key" >&2
        exit 1
      fi
      if [[ "$stored_checksum" != "$external_checksum" ]]; then
        echo "External migration checksum mismatch: $applied_key" >&2
        exit 1
      fi
      echo "Verified audited external migration history: $applied_key"
      continue
    fi

    if [[ ! "$applied_key" =~ ^[A-Za-z0-9_]+$ ]]; then
      echo "Invalid migration key stored in database: $applied_key" >&2
      exit 1
    fi
    echo "Applied migration is missing from repository: ${applied_key}.sql" >&2
    exit 1
  done < <("${PSQL[@]}" -F $'\t' -c "SELECT migration_key, COALESCE(checksum,'') FROM schema_migration ORDER BY migration_key;")
fi

for migration in "$MIGRATIONS_DIR"/*.sql; do
  [[ -e "$migration" ]] || { echo "No migration files found" >&2; exit 1; }
  key="$(basename "$migration" .sql)"
  checksum="$(sha256sum "$migration" | awk '{print $1}')"

  [[ "$key" =~ ^[A-Za-z0-9_]+$ ]] || { echo "Unsafe migration key: $key" >&2; exit 1; }
  [[ "$checksum" =~ ^[0-9a-f]{64}$ ]] || { echo "Invalid SHA-256 for $key" >&2; exit 1; }

  if schema_table_exists; then
    applied="$("${PSQL[@]}" -c "SELECT EXISTS (SELECT 1 FROM schema_migration WHERE migration_key = '$key');")"
  else
    applied="f"
  fi

  if [[ "$applied" == "t" ]]; then
    stored="$("${PSQL[@]}" -c "SELECT COALESCE(checksum,'') FROM schema_migration WHERE migration_key = '$key';")"
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
    -v migration_file="$migration" <<'PSQL'
SELECT pg_advisory_lock(hashtext(current_database()), hashtext('bridge_school_schema_migrations'));
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

  registered="$("${PSQL[@]}" -c "SELECT EXISTS (SELECT 1 FROM schema_migration WHERE migration_key = '$key');")"
  if [[ "$registered" != "t" ]]; then
    echo "Migration $key committed without registering itself in schema_migration" >&2
    exit 1
  fi

  "${PSQL[@]}" -c "UPDATE schema_migration SET checksum = '$checksum' WHERE migration_key = '$key' AND checksum IS NULL;" >/dev/null
  stored="$("${PSQL[@]}" -c "SELECT checksum FROM schema_migration WHERE migration_key = '$key';")"
  if [[ "$stored" != "$checksum" ]]; then
    echo "Failed to persist checksum for migration $key" >&2
    exit 1
  fi
done

echo "Migration history verified."
