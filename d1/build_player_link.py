#!/usr/bin/env python3
"""Deduce el puente FCTT↔RFETM (`player_link`) a partir de los partidos de LIGA
NACIONAL, NUNCA por nombre (plan_migracion_d1.md §6).

Idea: un mismo partido de liga nacional vive en los dos espacios de licencia —
en `individual_match` de `rfetm_2526` (licencia española) y en `player_match` de
la FCTT (evento `LLNN*`, código catalán). Cada partido, visto desde un jugador,
tiene una firma `(fecha, sets_ganados, sets_del_rival)`. Si un código FCTT y una
licencia RFETM son la misma persona, comparten TODAS esas firmas.

Algoritmo (votación con emparejamiento mutuo):
  1. Firmas FCTT: `(lic, d, s, o)` de `player_match` LLNN.
  2. Firmas RFETM: `(lic, d, s, o)` de `individual_match` (una por jugador y lado),
     con el tally de sets calculado desde el JSON `sets`.
  3. `votos[f_lic][r_lic]` = nº de firmas compartidas.
  4. Se acepta el par (f,r) si: r es el más votado de f, f es el más votado de r
     (MUTUO), votos ≥ --min-conf y (votos − segundo) ≥ --margin. El mutuo descarta
     compañeros de equipo (comparten fechas pero cada uno vota a SU propio código).
  5. Emite `player_link` idempotente (FCTT=A, RFETM=B), confidence = votos.

Uso (desde tt-scraper/):
    NODE_OPTIONS=--dns-result-order=ipv4first \\
        python d1/build_player_link.py --db tt-data [--min-conf 3] [--margin 2] \\
        [--dry-run] [--validate]
Luego cargar el .sql con d1/load_chunks.py.
"""
from __future__ import annotations
import os

import argparse
import datetime as dt
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# wrangler vive en apps/api (monorepo) por defecto; en otro despliegue (VPS) se
# apunta con TTS_WRANGLER_CWD (dir con wrangler.toml + node_modules) y, si hace
# falta, TTS_WRANGLER (ruta al binario).
WRANGLER_CWD = Path(os.environ.get("TTS_WRANGLER_CWD") or (REPO / "apps" / "api"))
WRANGLER = Path(os.environ.get("TTS_WRANGLER") or (WRANGLER_CWD / "node_modules" / ".bin" / "wrangler"))
SQL_OUT = Path(__file__).resolve().parents[1] / "output" / "sql"

FCTT_SQL = (
    "SELECT lic, substr(date,1,10) AS d, sets AS s, opp_sets AS o "
    "FROM player_match WHERE federation='FCTT' AND event LIKE 'LLNN%'"
)

# Una fila por jugador y lado, con el tally de sets desde el JSON.
RFETM_SQL = (
    "WITH t AS (SELECT im.competition_id c, im.encounter_id e, im.ord od, "
    "im.player_local_lic ll, im.player_visitor_lic vl, substr(en.date,1,10) d, "
    "SUM(CASE WHEN json_extract(j.value,'$.local')>json_extract(j.value,'$.visitor') THEN 1 ELSE 0 END) ls, "
    "SUM(CASE WHEN json_extract(j.value,'$.visitor')>json_extract(j.value,'$.local') THEN 1 ELSE 0 END) vs "
    "FROM individual_match im JOIN encounter en ON en.competition_id=im.competition_id AND en.id=im.encounter_id, "
    "json_each(im.sets) j WHERE im.competition_id LIKE 'rfetm%' AND COALESCE(im.is_doubles,0)=0 "
    "GROUP BY im.competition_id, im.encounter_id, im.ord) "
    "SELECT d, ll AS lic, ls AS s, vs AS o FROM t WHERE ll IS NOT NULL "
    "UNION ALL SELECT d, vl AS lic, vs AS s, ls AS o FROM t WHERE vl IS NOT NULL"
)

# Nombres solo para --validate (NUNCA para deducir).
NAMES_SQL = "SELECT federation, lic, name FROM player"


def query(db: str, sql: str) -> list[dict]:
    """Ejecuta un SELECT con wrangler --json y devuelve results[]."""
    proc = subprocess.run(
        [str(WRANGLER), "d1", "execute", db, "--remote", "--json", "--command", sql],
        cwd=str(WRANGLER_CWD), capture_output=True, text=True,
    )
    raw = proc.stdout.strip() or proc.stderr.strip()
    starts = [p for p in (raw.find("["), raw.find("{")) if p != -1]
    if not starts:
        sys.exit(f"❌ wrangler no devolvió JSON: {raw[:400]}")
    data = json.loads(raw[min(starts):])
    if isinstance(data, dict) and data.get("error"):
        sys.exit(f"❌ D1 error: {json.dumps(data['error'])[:400]}")
    results = data if isinstance(data, list) else [data]
    out: list[dict] = []
    for r in results:
        out.extend((r or {}).get("results") or [])
    return out


