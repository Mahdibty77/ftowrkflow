#!/bin/sh
# Media backup: avatars, stamps, signatures and other uploaded files under
# /data/media. Scheduled every 3 days; archives older than BACKUP_MEDIA_KEEP_DAYS
# (default 10) are deleted. Writes to /backups/media/.
set -eu

STAMP=$(date '+%Y-%m-%d_%H%M%S')
NAME="ftmedia_${STAMP}.tar.gz"
DIR="/backups/media"
OUT="${DIR}/${NAME}"
TMP="/backups/.work_media_${STAMP}"
mkdir -p "$TMP" "$DIR" /backups/.control

# A backup that only half-read /data/media is worse than no backup: it still
# gets wrapped, stamped and announced as OK, and the day it is restored
# do_restore.sh empties the live volume before unpacking it. tar used to run
# with `|| :`, which hid exactly that, so let a failed or empty archive abort
# the run instead - backup_service.sh then records it as an error and the
# 3-day marker below is left untouched, so the next pass retries.
tar -czf "$TMP/media.tar.gz" -C /data/media . || {
  echo "ERR media archive incomplete (tar exit $?)"
  rm -rf "$TMP"
  exit 1
}
[ -s "$TMP/media.tar.gz" ] || {
  echo "ERR media archive is empty"
  rm -rf "$TMP"
  exit 1
}
printf 'created=%s\nformat=media\nkind=media_snapshot\n' \
       "$STAMP" > "$TMP/MANIFEST.txt"

tar -czf "$OUT" -C "$TMP" media.tar.gz MANIFEST.txt
rm -rf "$TMP"

# Retention by age: keep archives for BACKUP_MEDIA_KEEP_DAYS days
# (day 11 removes the day-1 file when keep=10).
KEEP_DAYS="${BACKUP_MEDIA_KEEP_DAYS:-10}"
# BusyBox find: -mtime +N means strictly older than N*24h.
find "$DIR" -maxdepth 1 \( -name 'ftmedia_*.tar.gz' -o -name 'ftbackup_*.tar.gz' \) \
  -type f -mtime +"$KEEP_DAYS" -exec rm -f {} + 2>/dev/null || :

printf '%s\n' "$(date '+%Y-%m-%d')" > /backups/.control/media_last.txt

echo "OK media/${NAME}"
