#!/bin/sh
# Backup scheduler + control-queue worker.
#   * Every night ~00:00 (Asia/Tehran): PostgreSQL text DB -> /backups/db/ (keep 33).
#   * Every 3 nights ~00:00: media uploads -> /backups/media/ (keep 10 days).
#   * Every 3 nights ~00:00: code SQLite -> /backups/code_db/ftcode_db.tar.gz (replace).
#   * Watches /backups/.control/request.json for admin backup / restore actions.
set -u

CTRL="/backups/.control"
mkdir -p "$CTRL" /backups/db /backups/media /backups/code_db

status() {
  st="$1"; act="${2:-}"; fl="${3:-}"; ms="$4"
  ms=$(printf '%s' "$ms" | tr -d '"\\' | tr '\n\r' '  ')
  printf '{"state":"%s","action":"%s","file":"%s","message":"%s","time":"%s"}' \
    "$st" "$act" "$fl" "$ms" "$(date '+%Y-%m-%d %H:%M:%S')" > "$CTRL/status.json.tmp"
  mv "$CTRL/status.json.tmp" "$CTRL/status.json"
}

run_db_backup() {  # $1 = label
  status running backup "" "$1 (database)..."
  if out=$(sh /scripts/do_backup.sh 2>&1); then
    f=$(printf '%s' "$out" | sed -n 's/^OK \(.*\)$/\1/p' | tail -n1)
    status success backup "$f" "$1 completed"
  else
    status error backup "" "$1 failed: $(printf '%s' "$out" | tail -n1)"
    return 1
  fi
}

run_media_backup() {  # $1 = label
  status running backup "" "$1 (media)..."
  if out=$(sh /scripts/do_backup_media.sh 2>&1); then
    f=$(printf '%s' "$out" | sed -n 's/^OK \(.*\)$/\1/p' | tail -n1)
    status success backup "$f" "$1 completed"
  else
    status error backup "" "$1 failed: $(printf '%s' "$out" | tail -n1)"
    return 1
  fi
}

run_code_db_backup() {  # $1 = label
  status running backup "" "$1 (code tables)..."
  if out=$(sh /scripts/do_backup_code_db.sh 2>&1); then
    f=$(printf '%s' "$out" | sed -n 's/^OK \(.*\)$/\1/p' | tail -n1)
    status success backup "$f" "$1 completed"
  else
    status error backup "" "$1 failed: $(printf '%s' "$out" | tail -n1)"
    return 1
  fi
}

# True when last run was 3+ calendar days ago (or marker/file missing).
_due_every_3_days() {  # $1 = last.txt path  $2 = expected archive path
  last_file="$1"
  archive="$2"
  if [ ! -f "$archive" ]; then
    return 0
  fi
  if [ ! -f "$last_file" ]; then
    return 0
  fi
  last=$(tr -d ' \n\r' < "$last_file")
  [ -n "$last" ] || return 0
  last_s=$(date -d "$last" +%s 2>/dev/null) || return 0
  now_s=$(date +%s)
  diff=$(( (now_s - last_s) / 86400 ))
  [ "$diff" -ge 3 ]
}

code_db_due() {
  if [ -f /backups/code_db/ftcode_db.tar.gz ]; then
    _due_every_3_days "$CTRL/code_db_last.txt" /backups/code_db/ftcode_db.tar.gz
  else
    _due_every_3_days "$CTRL/code_db_last.txt" /backups/ftcode_db.tar.gz
  fi
}

media_due() {
  # Due when no media archive exists yet, or last media backup was 3+ days ago.
  if ! ls /backups/media/ftmedia_*.tar.gz >/dev/null 2>&1; then
    return 0
  fi
  last_file="$CTRL/media_last.txt"
  if [ ! -f "$last_file" ]; then
    return 0
  fi
  last=$(tr -d ' \n\r' < "$last_file")
  [ -n "$last" ] || return 0
  last_s=$(date -d "$last" +%s 2>/dev/null) || return 0
  now_s=$(date +%s)
  diff=$(( (now_s - last_s) / 86400 ))
  [ "$diff" -ge 3 ]
}

echo "[backup-service] started; TZ=${TZ:-UTC}; db keep=${BACKUP_DB_KEEP:-${BACKUP_KEEP:-33}}; media keep days=${BACKUP_MEDIA_KEEP_DAYS:-10}; media+code_db every 3 days"

# One-time: move legacy root-level archives into the new subfolders (no overwrite).
if [ -f /backups/ftcode_db.tar.gz ] && [ ! -f /backups/code_db/ftcode_db.tar.gz ]; then
  mv /backups/ftcode_db.tar.gz /backups/code_db/ftcode_db.tar.gz 2>/dev/null || true
fi
for f in /backups/ftbackup_*.tar.gz; do
  [ -f "$f" ] || continue
  base=$(basename "$f")
  # Legacy combined archives stay listable under db/ for restore (still contain media).
  if [ ! -f "/backups/db/$base" ]; then
    mv "$f" "/backups/db/$base" 2>/dev/null || true
  fi
done

# First start: ensure at least one of each kind exists.
if ! ls /backups/db/ftdb_*.tar.gz >/dev/null 2>&1 \
   && ! ls /backups/db/ftbackup_*.tar.gz >/dev/null 2>&1; then
  run_db_backup "Initial database backup" || true
fi
if ! ls /backups/media/ftmedia_*.tar.gz >/dev/null 2>&1; then
  run_media_backup "Initial media backup" || true
fi
if [ ! -f /backups/code_db/ftcode_db.tar.gz ]; then
  run_code_db_backup "Initial code_db backup" || true
fi

LAST_DAY=$(date '+%Y-%m-%d')
while true; do
  # 1) Admin-requested action (queued by the web app).
  if [ -f "$CTRL/request.json" ]; then
    mv "$CTRL/request.json" "$CTRL/processing.json" 2>/dev/null || true
    ACTION=$(sed -n 's/.*"action"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CTRL/processing.json" | head -n1)
    RFILE=$(sed -n 's/.*"file"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CTRL/processing.json" | head -n1)
    if [ "$ACTION" = "backup" ]; then
      # Manual "Run backup now": database + media (not the multi-GB code tables).
      run_db_backup "Manual database backup" || true
      run_media_backup "Manual media backup" || true
    elif [ "$ACTION" = "restore" ]; then
      status running restore "$RFILE" "Restore in progress..."
      if out=$(sh /scripts/do_restore.sh "$RFILE" 2>&1); then
        status success restore "$RFILE" "Restore completed. Users may need to sign in again."
      else
        status error restore "$RFILE" "Restore failed: $(printf '%s' "$out" | tail -n1)"
      fi
    else
      status error "${ACTION:-unknown}" "$RFILE" "Unknown request"
    fi
    rm -f "$CTRL/processing.json"
  fi

  # 2) Nightly schedule at local date rollover (~00:00 Asia/Tehran).
  TODAY=$(date '+%Y-%m-%d')
  if [ "$TODAY" != "$LAST_DAY" ]; then
    LAST_DAY="$TODAY"
    run_db_backup "Nightly database backup" || true
    if media_due; then
      run_media_backup "Scheduled media backup (every 3 days)" || true
    fi
    if code_db_due; then
      run_code_db_backup "Scheduled code_db backup (every 3 days)" || true
    fi
  fi

  sleep 10
done
