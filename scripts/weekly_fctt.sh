#!/usr/bin/env bash
#
# Carga semanal de las ligas catalanas (fctt.cat) en tt-data, con carga selectiva.
#   Ligas (PARTE): TDM (estatals) · RTB (Barcelona) · RTG (Girona) · RTT (Tarragona)
#   → competiciones fctt_2627 / rtbtt_2627 / rtg_2627 / rtt_2627.
#   Dos fases por liga: CALENDARIO (fixtures + marcador) y ACTAS (partidos
#   individuales). Todo incremental (hash por página).
#
# Ver `weekly_fctt.sh --help` para todas las opciones.

set -u
cd "$(dirname "$0")/.." || exit 1   # → tt-scraper/

export SEASON="${SEASON:-2026-2027}"
export STORAGE_BACKEND=d1

source scripts/_lib.sh   # tras fijar SEASON: PY, DB, SEASON_SHORT

PARTS_ALL=(TDM RTB RTG RTT)

usage() {
  cat <<EOF
Uso: weekly_fctt.sh [PARTE] [opciones]

Carga las ligas catalanas de fctt.cat en tt-data (calendario + actas), de forma
incremental. Sin argumentos, carga las cuatro ligas enteras.

PARTE (opcional):   TDM | RTB | RTG | RTT     (sin PARTE = las cuatro)
    TDM = Tercera Divisió (estatals) · RTB = Barcelona · RTG = Girona · RTT = Tarragona

Opciones:
  --category <cat>   Solo esa categoría (SOLO TDM; ej. --category TDM). En las
                     territoriales la categoría va en el slug del grupo (usa --group).
  --group <id>       Solo ese grupo. TDM: id de grupo (ej. TDM_G3). Territoriales:
                     slug (ej. rtb-pref, rtg-preferent). Requiere una PARTE concreta.
  --pending          Recarga SOLO los grupos con encuentros vencidos sin resultado
                     (fecha pasada y aún 'scheduled'), consultando la D1. Evita
                     recorrer los grupos ya cargados. Sin PARTE, aplica a todas.
                     Incompatible con --category/--group.
  --force            Ignora los hashes y reprocesa todo (re-emite y re-carga
                     aunque la página no haya cambiado). Útil para rellenar
                     campos nuevos (terreno de juego, árbitro, PF/PC) en lo ya
                     cargado esta temporada.
  --calendar-only    Solo la fase de calendario (no actas).
  --actas-only       Solo la fase de actas (no calendario).
  --list             Lista las categorías y grupos disponibles y termina.
  -h, --help         Muestra esta ayuda.

Variables de entorno:
  SEASON             Temporada (por defecto ${SEASON}). Ej: SEASON=2027-2028 weekly_fctt.sh RTB

Ejemplos:
  weekly_fctt.sh                       # las cuatro ligas, calendario + actas
  weekly_fctt.sh RTB                   # solo Barcelona
  weekly_fctt.sh RTB --pending         # solo los grupos de RTB con pendientes
  weekly_fctt.sh --force               # reprocesa TODO (rellena campos nuevos)
  weekly_fctt.sh --pending             # solo lo pendiente de todas las ligas
  weekly_fctt.sh TDM --group TDM_G3    # solo el grupo 3 de la Tercera División
  weekly_fctt.sh RTB --group rtb-pref  # solo el grupo Preferent de Barcelona
  weekly_fctt.sh RTG --actas-only      # solo actas de Girona
  weekly_fctt.sh --list                # ver categorías/grupos de cada liga
EOF
}

list_options() {
  _hr; echo "Categorías y grupos disponibles"; _hr
  echo "· TDM (estatals) — pasa la categoría con --category y el grupo con --group:"
  "$PY" main.py fctt list 2>/dev/null | sed -n '/Categoría/,/└/p' | sed 's/^/    /'
  for rep in rtb rtg rtt; do
    echo "· ${rep^^} — pasa el slug con --group:"
    "$PY" -c "from sources import FCTT_TERRITORIALS as T; print('    ' + ', '.join(T['$rep'].slugs))" 2>/dev/null
  done
}

