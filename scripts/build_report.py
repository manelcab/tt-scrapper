#!/usr/bin/env python3
"""Genera un report HTML del estado de carga de tt-data y del resultado de la
última ejecución del orquestador (scripts/run_all.sh).

- Consulta la D1 el estado de carga por liga y SEMANA NATURAL (ISO %Y-%W): de
  cada semana, cuántos encuentros vencidos (fecha <= hoy) están cargados
  (status='completed'). El % = vencidos cargados / vencidos.
- Lee el resumen de la ejecución (un .tsv por paso: nombre, código de salida,
  segundos, ruta del log) para mostrar qué pasos fueron bien y los errores.
- Pinta semáforos (% por liga) arriba, una matriz liga×semana coloreada, el
  detalle de encuentros pendientes y el log de cada paso.

Uso (desde tt-scraper/):
    python scripts/build_report.py <steps.tsv> --db tt-data --season 2026-2027 \\
        --out output/reports
"""
from __future__ import annotations
import os

import argparse
import datetime as dt
import html
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# wrangler vive en apps/api (monorepo) por defecto; en otro despliegue (VPS) se
# apunta con TTS_WRANGLER_CWD (dir con wrangler.toml + node_modules) y, si hace
# falta, TTS_WRANGLER (ruta al binario).
WRANGLER_CWD = Path(os.environ.get("TTS_WRANGLER_CWD") or (REPO / "apps" / "api"))
WRANGLER = Path(os.environ.get("TTS_WRANGLER") or (WRANGLER_CWD / "node_modules" / ".bin" / "wrangler"))

LEAGUES = [            # (key, label)
    ("fctt", "FCTT (TDM)"),
    ("rtbtt", "RTB (Barcelona)"),
    ("rtg", "RTG (Girona)"),
    ("rtt", "RTT (Tarragona)"),
    ("rfetm", "RFETM"),
]


def query(db: str, sql: str) -> list[dict]:
    proc = subprocess.run(
        [str(WRANGLER), "d1", "execute", db, "--remote", "--json", "--command", sql],
        cwd=str(WRANGLER_CWD), capture_output=True, text=True,
    )
    raw = proc.stdout.strip() or proc.stderr.strip()
    starts = [p for p in (raw.find("["), raw.find("{")) if p != -1]
    if not starts:
        return []
    try:
        data = json.loads(raw[min(starts):])
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict) and data.get("error"):
        return []
    results = data if isinstance(data, list) else [data]
    out: list[dict] = []
    for r in results:
        out.extend((r or {}).get("results") or [])
    return out


def season_short(season: str) -> str:
    # "2026-2027" → "2627"
    try:
        a, b = season.split("-")
        return a[2:4] + b[2:4]
    except ValueError:
        return season


def pct_class(p: float | None) -> str:
    if p is None:
        return "na"
    if p >= 95:
        return "ok"
    if p >= 70:
        return "warn"
    return "bad"


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def short_date(s: str | None) -> str:
    return f"{s[8:10]}/{s[5:7]}" if s and len(s) >= 10 else "—"


# ── Consultas ────────────────────────────────────────────────────────────────

def load_matrix(db: str, comps: list[str]) -> dict:
    cin = ",".join(f"'{c}'" for c in comps)
    sql = (
        "SELECT competition_id cid, strftime('%Y-%W', date) wk, MIN(date) wk_date, "
        "COUNT(*) tot, "
        "SUM(date(date) <= date('now')) due, "
        "SUM(status='completed' AND date(date) <= date('now')) due_done, "
        "SUM(status='scheduled' AND date(date) < date('now')) pending "
        f"FROM encounter WHERE competition_id IN ({cin}) AND date IS NOT NULL "
        "GROUP BY competition_id, wk ORDER BY wk"
    )
    rows = query(db, sql)
    by_cid: dict[str, dict[str, dict]] = {c: {} for c in comps}
    weeks: dict[str, str] = {}   # wk -> representative date (min)
    for r in rows:
        cid, wk = str(r["cid"]), str(r["wk"])
        by_cid.setdefault(cid, {})[wk] = r
        d = r.get("wk_date")
        if d and (wk not in weeks or d < weeks[wk]):
            weeks[wk] = d
    return {"by_cid": by_cid, "weeks": weeks}


def pending_detail(db: str, comps: list[str]) -> list[dict]:
    cin = ",".join(f"'{c}'" for c in comps)
    sql = (
        "SELECT e.competition_id cid, e.group_id grp, g.name gname, "
        "e.matchday_number j, e.date, e.team_local_name loc, e.team_visitor_name vis "
        "FROM encounter e LEFT JOIN comp_group g ON g.competition_id=e.competition_id "
        "AND g.id=e.group_id "
        f"WHERE e.competition_id IN ({cin}) AND e.status='scheduled' "
        "AND e.date IS NOT NULL AND date(e.date) < date('now') "
        "ORDER BY e.competition_id, e.date, e.group_id"
    )
    return query(db, sql)


