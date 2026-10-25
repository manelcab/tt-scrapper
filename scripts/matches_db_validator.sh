#!/usr/bin/env bash
#
# Validador del estado de carga en tt-data. Muestra UNA fila por liga (FCTT, RTB,
# RTG, RTT, RFETM) con, de la competición:
#   - Encuentros (total del calendario) y Semanas naturales planificadas.
#   - Fecha fin de la última semana planificada.
#   - Semanas cargadas (con algún resultado) y fecha de la última carga.
#   - Encuentros cargados (status='completed') y partidos individuales cargados.
#   - Comentario: si faltan encuentros por cargar y otras pistas para leer la fila.
# Y, debajo, el DETALLE de los encuentros que faltan por cargar (grupo + equipos)
# para poder validarlos a mano en la web.
#
# Por qué SEMANA NATURAL y no jornada: cada grupo numera sus jornadas por su cuenta
# y las juega en fechas distintas, así que contar por `matchday_number` mezcla peras
# con manzanas. La semana natural (ISO `%Y-%W`) es una frontera de calendario común:
# un grupo que esta semana no juega simplemente no tiene encuentros en ella y no
# cuenta. El "falta por cargar" es, por eso, por fecha: encuentros con FECHA YA
# PASADA que siguen 'scheduled' (mismo criterio que report_pending).
#
# Uso:   bash scripts/matches_db_validator.sh
# (SEASON por defecto 2026-2027).

set -u
cd "$(dirname "$0")/.." || exit 1   # → tt-scraper/

export SEASON="${SEASON:-2026-2027}"

source scripts/_lib.sh   # tras fijar SEASON: PY, DB, SEASON_SHORT
SS="$SEASON_SHORT"

COMPS="'fctt_${SS}','rtbtt_${SS}','rtg_${SS}','rtt_${SS}','rfetm_${SS}'"

# Etiquetas y orden de las ligas (compartido por la tabla y el detalle).
read -r -d '' PY_LABELS <<'PY_END' || true
LABELS = {
    'fctt':  'FCTT (TDM)',
    'rtbtt': 'RTB (Barcelona)',
    'rtg':   'RTG (Girona)',
    'rtt':   'RTT (Tarragona)',
    'rfetm': 'RFETM',
}
ORDER = ['fctt', 'rtbtt', 'rtg', 'rtt', 'rfetm']
def league_key(cid):
    return cid.rsplit('_', 1)[0]
PY_END

echo "Estado de carga en ${DB} · temporada ${SEASON} · $(date '+%Y-%m-%d %H:%M')"

# ── Tabla resumen ────────────────────────────────────────────────────────────
read -r -d '' SQL_TABLE <<SQL_END || true
WITH comp(cid,ord,label) AS (
  VALUES ('fctt_${SS}',1,'FCTT (TDM)'),
         ('rtbtt_${SS}',2,'RTB (Barcelona)'),
         ('rtg_${SS}',3,'RTG (Girona)'),
         ('rtt_${SS}',4,'RTT (Tarragona)'),
         ('rfetm_${SS}',5,'RFETM')
),
agg AS (
  SELECT competition_id AS cid,
    COUNT(*) AS tot_enc,
    COUNT(DISTINCT CASE WHEN date IS NOT NULL THEN strftime('%Y-%W',date) END) AS weeks_plan,
    COUNT(DISTINCT CASE WHEN status='completed' THEN strftime('%Y-%W',date) END) AS weeks_load,
    MAX(date) AS last_plan_date,
    MAX(CASE WHEN status='completed' THEN date END) AS last_load_date,
    SUM(status='completed') AS loaded_enc,
    SUM(status='scheduled' AND date IS NOT NULL AND date(date) < date('now')) AS pending_past,
    SUM(status='completed' AND date IS NOT NULL AND date(date) > date('now')) AS future_loaded
  FROM encounter WHERE competition_id IN (SELECT cid FROM comp)
  GROUP BY competition_id
)
SELECT c.ord, c.label AS liga,
  COALESCE(a.tot_enc,0) AS tot_enc,
  COALESCE(a.weeks_plan,0) AS weeks_plan,
  a.last_plan_date,
  COALESCE(a.weeks_load,0) AS weeks_load,
  a.last_load_date,
  COALESCE(a.loaded_enc,0) AS loaded_enc,
  (SELECT COUNT(*) FROM individual_match im WHERE im.competition_id=c.cid) AS loaded_matches,
  COALESCE(a.pending_past,0) AS pending_past,
  COALESCE(a.future_loaded,0) AS future_loaded