def esc(v: str) -> str:
    return str(v).replace("'", "''")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="tt-data")
    ap.add_argument("--min-conf", type=int, default=3, help="votos mínimos para aceptar")
    ap.add_argument("--margin", type=int, default=2, help="votos por encima del 2º candidato")
    ap.add_argument("--dry-run", action="store_true", help="no escribe .sql, solo estadísticas")
    ap.add_argument("--validate", action="store_true", help="contrasta nombres (solo informe)")
    args = ap.parse_args()

    print("⬇️  Leyendo firmas FCTT (LLNN) y RFETM (liga nacional)…")
    fctt = query(args.db, FCTT_SQL)
    rf = query(args.db, RFETM_SQL)
    print(f"   FCTT LLNN: {len(fctt)} filas · RFETM perspectivas: {len(rf)} filas")

    # Índice de firmas RFETM → licencias.
    rf_by_sig: dict[tuple, list[str]] = defaultdict(list)
    for row in rf:
        rf_by_sig[(row["d"], row["s"], row["o"])].append(str(row["lic"]))

    votes: dict[str, Counter] = defaultdict(Counter)
    for row in fctt:
        sig = (row["d"], row["s"], row["o"])
        f_lic = str(row["lic"])
        for r_lic in rf_by_sig.get(sig, ()):  # noqa: SIM118
            votes[f_lic][r_lic] += 1

    # Voto inverso (mismos conteos) para el emparejamiento mutuo.
    votes_rev: dict[str, Counter] = defaultdict(Counter)
    for f_lic, ctr in votes.items():
        for r_lic, v in ctr.items():
            votes_rev[r_lic][f_lic] = v
    best_f_of_r = {r: ctr.most_common(1)[0][0] for r, ctr in votes_rev.items()}

    accepted: list[tuple[str, str, int]] = []
    rej_mutual = rej_conf = rej_margin = 0
    for f_lic, ctr in votes.items():
        ranked = ctr.most_common(2)
        r_lic, v = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0
        if best_f_of_r.get(r_lic) != f_lic:
            rej_mutual += 1
            continue
        if v < args.min_conf:
            rej_conf += 1
            continue
        if v - second < args.margin:
            rej_margin += 1
            continue
        accepted.append((f_lic, r_lic, v))

    print(
        f"\n📊 Candidatos FCTT con voto: {len(votes)} · aceptados: {len(accepted)}"
        f"  (rechazos → no-mutuo {rej_mutual}, poca conf {rej_conf}, poco margen {rej_margin})"
    )
    if accepted:
        confs = sorted(v for _, _, v in accepted)
        print(f"   confianza: min {confs[0]} · mediana {confs[len(confs)//2]} · max {confs[-1]}")

    if args.validate:
        names = {(r["federation"], str(r["lic"])): r["name"] for r in query(args.db, NAMES_SQL)}
        ok = mismatch = unknown = 0
        samples = []
        for f_lic, r_lic, v in accepted:
            nf = names.get(("FCTT", f_lic))
            nr = names.get(("RFETM", r_lic))
            if not nf or not nr:
                unknown += 1
                continue
            # Comparación laxa: apellido (primer token en mayúsculas) coincide.
            kf = nf.split()[0].upper()
            kr = nr.split()[0].upper()
            if kf == kr:
                ok += 1
            else:
                mismatch += 1
                if len(samples) < 15:
                    samples.append(f"      ⚠️  FCTT {f_lic} «{nf}»  ≠  RFETM {r_lic} «{nr}»  (conf {v})")
        print(f"\n🔎 Validación por nombre (informativa): ok {ok} · discrepan {mismatch} · sin nombre {unknown}")
        for s in samples:
            print(s)

    if args.dry_run:
        print("\n(dry-run: no se escribe .sql)")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    out_path = SQL_OUT / f"player_link_{stamp}.sql"
    SQL_OUT.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for f_lic, r_lic, v in sorted(accepted):
            fh.write(
                "INSERT INTO player_link (federation_a, lic_a, federation_b, lic_b, "
                "method, confidence, verified_at) VALUES "
                f"('FCTT', '{esc(f_lic)}', 'RFETM', '{esc(r_lic)}', "
                f"'nat_league_match', {float(v)}, '{now_iso}') "
                "ON CONFLICT(federation_a, lic_a, federation_b, lic_b) DO UPDATE SET "
                "method=excluded.method, confidence=excluded.confidence, "
                "verified_at=excluded.verified_at;\n"
            )
    print(f"\n💾 {out_path.name}: {len(accepted)} enlaces. Cárgalo con d1/load_chunks.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
