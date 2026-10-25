#!/usr/bin/env bash
#
# Recalcula la capa de identidad de persona en tt-data y la carga
# (design/plan_identidad_persona.md). Encadena, EN ORDEN (cada paso carga antes
# del siguiente, porque el siguiente lee de D1 lo que dejó el anterior):
#
#   1. build_player_link  → puente FCTT↔RFETM por partidos de liga nacional
#   2. build_club_link    → club FCTT-código ↔ RFETM-nombre (de los confirmados)
#   3. build_person       → person / person_licence (confirmado + probable)
#
# Pensado para pasar DESPUÉS de las cargas semanales (weekly_fctt/rfetm/rankings),
# cuando ya hay partidos y rankings nuevos. Escribe en D1 (consume cuota).
#
# Ver `weekly_identity.sh --help`.

set -u
cd "$(dirname "$0")/.." || exit 1   # → tt-scraper/

source scripts/_lib.sh   # PY, DB, NODE_OPTIONS, _hr

usage() {
  cat <<EOF
Uso: weekly_identity.sh [opciones]

Recalcula y carga la identidad de persona (player_link → club_link → person).

Opciones:
  --no-probable   build_person solo con lo confirmado (sin nombre+club).
  --dry-run       calcula y genera los .sql pero NO carga en D1.
  -h, --help      Esta ayuda.

Variables de entorno:
  DB              Base D1 (por defecto ${DB}).
EOF
}

DRY=0
PROBABLE_ARGS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)     usage; exit 0 ;;
    --dry-run)     DRY=1; shift ;;
    --no-probable) PROBABLE_ARGS+=(--no-probable); shift ;;
    *) echo "❌ Argumento no reconocido: $1"; echo; usage; exit 2 ;;
  esac
done

# Ejecuta un builder y carga su .sql más reciente (salvo --dry-run).
run_step() {
  local label="$1" prefix="$2"; shift 2
  _hr; echo "▶ ${label}"; _hr
  "$PY" "$@" || { echo "❌ ${label}: el builder falló"; exit 1; }
  if [ "$DRY" = 1 ]; then echo "(dry-run: no se carga)"; echo; return 0; fi
  local f
  f="$(ls -t "output/sql/${prefix}_"*.sql 2>/dev/null | head -1)"
  if [ -z "$f" ]; then echo "⚠️  ${label}: sin fichero que cargar"; echo; return 0; fi
  "$PY" d1/load_chunks.py "$f" --db "$DB" || { echo "❌ ${label}: la carga falló"; exit 1; }
  echo
}

_hr
echo "Identidad de persona · $(date '+%Y-%m-%d %H:%M') · DB=${DB}"
echo "$([ "$DRY" = 1 ] && echo 'modo dry-run (no carga)')$([ ${#PROBABLE_ARGS[@]} -gt 0 ] && echo ' · solo confirmado')"
_hr
echo

run_step "player_link (puente por liga nacional)" player_link d1/build_player_link.py --db "$DB"
run_step "club_link (club FCTT↔RFETM)" club_link d1/build_club_link.py --db "$DB" --emit
run_step "person (confirmado + probable)" person d1/build_person.py --db "$DB" "${PROBABLE_ARGS[@]}"
# Independiente de los anteriores (solo usa encounter/individual_match): club
# vigente por licencia y temporada, desde las alineaciones.
run_step "player_club_season (club por temporada)" player_club_season d1/build_player_club_season.py --db "$DB"

echo "✅ Identidad: hecho."