FROM comp c LEFT JOIN agg a ON a.cid=c.cid
ORDER BY c.ord;
SQL_END

d1_json "$SQL_TABLE" \
  | "$PY" -c "
import json, sys
try:
    rows = json.load(sys.stdin)[0]['results']
except Exception:
    print('  ✗ no se pudo consultar la D1 (¿wrangler/credenciales?).'); sys.exit(1)

def d(s):
    return (s or '')[:10] if s else '—'

def comment(r):
    tot, loaded, pend, mts = r['tot_enc'], r['loaded_enc'], r['pending_past'], r['loaded_matches']
    if tot == 0:
        return 'sin datos en la BD (competición no cargada)'
    if loaded == 0:
        return 'sin resultados aún (semana no disputada o no cargada)'
    notes = []
    if pend > 0:
        notes.append(f'⚠ faltan {pend} encuentro(s) con fecha pasada sin cargar → re-lanzar carga')
    else:
        notes.append('✓ al día (ningún encuentro vencido sin resultado)')
    if mts == 0:
        notes.append('encuentros sin actas (partidos individuales = 0)')
    if r.get('future_loaded', 0) > 0:
        notes.append(f\"{r['future_loaded']} resultado(s) con fecha futura (partido adelantado o fecha errónea)\")
    return '; '.join(notes)

cols = [
    ('Liga',           'liga',            'l', 15),
    ('Enc.tot',        'tot_enc',         'r', 7),
    ('Sem.plan',       'weeks_plan',      'r', 8),
    ('Fin últ.sem',    'last_plan_date',  'l', 11),
    ('Sem.carg',       'weeks_load',      'r', 8),
    ('Últ.carga',      'last_load_date',  'l', 10),
    ('Enc.carg',       'loaded_enc',      'r', 8),
    ('Partidos',       'loaded_matches',  'r', 8),
]

def cell(r, key):
    if key in ('last_plan_date', 'last_load_date'):
        return d(r[key])
    return str(r[key])

widths = {}
for head, key, _, mn in cols:
    widths[key] = max(len(head), mn, *(len(cell(r, key)) for r in rows))

def fmt(key, align, text):
    w = widths[key]
    return text.ljust(w) if align == 'l' else text.rjust(w)

header = '  '.join(fmt(k, a, h) for h, k, a, _ in cols) + '  ' + 'Comentario'
print()
print(header)
print('─' * len(header))
for r in rows:
    line = '  '.join(fmt(k, a, cell(r, k)) for h, k, a, _ in cols)
    print(line + '  ' + comment(r))
print()
"

# ── Detalle: encuentros que faltan por cargar ────────────────────────────────
read -r -d '' SQL_DETAIL <<SQL_END || true
SELECT e.competition_id AS cid, e.group_id AS grp, g.name AS gname,
       e.matchday_number AS j, e.date, e.team_local_name AS loc, e.team_visitor_name AS vis
FROM encounter e
LEFT JOIN comp_group g ON g.competition_id=e.competition_id AND g.id=e.group_id
WHERE e.competition_id IN (${COMPS})
  AND e.status='scheduled' AND e.date IS NOT NULL AND date(e.date) < date('now')
ORDER BY e.competition_id, e.date, e.group_id;
SQL_END

d1_json "$SQL_DETAIL" \
  | "$PY" -c "
import json, sys
${PY_LABELS}
try:
    rows = json.load(sys.stdin)[0]['results']
except Exception:
    print('  ✗ no se pudo consultar el detalle.'); sys.exit(1)

print('Encuentros que faltan por cargar (fecha pasada, aún sin resultado) — validar en la web:')
if not rows:
    print('  ✅ ninguno — no hay encuentros vencidos sin cargar.')
    sys.exit(0)

by = {}
for r in rows:
    by.setdefault(league_key(r['cid']), []).append(r)

for key in ORDER:
    grp_rows = by.get(key)
    if not grp_rows:
        continue
    print(f'  {LABELS[key]}  ({len(grp_rows)})')
    for r in grp_rows:
        dd = (r['date'] or '')[8:10] + '/' + (r['date'] or '')[5:7]
        name = r['gname'] or r['grp']
        print(f\"    · [{name}] J{r['j']} · {dd} · {r['loc']} vs {r['vis']}\")
print()
print('  Si alguno no cuadra (un equipo que en realidad descansa, o un rival que no')
print('  es de ese grupo), puede ser un equipo RETIRADO cuyo calendario quedó como')
print('  fantasma. Audítalo con:  venv/bin/python scripts/detect_orphans.py')
print()
"
