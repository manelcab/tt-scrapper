#!/usr/bin/env bash
#
# Orquestador: lanza las cargas semanales (solo lo PENDIENTE donde se puede) y
# genera un report HTML del estado. Pensado para ejecutarse una o varias veces al
# día; es seguro repetir (todo es incremental/idempotente).
#
# Pasos, en orden:
#   1. weekly_fctt.sh --pending      (TDM + territoriales: solo grupos con vencidos)
#   2. weekly_rfetm.sh --pending     (liga nacional: solo grupos con vencidos)
#   3. weekly_rankings.sh            (rànquings FCTT + RFETM, incremental por hash)
#   4. weekly_identity.sh            (recalcula person/person_licence tras las cargas)
#   5. build_report.py               (report HTML con semáforos % por liga/semana)
#
# Cada paso captura su log, código de salida y duración; un paso que falle NO
# aborta el resto (el report lo refleja). El report queda en output/reports/.
#
# Tras el report, lo EMPUJA a Kayzen (PUT) si hay credenciales en el entorno
# (KAYZEN_REPORT_URL / KAYZEN_REPORT_TOKEN, que env.sh toma de ~/.tt-scrapper-secrets).
# Kayzen lo sirve detrás del rol de admin en kaizen.gravityplayers.net/status/jobs;
# el VPS no expone nada. Un fallo del PUT NO aborta la pasada (el siguiente empujón
# lo arregla y mientras tanto Kayzen sirve el último que llegó). Ver
# design/PARA-TT-SCRAPER-report.md.
#
# Uso:   bash scripts/run_all.sh [--no-identity] [--full] [--no-push]
#   --no-identity  no recalcula identidad (solo cargas + report).
#   --full         carga TODO, no solo lo pendiente (fctt/rfetm sin --pending).
#   --no-push      no empuja el report a Kayzen (útil para pruebas locales).

set -u
cd "$(dirname "$0")/.." || exit 1   # → tt-scraper/

export SEASON="${SEASON:-2026-2027}"
source scripts/_lib.sh              # PY, DB, SEASON_SHORT, NODE_OPTIONS

DO_IDENTITY=1
SELECTIVE="--pending"
DO_PUSH=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-identity) DO_IDENTITY=0; shift ;;
    --full)        SELECTIVE=""; shift ;;
    --no-push)     DO_PUSH=0; shift ;;
    -h|--help)
      sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "❌ Argumento no reconocido: $1"; exit 2 ;;
  esac
done

STAMP="$(date +%Y%m%d_%H%M%S)"
LOGDIR="output/logs/run_${STAMP}"
mkdir -p "$LOGDIR" output/reports
STEPS="$LOGDIR/steps.tsv"
: > "$STEPS"

# run_step <nombre> <script> [args…]: ejecuta, captura log/código/segundos.
run_step() {
  local name="$1"; shift
  local safe log start code end
  safe="$(echo "$name" | tr ' /' '__')"
  log="$LOGDIR/${safe}.log"
  _hr; echo "▶ ${name}  →  ${log}"; _hr
  start="$(date +%s)"
  bash "$@" >"$log" 2>&1
  code=$?
  end="$(date +%s)"
  tail -n 3 "$log" | sed 's/^/    /'
  [ "$code" -ne 0 ] && echo "    ⚠️  ${name}: código de salida ${code}"
  printf '%s\t%s\t%s\t%s\n' "$name" "$code" "$((end - start))" "$log" >> "$STEPS"
  echo
}

_hr
echo "run_all · temporada ${SEASON} · DB=${DB} · $(date '+%Y-%m-%d %H:%M')"
echo "Modo: $([ -n "$SELECTIVE" ] && echo 'solo pendiente' || echo 'carga completa')$([ "$DO_IDENTITY" = 0 ] && echo ' · sin identidad')"
_hr
echo

run_step "FCTT"      scripts/weekly_fctt.sh $SELECTIVE
run_step "RFETM"     scripts/weekly_rfetm.sh $SELECTIVE
run_step "Rankings"  scripts/weekly_rankings.sh
[ "$DO_IDENTITY" = 1 ] && run_step "Identidad" scripts/weekly_identity.sh

# ── Report ───────────────────────────────────────────────────────────────────
_hr; echo "▶ Report HTML"; _hr
"$PY" scripts/build_report.py "$STEPS" --db "$DB" --season "$SEASON" --out output/reports

# ── Empujar el report a Kayzen ────────────────────────────────────────────────
# PUT idempotente del último HTML. Nunca aborta la pasada (PARA-TT-SCRAPER-report.md §3).
REPORT_HTML="output/reports/latest.html"
if [ "$DO_PUSH" = 0 ]; then
  echo "ℹ️  push del report omitido (--no-push)"
elif [ -z "${KAYZEN_REPORT_URL:-}" ] || [ -z "${KAYZEN_REPORT_TOKEN:-}" ]; then
  echo "ℹ️  push del report omitido (faltan KAYZEN_REPORT_URL / KAYZEN_REPORT_TOKEN)"
elif [ ! -f "$REPORT_HTML" ]; then
  echo "⚠️  push del report omitido (no existe $REPORT_HTML)"
else
  _hr; echo "▶ Empujar report → Kayzen"; _hr
  if curl -sS --fail-with-body -X PUT \
       -H "Authorization: Bearer ${KAYZEN_REPORT_TOKEN}" \
       -H "Content-Type: text/html" \
       --data-binary @"$REPORT_HTML" \
       "$KAYZEN_REPORT_URL"; then
    echo; echo "    ✅ report empujado"
  else
    echo; echo "    ⚠️  no se pudo empujar el report (se reintenta en la próxima pasada)"
  fi
fi

echo
echo "✅ run_all: hecho. Abre output/reports/latest.html"
