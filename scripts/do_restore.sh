#!/bin/sh
# Restore a backup archive from /backups/{db,media,code_db}/ (or legacy root).
#   * db/ftdb_*.tar.gz | legacy ftbackup_*  -> PostgreSQL (+ media if present)
#   * media/ftmedia_*.tar.gz                -> uploaded media volume only
#   * code_db/ftcode_db.tar.gz              -> code tables volume only
set -eu

NAME="$1"
case "$NAME" in
  ""|*..*|/*)
    echo "ERR invalid backup name: ${NAME}"
    exit 1
    ;;
esac

SRC="/backups/${NAME}"
if [ ! -f "$SRC" ]; then
  base=$(basename "$NAME")
  for cand in \
      "/backups/db/${base}" \
      "/backups/media/${base}" \
      "/backups/code_db/${base}" \
      "/backups/${base}"; do
    if [ -f "$cand" ]; then
      SRC="$cand"
      break
    fi
  done
fi
[ -f "$SRC" ] || { echo "ERR backup not found: ${NAME}"; exit 1; }

TMP="/backups/.restore_work"
rm -rf "$TMP"; mkdir -p "$TMP"

# Every restore below has to empty the destination volume before it can unpack
# into it, so the archive must be proven readable BEFORE the wipe and a failed
# unpack must stop the script. backup_service.sh judges a restore purely by our
# exit status, so an ignored tar error was reported to the admin as "Restore
# completed" while the volume it had just emptied stayed empty.
restore_into() {  # $1 = .tar.gz to unpack  $2 = destination directory
  if ! tar -tzf "$1" >/dev/null 2>&1; then
    echo "ERR archive unreadable, nothing was changed: $(basename "$1")"
    rm -rf "$TMP"
    exit 1
  fi
  rm -rf "$2"/* 2>/dev/null || :
  if ! tar -xzf "$1" -C "$2"; then
    echo "ERR failed to unpack $(basename "$1") into $2 (disk space? permissions?) - $2 is now empty, restore again once the cause is fixed"
    rm -rf "$TMP"
    exit 1
  fi
}

base=$(basename "$SRC")

if [ "$base" = "ftcode_db.tar.gz" ]; then
  if ! tar -xzf "$SRC" -C "$TMP"; then
    echo "ERR failed to extract code_db archive"; rm -rf "$TMP"; exit 1
  fi
  if [ -f "$TMP/code_db.tar.gz" ]; then
    restore_into "$TMP/code_db.tar.gz" /data/code_db
  else
    # Legacy archives are the code_db tarball itself, without the wrapper.
    restore_into "$SRC" /data/code_db
  fi
  rm -rf "$TMP"
  echo "OK restored code_db"
  exit 0
fi

if ! tar -xzf "$SRC" -C "$TMP"; then
  echo "ERR failed to extract archive (corrupt or not a .tar.gz)"
  rm -rf "$TMP"
  exit 1
fi

if [ ! -f "$TMP/database.sql" ] && [ -f "$TMP/media.tar.gz" ]; then
  restore_into "$TMP/media.tar.gz" /data/media
  rm -rf "$TMP"
  echo "OK restored media"
  exit 0
fi

[ -f "$TMP/database.sql" ] || {
  echo "ERR invalid backup (need database.sql or media.tar.gz)"
  rm -rf "$TMP"
  exit 1
}

psql -v ON_ERROR_STOP=0 -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid();" \
  >/dev/null 2>&1 || :

# --single-transaction is what makes a failed restore survivable: the dump is
# produced by pg_dump --clean --if-exists (do_backup.sh), so it opens by
# DROPping every table. Statement-by-statement autocommit meant those DROPs were
# already permanent by the time ON_ERROR_STOP aborted on a later bad statement,
# leaving the live database empty with nothing to roll back to. Wrapped in one
# transaction the whole restore either lands or leaves the database untouched.
if ! psql --single-transaction -v ON_ERROR_STOP=1 -q -f "$TMP/database.sql"; then
  echo "ERR database restore failed (SQL error)"
  rm -rf "$TMP"
  exit 1
fi

if [ -f "$TMP/media.tar.gz" ]; then
  restore_into "$TMP/media.tar.gz" /data/media
fi

if [ -f "$TMP/code_db.tar.gz" ]; then
  restore_into "$TMP/code_db.tar.gz" /data/code_db
fi

rm -rf "$TMP"
echo "OK restored"
