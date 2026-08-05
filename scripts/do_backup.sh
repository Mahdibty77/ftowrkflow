#!/bin/sh
# Daily app database backup: PostgreSQL only (cases, people, seats, histories,
# and other textual app data). Media and code SQLite are backed up separately.
# Writes to /backups/db/ and keeps the newest BACKUP_DB_KEEP archives (default 33).
# Runs inside the postgres:16 image so pg_dump matches the server.
set -eu

STAMP=$(date '+%Y-%m-%d_%H%M%S')
NAME="ftdb_${STAMP}.tar.gz"
DIR="/backups/db"
OUT="${DIR}/${NAME}"
TMP="/backups/.work_db_${STAMP}"
mkdir -p "$TMP" "$DIR" /backups/.control

# 1) Database -> plain SQL with DROP ... IF EXISTS so a restore replaces cleanly.
pg_dump --clean --if-exists --no-owner --no-privileges -f "$TMP/database.sql"

# 2) Manifest.
printf 'created=%s\ndb=%s\nformat=plain-sql\nkind=daily_db\n' \
       "$STAMP" "${PGDATABASE:-}" > "$TMP/MANIFEST.txt"

# 3) Bundle (database only).
tar -czf "$OUT" -C "$TMP" database.sql MANIFEST.txt
rm -rf "$TMP"

# 4) Rotation: keep newest BACKUP_DB_KEEP daily DB archives.
KEEP="${BACKUP_DB_KEEP:-${BACKUP_KEEP:-33}}"
n=0
for f in $(ls -1t "$DIR"/ftdb_*.tar.gz "$DIR"/ftbackup_*.tar.gz 2>/dev/null); do
  n=$((n + 1))
  [ "$n" -gt "$KEEP" ] && rm -f "$f"
done

echo "OK db/${NAME}"
