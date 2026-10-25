# Helpers para las cargas semanales en tt-data. Se usa (source) desde
# weekly_rfetm.sh / weekly_fctt.sh, que fijan cwd en tt-scraper/.
#
# run_and_load "<etiqueta>" <comando...>:
#   ejecuta el scrapeo (emite un .sql en output/sql/), muestra las líneas útiles
#   del resumen (grupos, encuentros, individuales, errores…) y, si el comando ha
#   generado un .sql NUEVO, lo carga en D1. Si no hubo cambios, no carga nada.

PY="${PY:-venv/bin/python}"
DB="${DB:-tt-data}"
# wrangler: por defecto el del monorepo (apps/api). En otro despliegue (VPS) se
# apunta con TTS_WRANGLER_CWD (dir con wrangler.toml + node_modules) y, si hace
# falta, TTS_WRANGLER (ruta al binario, relativa a ese dir o absoluta).
WRANGLER_CWD="${TTS_WRANGLER_CWD:-../apps/api}"
WRANGLER_BIN="${TTS_WRANGLER:-node_modules/.bin/wrangler}"
# La API de Cloudflare resuelve IPv6 primero; si el IPv6 de la red está roto (o
# parpadea), el fetch de Node (undici, que usa wrangler) falla con "fetch failed".
# Forzamos IPv4 como en deploy.sh para que las consultas a la D1 sean fiables.
export NODE_OPTIONS="--dns-result-order=ipv4first${NODE_OPTIONS:+ $NODE_OPTIONS}"
# Sufijo de temporada (2026-2027 → 2627) para construir los competition_id.
# Se deriva de SEASON, que los scripts fijan ANTES de sourcear este fichero. El
# `${SEASON:-2026-2027}` intermedio evita que `set -u` pete si se sourea suelto.
_season_for_short="${SEASON:-2026-2027}"
SEASON_SHORT="${SEASON_SHORT:-${_season_for_short:2:2}${_season_for_short:7:2}}"

# d1_json "<SQL>": ejecuta una consulta --json contra la D1 con reintentos y
# emite el JSON por stdout. wrangler cae a veces con "fetch failed" por cortes de
# red; reintentamos (como deploy.sh). Si agota los intentos, muestra el error
# REAL de wrangler por stderr (en vez de tragárselo) y devuelve !=0.
d1_json() {
  local sql="$1" out rc n=1 max=3 err
  err="$(mktemp)"
  while :; do
    out="$( cd "$WRANGLER_CWD" && "$WRANGLER_BIN" d1 execute "$DB" \
            --remote --json --command "$sql" 2>"$err" )"
    rc=$?
    if [ "$rc" -eq 0 ] && [ -n "$out" ]; then
      rm -f "$err"; printf '%s' "$out"; return 0
    fi
    if [ "$n" -ge "$max" ]; then
      echo "    ✗ wrangler/D1 falló tras ${max} intentos:" >&2
      sed 's/^/      /' "$err" >&2 2>/dev/null
      rm -f "$err"; return 1
    fi
    n=$((n + 1)); sleep 3
  done
}

# pending_groups <competition_id>: devuelve (por stdout, separados por comas) los
# group_id de esa competición con encuentros VENCIDOS sin resultado (fecha pasada y
# aún 'scheduled'). Vacío si no hay. Sirve para recargar solo lo pendiente.
pending_groups() {
  local comp="$1"
  d1_json "SELECT DISTINCT group_id AS g FROM encounter
           WHERE competition_id='${comp}' AND status='scheduled'
             AND date IS NOT NULL AND date(date) < date('now')
           ORDER BY group_id;" \
    | "$PY" -c "import json,sys
try:
    r=json.load(sys.stdin)[0]['results']
except Exception:
    sys.exit(0)
print(','.join(x['g'] for x in r if x.get('g')))"
}

_hr() { printf '─%.0s' {1..64}; echo; }

