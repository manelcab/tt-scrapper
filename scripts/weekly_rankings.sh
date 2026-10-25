#!/usr/bin/env bash
#
# Carga semanal de los RÀNQUINGS mensuales en tt-data: FCTT y RFETM.
#
# Distinto de weekly_fctt.sh / weekly_rfetm.sh, que cargan PARTIDOS (calendario,
# actas, resultados). Los rànquings se publican una vez al mes y tienen su propio
# comando; este script los agrupa. Es incremental (hash por página/edición): si
# una edición no ha cambiado, no reescribe nada.
#
# Ver `weekly_rankings.sh --help` para todas las opciones.

set -u
cd "$(dirname "$0")/.." || exit 1   # → tt-scraper/

export SEASON="${SEASON:-2026-2027}"
export STORAGE_BACKEND=d1

source scripts/_lib.sh   # tras fijar SEASON: PY, DB, SEASON_SHORT

PARTS_ALL=(FCTT RFETM)

usage() {
  cat <<EOF
Uso: weekly_rankings.sh [PARTE] [opciones]

Carga los rànquings mensuales en tt-data, de forma incremental. Sin argumentos,
carga las dos federaciones.

PARTE (opcional):   FCTT | RFETM     (sin PARTE = las dos)

Opciones:
  --edition <id>   Solo esa edición (FCTT: nº, ej. 10 · RFETM: mes, ej. 2026-09).
  --category <cat> Solo esa categoría (SOLO FCTT; ej. SEM).
  --force          Ignora los hashes y reprocesa todo.
  -h, --help       Muestra esta ayuda.

Variables de entorno:
  SEASON           Temporada (por defecto ${SEASON}). Ej: SEASON=2027-2028 weekly_rankings.sh

Ejemplos:
  weekly_rankings.sh                      # rànquings FCTT + RFETM
  weekly_rankings.sh FCTT                 # solo FCTT
  weekly_rankings.sh FCTT --edition 9     # solo la edición 9 de la FCTT
  weekly_rankings.sh RFETM --edition 2026-09   # solo septiembre de la RFETM
  weekly_rankings.sh --force              # reprocesa todo (ignora hashes)
EOF
}

# ── Parseo de argumentos ─────────────────────────────────────────────────────
PART=""; EDITION=""; CATEGORY=""; FORCE=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)   usage; exit 0 ;;
    --edition)   EDITION="${2:-}"; shift 2 ;;
    --category)  CATEGORY="${2:-}"; shift 2 ;;
    --force)     FORCE=1; shift ;;
    FCTT|RFETM|fctt|rfetm) PART="${1^^}"; shift ;;
    *) echo "❌ Argumento no reconocido: $1"; echo; usage; exit 2 ;;
  esac
done

if [ -n "$PART" ]; then PARTS=("$PART"); else PARTS=("${PARTS_ALL[@]}"); fi

# ── Ejecución ────────────────────────────────────────────────────────────────
_hr
echo "RÀNQUINGS · temporada ${SEASON} · $(date '+%Y-%m-%d %H:%M')"
echo "Federaciones: ${PARTS[*]}${EDITION:+  · edición=$EDITION}${CATEGORY:+  · categoría=$CATEGORY}$([ "$FORCE" = 1 ] && echo '  · --force')"
_hr
echo

run_part() {
  local part="$1"
  local -a extra=()
  [ -n "$EDITION" ] && extra+=(--edition "$EDITION")
  [ "$FORCE" = 1 ]  && extra+=(--force)
  case "$part" in
    FCTT)
      [ -n "$CATEGORY" ] && extra+=(--category "$CATEGORY")
      run_and_load "FCTT — rànquings mensuales" \
        "$PY" main.py fctt rankings "${extra[@]}" ;;
    RFETM)
      [ -n "$CATEGORY" ] && echo "    (aviso: --category no aplica a RFETM; se ignora)"
      run_and_load "RFETM — rànquings mensuales" \
        "$PY" main.py rfetm rankings "${extra[@]}" ;;
  esac
}

for p in "${PARTS[@]}"; do run_part "$p"; done

analyze
echo
echo "✅ Rànquings: hecho."
