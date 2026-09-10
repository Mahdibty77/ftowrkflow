#!/bin/sh
# Code-table backup (large SQLite files under the code_db volume).
# Always writes ONE fixed file /backups/code_db/ftcode_db.tar.gz — each run
# replaces the previous snapshot. Scheduled every 3 days at ~00:00.
set -eu

DIR="/backups/code_db"
OUT="${DIR}/ftcode_db.tar.gz"
TMP="/backups/.ftcode_db_work_$$"
STAMP=$(date '+%Y-%m-%d_%H%M%S')
mkdir -p "$TMP" "$DIR" /backups/.control

# This snapshot is the only copy there is - the mv below replaces the previous
# one - so a half-read archive destroys the last good code tables. tar used to
# run with `|| :`, which turned a truncated read (a SQLite file being rewritten
# by an import, a full disk) into a silent OK. Abort instead: nothing is
# replaced, backup_service.sh records the error, and the 3-day marker at the
# bottom is not written, so the next pass retries rather than waiting 3 days.
tar -czf "$TMP/code_db.tar.gz" -C /data/code_db . || {
  echo "ERR code_db archive incomplete (tar exit $?)"
  rm -rf "$TMP"
  exit 1
}
[ -s "$TMP/code_db.tar.gz" ] || {
  echo "ERR code_db archive is empty"
  rm -rf "$TMP"
  exit 1
}
printf 'created=%s\nformat=code_db\nkind=code_db_snapshot\n' \
       "$STAMP" > "$TMP/MANIFEST.txt"

# Build a new archive next to the destination, then atomically replace.
NEW="${OUT}.new"
tar -czf "$NEW" -C "$TMP" code_db.tar.gz MANIFEST.txt
rm -rf "$TMP"
mv -f "$NEW" "$OUT"

# Remember when this snapshot was taken (used by the 3-day scheduler).
printf '%s\n' "$(date '+%Y-%m-%d')" > /backups/.control/code_db_last.txt

echo "OK code_db/ftcode_db.tar.gz"
