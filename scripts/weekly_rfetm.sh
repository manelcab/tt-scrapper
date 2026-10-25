#!/usr/bin/env bash
#
# Carga semanal de la RFETM (liga nacional) en tt-data, con carga selectiva.
#
# `rfetm run` recorre las categorías y, por jornada, carga en una sola pasada el
# calendario (fixtures) y los encuentros disputados CON sus partidos individuales.
# Es incremental (hash por página de jornada). Se puede acotar por categoría, grupo
# o jornada.
#
# Ver `weekly_rfetm.sh --help` para todas las opciones.

set -u
cd "$(dirname "$0")/.." || exit 1   # → tt-scraper/

export SEASON="${SEASON:-2026-2027}"
export STORAGE_BACKEND=d1

source scripts/_lib.sh   # tras fijar SEASON: PY, DB, SEASON_SHORT

usage() {
  cat <<EOF
Uso: weekly_rfetm.sh [opciones]

Carga la RFETM (liga nacional) en tt-data: calendario + resultados + partidos
individuales, de forma incremental. Sin opciones, carga todas las categorías.

Opciones:
  --category <cat>   Solo esa categoría (ej. --category liga1_M).
  --group <id>       Solo ese grupo (ej. --group liga1_g0_M).
  --jornada <N>      Solo esa jornada (ignora hashes; útil para forzar un refresco).
  --pending          Recarga SOLO los grupos con encuentros vencidos sin resultado
                     (fecha pasada y aún 'scheduled'), consultando la D1. Evita
                     recorrer los grupos ya cargados. Incompatible con
                     --category/--group/--jornada.
  --force            Ignora los hashes y reprocesa todo (re-emite y re-carga
                     aunque la página no haya cambiado). Útil para rellenar
                     campos nuevos (lugar, árbitro, PF/PC) en lo ya cargado.
  --list             Lista las categorías y grupos disponibles y termina.
  -h, --help         Muestra esta ayuda.

Variables de entorno:
  SEASON             Temporada (por defecto ${SEASON}). Ej: SEASON=2027-2028 weekly_rfetm.sh

Ejemplos:
  weekly_rfetm.sh                              # todas las categorías
  weekly_rfetm.sh --pending                    # solo los grupos con pendientes
  weekly_rfetm.sh --force                      # reprocesa TODO (rellena campos nuevos)
  weekly_rfetm.sh --category liga1_M           # solo Superdivisión Masculina
  weekly_rfetm.sh --group liga1_g0_M           # solo ese grupo
  weekly_rfetm.sh --category liga2_M --jornada 3   # solo la jornada 3 de esa categoría
  weekly_rfetm.sh --list                       # ver categorías/grupos
EOF
}

# ── Parseo de argumentos ─────────────────────────────────────────────────────
CATEGORY=""; GROUP=""; JORNADA=""; PENDING=0; FORCE=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)  usage; exit 0 ;;
    --list)     "$PY" main.py rfetm list; exit 0 ;;
    --pending)  PENDING=1; shift ;;
    --force)    FORCE=1; shift ;;
    --category) CATEGORY="${2:-}"; shift 2 ;;
    --group)    GROUP="${2:-}"; shift 2 ;;
    --jornada)  JORNADA="${2:-}"; shift 2 ;;
    *) echo "❌ Argumento no reconocido: $1"; echo; usage; exit 2 ;;
  esac
done

if [ "$PENDING" = 1 ] && { [ -n "$CATEGORY" ] || [ -n "$GROUP" ] || [ -n "$JORNADA" ]; }; then
  echo "❌ --pending no se combina con --category/--group/--jornada (ya decide los grupos solo)."; exit 2
fi

# ── Ejecución ────────────────────────────────────────────────────────────────
COMP_ID="rfetm_${SEASON_SHORT}"
# --pending: los grupos a recargar salen de la D1 (los que tienen vencidos sin
# resultado). Si no hay, no se recorre nada.
PENDING_GRP=""
[ "$PENDING" = 1 ] && PENDING_GRP="$(pending_groups "$COMP_ID")"

extra=()
if [ "$PENDING" = 1 ]; then
  [ -n "$PENDING_GRP" ] && extra+=(--group "$PENDING_GRP")
else
  [ -n "$CATEGORY" ] && extra+=(--category "$CATEGORY")
  [ -n "$GROUP" ]    && extra+=(--group "$GROUP")
  [ -n "$JORNADA" ]  && extra+=(--jornada "$JORNADA")
fi
[ "$FORCE" = 1 ] && extra+=(--force)

if [ "$PENDING" = 1 ]; then
  filtro="solo pendientes${PENDING_GRP:+ (grupos: $PENDING_GRP)}"
elif [ "${#extra[@]}" -eq 0 ]; then
  filtro="todas las categorías"
else
  filtro="${CATEGORY:+categoría=$CATEGORY }${GROUP:+grupo=$GROUP }${JORNADA:+jornada=$JORNADA}"
fi
[ "$FORCE" = 1 ] && filtro="${filtro:+$filtro · }--force (reprocesa todo)"

_hr
echo "RFETM · temporada ${SEASON} · $(date '+%Y-%m-%d %H:%M')"
echo "Filtro: ${filtro}"
_hr
echo

if [ "$PENDING" = 1 ] && [ -z "$PENDING_GRP" ]; then
  echo "▶ sin pendientes, nada que recargar"
  echo
else
  run_and_load "RFETM (calendario + resultados + partidos individuales)" \
    "$PY" main.py rfetm run "${extra[@]}"
fi

analyze
echo
report_pending "rfetm_${SEASON_SHORT}"
echo
echo "✅ RFETM: hecho."
