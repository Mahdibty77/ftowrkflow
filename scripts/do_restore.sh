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

base=$(basename "$SRC")

if [ "$base" = "ftcode_db.tar.gz" ]; then
  if ! tar -xzf "$SRC" -C "$TMP"; then
    echo "ERR failed to extract code_db archive"; rm -rf "$TMP"; exit 1
  fi
  if [ -f "$TMP/code_db.tar.gz" ]; then
    rm -rf /data/code_db/* 2>/dev/null || :
    tar -xzf "$TMP/code_db.tar.gz" -C /data/code_db 2>/dev/null || :
  else
    rm -rf /data/code_db/* 2>/dev/null || :
    tar -xzf "$SRC" -C /data/code_db 2>/dev/null || :
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
  rm -rf /data/media/* 2>/dev/null || :
  tar -xzf "$TMP/media.tar.gz" -C /data/media 2>/dev/null || :
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

if ! psql -v ON_ERROR_STOP=1 -q -f "$TMP/database.sql"; then
  echo "ERR database restore failed (SQL error)"
  rm -rf "$TMP"
  exit 1
fi

if [ -f "$TMP/media.tar.gz" ]; then
  rm -rf /data/media/* 2>/dev/null || :
  tar -xzf "$TMP/media.tar.gz" -C /data/media 2>/dev/null || :
fi

if [ -f "$TMP/code_db.tar.gz" ]; then
  rm -rf /data/code_db/* 2>/dev/null || :
  tar -xzf "$TMP/code_db.tar.gz" -C /data/code_db 2>/dev/null || :
fi

rm -rf "$TMP"
echo "OK restored"