run_and_load() {
  local label="$1"; shift
  local marker; marker="$(mktemp)"
  echo "▶ ${label}"
  # Scrapeo: filtramos a lo útil (resumen + avisos + errores).
  "$@" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -E \
    "Grupos|Equipos|Jornadas|Encuentros|Partidos|individuales|Errores:|•|fuera de temporada|SeasonMismatch|❌|Ediciones|Meses|Categorías|categoría|Filas parseadas|Jugadores|Escribiendo" \
    | sed 's/^ *//; s/^/    /'
  # ¿Se generó un .sql nuevo (más reciente que el marcador)?
  local sql
  sql="$(find output/sql -maxdepth 1 -name 'load_*.sql' -newer "$marker" -printf '%T@ %p\n' 2>/dev/null \
         | sort -nr | head -1 | cut -d' ' -f2-)"
  rm -f "$marker"
  if [ -n "$sql" ]; then
    local n; n="$(grep -c 'INSERT INTO' "$sql" 2>/dev/null || echo 0)"
    echo "    → cargando $(basename "$sql") (${n} statements)"
    "$PY" d1/load_chunks.py "$sql" --db "$DB" 2>&1 \
      | grep -E "Filas escritas|🛑|❌|falló|cuota" | sed 's/^ *//; s/^/    ↳ /'
  else
    echo "    → sin cambios que cargar"
  fi
  echo
}

# ANALYZE una sola vez al final (refresca sqlite_stat1; ver design/PETICION-A-TT-SCRAPER.md).
analyze() {
  echo "🔎 ANALYZE (${DB})…"
  d1_json "ANALYZE;" \
    | "$PY" -c "import json,sys
try:
    m=json.load(sys.stdin)[0].get('meta',{})
    print(f\"    ↳ ok (leídas {m.get('rows_read')}, escritas {m.get('rows_written')})\")
except Exception:
    print('    ↳ (no se pudo leer el resultado del ANALYZE)')"
}

# report_pending <competition_id...>:
#   lista los encuentros cuya FECHA YA PASÓ pero siguen en 'scheduled' (la FCTT/RFETM
#   aún no ha publicado el resultado, o está aplazado). Sirve para saber qué grupos
#   hay que volver a cargar más adelante. Lee el estado real de la D1 (refleja la web
#   aunque un grupo se haya saltado por hash en esta pasada).
report_pending() {
  [ "$#" -gt 0 ] || return 0
  local in
  in="$(printf "'%s'," "$@")"; in="${in%,}"
  echo "🕓 Encuentros con fecha pasada y aún SIN resultado (re-lanzar más tarde):"
  d1_json "
      SELECT competition_id AS comp, group_id AS grp, matchday_number AS j,
             team_local_name AS loc, team_visitor_name AS vis, date
      FROM encounter
      WHERE competition_id IN (${in})
        AND status='scheduled' AND date IS NOT NULL AND date(date) < date('now')
      ORDER BY date, competition_id, group_id;" \
    | "$PY" -c "import json,sys
try:
    r=json.load(sys.stdin)[0]['results']
except Exception:
    print('    ↳ (no se pudo consultar la D1)'); sys.exit(0)
if not r:
    print('    ✅ ninguno — todos los encuentros disputados tienen resultado.'); sys.exit(0)
LIMIT=60
for x in r[:LIMIT]:
    d=(x.get('date') or '')[8:10]+'/'+(x.get('date') or '')[5:7]
    print(f\"    · {x['comp']:<11} {x['grp']:<16} J{x['j']:<3} {d}  {x['loc']} vs {x['vis']}\")
if len(r)>LIMIT:
    print(f'    · … y {len(r)-LIMIT} más')
grp={(x['comp'],x['grp']) for x in r}
print(f'    → {len(r)} pendiente(s) en {len(grp)} grupo(s). Vuelve a lanzar la carga cuando se publiquen.')"
}
