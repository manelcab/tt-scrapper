#!/usr/bin/env python3
"""Deriva el diccionario de club FCTT-código → RFETM-nombre a partir de los pares
CONFIRMADOS de `player_link`, y mide cuánta cobertura da (paso 7 del
design/plan_identidad_persona.md).

Por qué: en la FCTT `player.club` es un CÓDIGO (`CTTBDN`) y en la RFETM un NOMBRE
(`CTT BADALONA`), así que "mismo club" casi nunca casa y el recall de
"nombre+club" se queda en ~20%. Pero cada par confirmado por partidos de liga
nacional (misma persona) nos da el código FCTT y el nombre RFETM de esa persona:
votando sobre los 312 pares sale un diccionario código→nombre que luego resuelve
el club de CUALQUIER jugador FCTT de ese club, no solo de los confirmados.

Por defecto solo INFORMA (cobertura). Con --emit escribe un .sql para una tabla
`club_link` (derivada; crear antes la tabla, ver nota al final).

Uso (desde tt-scraper/):
    NODE_OPTIONS=--dns-result-order=ipv4first \\
        python d1/build_club_link.py --db tt-data [--min-votes 1] [--emit]
"""
from __future__ import annotations
import os

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# wrangler vive en apps/api (monorepo) por defecto; en otro despliegue (VPS) se
# apunta con TTS_WRANGLER_CWD (dir con wrangler.toml + node_modules) y, si hace
# falta, TTS_WRANGLER (ruta al binario).
WRANGLER_CWD = Path(os.environ.get("TTS_WRANGLER_CWD") or (REPO / "apps" / "api"))
WRANGLER = Path(os.environ.get("TTS_WRANGLER") or (WRANGLER_CWD / "node_modules" / ".bin" / "wrangler"))
SQL_OUT = Path(__file__).resolve().parents[1] / "output" / "sql"

LINKS_SQL = "SELECT federation_a, lic_a, federation_b, lic_b FROM player_link"
PLAYERS_SQL = "SELECT federation, lic, club FROM player"


def query(db: str, sql: str) -> list[dict]:
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


def esc(v) -> str:
    return str(v).replace("\n", " ").replace("\r", " ").replace("'", "''")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="tt-data")
    ap.add_argument("--min-votes", type=int, default=1,
                    help="votos mínimos para fijar código→nombre")
    ap.add_argument("--emit", action="store_true", help="escribe .sql de club_link")
    args = ap.parse_args()

    print("⬇️  Leyendo player_link y player…")
    links = query(args.db, LINKS_SQL)
    players = query(args.db, PLAYERS_SQL)

    club_of: dict[tuple[str, str], str] = {}
    for p in players:
        c = (p.get("club") or "").strip()
        if c:
            club_of[(str(p["federation"]), str(p["lic"]))] = c

    # Votos código-FCTT → nombre-RFETM desde los pares confirmados.
    votes: dict[str, Counter] = defaultdict(Counter)
    for ln in links:
        fa, la = str(ln["federation_a"]), str(ln["lic_a"])
        fb, lb = str(ln["federation_b"]), str(ln["lic_b"])
        f = (fa, la) if fa == "FCTT" else (fb, lb)
        r = (fb, lb) if fb == "RFETM" else (fa, la)
        fc, rc = club_of.get(f), club_of.get(r)
        if fc and rc:
            votes[fc][rc] += 1

    mapping: dict[str, tuple[str, int]] = {}
    ambiguous = 0
    for code, ctr in votes.items():
        name, v = ctr.most_common(1)[0]
        if v < args.min_votes:
            continue
        if len(ctr) > 1 and ctr.most_common(2)[1][1] == v:
            ambiguous += 1  # empate entre dos nombres: no fijamos
            continue
        mapping[code] = (name, v)

    # Cobertura: jugadores FCTT cuyo código de club queda resuelto.
    fctt_players = [(lic, c) for (fed, lic), c in club_of.items() if fed == "FCTT"]
    distinct_codes = {c for _, c in fctt_players}
    covered_players = sum(1 for _, c in fctt_players if c in mapping)
    covered_codes = sum(1 for c in distinct_codes if c in mapping)

    print(f"\n📊 Pares confirmados con club en ambos lados: "
          f"{sum(v for ctr in votes.values() for v in ctr.values())}")
    print(f"   códigos FCTT con nombre RFETM derivado: {len(mapping)}"
          f"  (ambiguos no fijados: {ambiguous})")
    print("\n── Cobertura sobre los jugadores FCTT ──")
    print(f"   códigos de club FCTT distintos:   {len(distinct_codes)}")
    print(f"   resueltos por el diccionario:     {covered_codes} "
          f"({100*covered_codes/max(1,len(distinct_codes)):.1f}%)")
    print(f"   jugadores FCTT con club resuelto: {covered_players}/{len(fctt_players)} "
          f"({100*covered_players/max(1,len(fctt_players)):.1f}%)")
    print("\n   ejemplos código→nombre:")
    for code, (name, v) in sorted(mapping.items(), key=lambda x: -x[1][1])[:12]:
        print(f"      {code:10} → {name}   (votos {v})")

    if args.emit:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        now_iso = dt.datetime.now().isoformat(timespec="seconds")
        out_path = SQL_OUT / f"club_link_{stamp}.sql"
        SQL_OUT.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            for code, (name, v) in sorted(mapping.items()):
                fh.write(
                    "INSERT INTO club_link (fctt_code, rfetm_name, confidence, derived_at) "
                    f"VALUES ('{esc(code)}', '{esc(name)}', {v}, '{now_iso}') "
                    "ON CONFLICT(fctt_code) DO UPDATE SET rfetm_name=excluded.rfetm_name, "
                    "confidence=excluded.confidence, derived_at=excluded.derived_at;\n"
                )
        print(f"\n💾 {out_path.name}: {len(mapping)} equivalencias. "
              "Requiere la tabla club_link (ver nota).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
