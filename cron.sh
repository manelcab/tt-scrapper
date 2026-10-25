#!/usr/bin/env bash
# Wrapper para cron: fija entorno, evita solapes (flock) y registra en log.
# Uso: cron.sh            → run_all.sh (solo pendiente)
#      cron.sh --full     → run_all.sh --full (repaso completo por hash)
cd /home/manu/tt-scrapper || exit 1
. ./env.sh
mkdir -p output/logs
{
  echo "=== $(date "+%F %T") · run_all $* ==="
  flock -n output/.run_all.lock bash scripts/run_all.sh "$@"
  rc=$?
  [ "$rc" = 1 ] && echo "(otra ejecución en curso: saltada por el lock)"
  [ "$rc" != 0 ] && echo "run_all código $rc"
  echo
} >> output/logs/cron.log 2>&1