# ── Parseo de argumentos ─────────────────────────────────────────────────────
PART=""
CATEGORY=""; GROUP=""; DO_CAL=1; DO_ACT=1; PENDING=0; FORCE=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)      usage; exit 0 ;;
    --list)         list_options; exit 0 ;;
    --pending)      PENDING=1; shift ;;
    --force)        FORCE=1; shift ;;
    --category)     CATEGORY="${2:-}"; shift 2 ;;
    --group)        GROUP="${2:-}"; shift 2 ;;
    --calendar-only) DO_ACT=0; shift ;;
    --actas-only)   DO_CAL=0; shift ;;
    TDM|RTB|RTG|RTT|tdm|rtb|rtg|rtt)
                    PART="${1^^}"; shift ;;
    *) echo "❌ Argumento no reconocido: $1"; echo; usage; exit 2 ;;
  esac
done

if [ "$DO_CAL" = 0 ] && [ "$DO_ACT" = 0 ]; then
  echo "❌ --calendar-only y --actas-only son excluyentes."; exit 2
fi
if [ "$PENDING" = 1 ] && { [ -n "$CATEGORY" ] || [ -n "$GROUP" ]; }; then
  echo "❌ --pending no se combina con --category/--group (ya decide los grupos solo)."; exit 2
fi
if { [ -n "$CATEGORY" ] || [ -n "$GROUP" ]; } && [ -z "$PART" ]; then
  echo "❌ --category/--group requieren una PARTE concreta (TDM|RTB|RTG|RTT)."; exit 2
fi

if [ -n "$PART" ]; then PARTS=("$PART"); else PARTS=("${PARTS_ALL[@]}"); fi

# ── Ejecución ────────────────────────────────────────────────────────────────
_hr
echo "FCTT · temporada ${SEASON} · $(date '+%Y-%m-%d %H:%M')"
echo "Ligas: ${PARTS[*]}${GROUP:+  · grupo=$GROUP}${CATEGORY:+  · categoría=$CATEGORY}" \
     "$([ "$PENDING" = 1 ] && echo '· solo pendientes')$([ "$FORCE" = 1 ] && echo '· --force (reprocesa todo)')$([ "$DO_CAL" = 0 ] && echo '· solo actas')$([ "$DO_ACT" = 0 ] && echo '· solo calendario')"
_hr
echo

PENDING_COMPS=()

comp_id_for() {
  case "$1" in
    TDM) echo "fctt_${SEASON_SHORT}" ;;
    RTB) echo "rtbtt_${SEASON_SHORT}" ;;
    RTG) echo "rtg_${SEASON_SHORT}" ;;
    RTT) echo "rtt_${SEASON_SHORT}" ;;
  esac
}

run_part() {
  local part="$1" rep comp_id grp="$GROUP"
  comp_id="$(comp_id_for "$part")"
  # --pending: los grupos a recargar salen de la D1 (los que tienen vencidos sin
  # resultado). Si no hay, no se recorre nada de esta liga.
  if [ "$PENDING" = 1 ]; then
    grp="$(pending_groups "$comp_id")"
    if [ -z "$grp" ]; then
      echo "▶ ${part}: sin pendientes, nada que recargar"; echo
      PENDING_COMPS+=("$comp_id"); return
    fi
    echo "▶ ${part}: recargando solo pendientes → ${grp}"
  fi
  local -a cal_extra=() acta_extra=()
  [ -n "$grp" ] && { cal_extra+=(--group "$grp"); acta_extra+=(--group "$grp"); }
  [ "$FORCE" = 1 ] && { cal_extra+=(--force); acta_extra+=(--force); }
  case "$part" in
    TDM)
      [ -n "$CATEGORY" ] && cal_extra+=(--category "$CATEGORY")
      [ "$DO_CAL" = 1 ] && run_and_load "TDM (estatals) — calendario" \
        "$PY" main.py fctt calendar "${cal_extra[@]}"
      [ "$DO_ACT" = 1 ] && run_and_load "TDM (estatals) — actas" \
        "$PY" main.py fctt actas --rep estatals "${acta_extra[@]}" ;;
    RTB|RTG|RTT)
      rep="${part,,}"
      [ -n "$CATEGORY" ] && echo "    (aviso: --category no aplica a ${part}; la categoría va en el slug, usa --group)"
      [ "$DO_CAL" = 1 ] && run_and_load "${part} — calendario" \
        "$PY" main.py fctt territorial --rep "$rep" "${cal_extra[@]}"
      [ "$DO_ACT" = 1 ] && run_and_load "${part} — actas" \
        "$PY" main.py fctt actas --rep "$rep" "${acta_extra[@]}" ;;
  esac
  PENDING_COMPS+=("$comp_id")
}

for p in "${PARTS[@]}"; do run_part "$p"; done

analyze
echo
report_pending "${PENDING_COMPS[@]}"
echo
echo "✅ FCTT: hecho."