def identity_counts(db: str) -> dict:
    rows = query(
        db,
        "SELECT (SELECT COUNT(*) FROM person) persons, "
        "(SELECT COUNT(*) FROM person_licence WHERE status='confirmed') confirmed, "
        "(SELECT COUNT(*) FROM person_licence WHERE status='probable') probable",
    )
    return rows[0] if rows else {}


# ── Lectura del resumen de la ejecución ──────────────────────────────────────

def read_steps(steps_tsv: Path) -> list[dict]:
    steps: list[dict] = []
    if not steps_tsv.exists():
        return steps
    for line in steps_tsv.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, code, secs, logpath = parts[0], parts[1], parts[2], parts[3]
        log = Path(logpath)
        tail, errors = "", []
        if log.exists():
            txt = log.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(txt[-40:])
            errors = [ln for ln in txt
                      if "❌" in ln or "error" in ln.lower() or "Traceback" in ln][:10]
        steps.append({
            "name": name, "code": int(code) if code.isdigit() else 1,
            "secs": secs, "tail": tail, "errors": errors,
        })
    return steps


# ── HTML ─────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#0d1117;--card:#161b22;--line:#30363d;--fg:#e6edf3;--mut:#8b949e;
--ok:#2ea043;--warn:#d29922;--bad:#da3633;--na:#484f58;--accent:#2f81f7}
@media(prefers-color-scheme:light){:root{--bg:#f6f8fa;--card:#fff;--line:#d0d7de;
--fg:#1f2328;--mut:#656d76;--na:#afb8c1}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:24px 16px}
.wrap{max-width:1200px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
h2{font-size:16px;margin:28px 0 12px;border-bottom:1px solid var(--line);padding-bottom:6px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .lab{color:var(--mut);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.card .big{font-size:30px;font-weight:800;margin:6px 0 2px;display:flex;align-items:center;gap:8px}
.card .meta{color:var(--mut);font-size:12px}
.dot{width:12px;height:12px;border-radius:50%;display:inline-block;flex:0 0 auto}
.ok{background:var(--ok)}.warn{background:var(--warn)}.bad{background:var(--bad)}.na{background:var(--na)}
.steps{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:8px}
.step{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px;font-size:13px}
.step b{font-weight:700}
.badge{font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;color:#fff}
.badge.ok{background:var(--ok)}.badge.bad{background:var(--bad)}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 9px;text-align:left;white-space:nowrap;border-bottom:1px solid var(--line)}
th{background:var(--card);color:var(--mut);font-weight:600;position:sticky;top:0}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.matrix td{text-align:center;font-variant-numeric:tabular-nums;padding:0}
.matrix .cell{display:block;padding:6px 4px;font-size:11px;font-weight:700;color:#fff;min-width:38px}
.cell.ok{background:var(--ok)}.cell.warn{background:var(--warn)}.cell.bad{background:var(--bad)}
.cell.na{background:transparent;color:var(--mut);font-weight:400}
.matrix th.lg{text-align:left;position:sticky;left:0;background:var(--card);z-index:1}
.matrix td.lg{text-align:left;font-weight:600;position:sticky;left:0;background:var(--card)}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:8px 0;padding:0 12px}
details summary{cursor:pointer;padding:10px 0;font-weight:600}
pre{overflow-x:auto;font-size:12px;color:var(--mut);white-space:pre-wrap}
.err{color:var(--bad);font-weight:600}
.empty{color:var(--mut);padding:12px}
"""


def render(db, season, comps, matrix, pending, steps, identity, out_dir, stamp) -> Path:
    by_cid, weeks = matrix["by_cid"], matrix["weeks"]
    week_keys = sorted(weeks.keys())
    label_by_cid = {f"{k}_{season_short(season)}": lab for k, lab in LEAGUES}
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    # Resumen por liga (totales sobre vencidos).
    summary = {}
    for cid in comps:
        due = sum(int(w["due"] or 0) for w in by_cid.get(cid, {}).values())
        done = sum(int(w["due_done"] or 0) for w in by_cid.get(cid, {}).values())
        pend = sum(int(w["pending"] or 0) for w in by_cid.get(cid, {}).values())
        pct = round(100 * done / due, 1) if due else None
        summary[cid] = {"due": due, "done": done, "pending": pend, "pct": pct}

    p: list[str] = []
    p.append(f"<!doctype html><html lang=es><head><meta charset=utf-8>")
    p.append("<meta name=viewport content='width=device-width,initial-scale=1'>")
    p.append(f"<title>Carga tt-data</title><style>{CSS}</style></head><body><div class=wrap>")
    p.append(f"<h1>Estado de carga · tt-data</h1>")
    p.append(f"<div class=sub>DB <b>{esc(db)}</b> · temporada <b>{esc(season)}</b> · {now}</div>")

    # Pasos de la ejecución.
    if steps:
        p.append("<div class=steps>")
        for s in steps:
            cls = "ok" if s["code"] == 0 else "bad"
            txt = "OK" if s["code"] == 0 else f"FALLO ({s['code']})"
            p.append(f"<div class=step><b>{esc(s['name'])}</b> "
                     f"<span class='badge {cls}'>{txt}</span> "
                     f"<span class=meta>· {esc(s['secs'])}s</span></div>")
        p.append("</div>")

    # Semáforos por liga.
    p.append("<h2>Grado de carga por liga</h2><div class=cards>")
    for cid in comps:
        s = summary[cid]
        cls = pct_class(s["pct"])
        pct_txt = f"{s['pct']:.0f}%" if s["pct"] is not None else "—"
        pend_txt = f" · {s['pending']} pendientes" if s["pending"] else ""
        p.append(f"<div class=card><div class=lab>{esc(label_by_cid.get(cid, cid))}</div>"
                 f"<div class=big><span class='dot {cls}'></span>{pct_txt}</div>"
                 f"<div class=meta>{s['done']}/{s['due']} vencidos cargados{pend_txt}</div></div>")
    # Identidad como tarjeta informativa.
    if identity:
        p.append(f"<div class=card><div class=lab>Identidad</div>"
                 f"<div class=big>{esc(identity.get('persons','—'))}</div>"
                 f"<div class=meta>{esc(identity.get('confirmed','?'))} confirmadas · "
                 f"{esc(identity.get('probable','?'))} probables</div></div>")
    p.append("</div>")

    # Matriz liga × semana.
    p.append("<h2>Carga por semana (vencidos cargados)</h2>")
    if week_keys:
        p.append("<div class=tablewrap><table class=matrix><thead><tr><th class=lg>Liga</th>")
        for wk in week_keys:
            p.append(f"<th title='{esc(wk)}'>{short_date(weeks[wk])}</th>")
        p.append("</tr></thead><tbody>")
        for cid in comps:
            p.append(f"<tr><td class=lg>{esc(label_by_cid.get(cid, cid))}</td>")
            for wk in week_keys:
                w = by_cid.get(cid, {}).get(wk)
                if not w or int(w["due"] or 0) == 0:
                    p.append("<td><span class='cell na'>·</span></td>")
                    continue
                due, done = int(w["due"]), int(w["due_done"] or 0)
                pc = 100 * done / due
                cls = pct_class(pc)
                p.append(f"<td><span class='cell {cls}' title='{esc(wk)}: {done}/{due}'>"
                         f"{done}/{due}</span></td>")
            p.append("</tr>")
        p.append("</tbody></table></div>")
    else:
        p.append("<div class=empty>Sin encuentros con fecha cargados.</div>")

    # Pendientes (detalle).
    p.append(f"<h2>Encuentros pendientes de cargar ({len(pending)})</h2>")
    if pending:
        p.append("<div class=tablewrap><table><thead><tr><th>Liga</th><th>Grupo</th>"
                 "<th>J</th><th>Fecha</th><th>Local</th><th>Visitante</th></tr></thead><tbody>")
        for r in pending:
            lk = str(r["cid"]).rsplit("_", 1)[0]
            lab = dict(LEAGUES).get(lk, lk)
            p.append(f"<tr><td>{esc(lab)}</td><td>{esc(r.get('gname') or r.get('grp'))}</td>"
                     f"<td class=num>{esc(r.get('j'))}</td><td>{short_date(r.get('date'))}</td>"
                     f"<td>{esc(r.get('loc'))}</td><td>{esc(r.get('vis'))}</td></tr>")
        p.append("</tbody></table></div>")
    else:
        p.append("<div class=empty>✅ Ninguno: no hay encuentros vencidos sin resultado.</div>")

    # Logs de cada paso.
    if steps:
        p.append("<h2>Detalle de la ejecución</h2>")
        for s in steps:
            mark = "" if s["code"] == 0 else " ⚠️"
            p.append(f"<details><summary>{esc(s['name'])}{mark}</summary>")
            if s["errors"]:
                p.append("<div class=err>" + "<br>".join(esc(e) for e in s["errors"]) + "</div>")
            p.append(f"<pre>{esc(s['tail'])}</pre></details>")

    p.append("</div></body></html>")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"report_{stamp}.html"
    out_path.write_text("".join(p), encoding="utf-8")
    (out_dir / "latest.html").write_text("".join(p), encoding="utf-8")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("steps", type=Path, nargs="?", help="steps.tsv de la ejecución")
    ap.add_argument("--db", default="tt-data")
    ap.add_argument("--season", default="2026-2027")
    ap.add_argument("--out", type=Path, default=Path("output/reports"))
    args = ap.parse_args()

    comps = [f"{k}_{season_short(args.season)}" for k, _ in LEAGUES]
    print("⬇️  Consultando estado de carga…")
    matrix = load_matrix(args.db, comps)
    pending = pending_detail(args.db, comps)
    identity = identity_counts(args.db)
    steps = read_steps(args.steps) if args.steps else []

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = render(args.db, args.season, comps, matrix, pending, steps,
                      identity, args.out, stamp)
    print(f"📄 Report: {out_path}")
    print(f"   (y {args.out / 'latest.html'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
