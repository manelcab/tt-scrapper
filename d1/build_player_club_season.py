#!/usr/bin/env python3
"""Deriva el club por temporada de cada licencia a partir de las ALINEACIONES
(`individual_match` → `encounter`: el equipo con el que juega cada jugador), no
del ranking —que trae el club del final de la temporada anterior
(design/plan_identidad_persona.md, paso 8; design/PARA-KAYZEN-licencias.md).

Para cada partido individual, el jugador local juega con el equipo local del
encuentro y el visitante con el visitante. El club es el nombre del equipo
normalizado (sin la letra/número de equipo: «... "A"», «... B»). Por
(federación, licencia, temporada) se toma el club MAYORITARIO (más partidos) y
se cuenta cuántos clubes distintos aparecen (clubs>1 = préstamo o dato raro).

La federación y la temporada salen de competition (competition_id →
federation, season).

Uso (desde tt-scraper/):
    NODE_OPTIONS=--dns-result-order=ipv4first \\
        python d1/build_player_club_season.py --db tt-data [--dry-run]
Luego cargar el .sql con d1/load_chunks.py.
"""
from __future__ import annotations
import os

import argparse
import datetime as dt
import json
import re
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

COMP_SQL = "SELECT id, federation, season FROM competition"
ENC_SQL = ("SELECT competition_id, id, team_local_name, team_visitor_name "
           "FROM encounter")
IM_SQL = (
    "SELECT competition_id, encounter_id, player_local_lic, player_visitor_lic, "
    "is_doubles, player_local_lic_2, player_visitor_lic_2 FROM individual_match"
)


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


# Letra/número de equipo al final del nombre: «... "A"», «... 'A'», «... B», «... 2».
_TEAM_SUFFIX = re.compile(r"""\s+['"]?[A-Za-z0-9]['"]?$""")


def norm_club(name: str) -> str:
    """Nombre de equipo → club: quita la letra/número de equipo del final."""
    s = (name or "").strip()
    prev = None
    while s and s != prev:          # por si hay doble sufijo raro
        prev = s
        s = _TEAM_SUFFIX.sub("", s).strip()
    return s or (name or "").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="tt-data")
    ap.add_argument("--dry-run", action="store_true", help="no escribe .sql, solo estadísticas")
    args = ap.parse_args()

    print("⬇️  Leyendo competition, encounter e individual_match…")
    comps = {str(c["id"]): (str(c["federation"]), str(c.get("season") or ""))
             for c in query(args.db, COMP_SQL)}
    enc = {(str(e["competition_id"]), str(e["id"])):
           (e.get("team_local_name") or "", e.get("team_visitor_name") or "")
           for e in query(args.db, ENC_SQL)}
    ims = query(args.db, IM_SQL)
    print(f"   competitions: {len(comps)} · encounters: {len(enc)} · "
          f"individual_match: {len(ims)}")

    # (federation, lic, season) → Counter de club.
    tally: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    for im in ims:
        comp = str(im["competition_id"])
        fed_season = comps.get(comp)
        teams = enc.get((comp, str(im["encounter_id"])))
        if not fed_season or not teams:
            continue
        fed, season = fed_season
        local_club, visitor_club = norm_club(teams[0]), norm_club(teams[1])
        doubles = str(im.get("is_doubles") or "0") in ("1", "True", "true")
        local = [im.get("player_local_lic")]
        visitor = [im.get("player_visitor_lic")]
        if doubles:
            local.append(im.get("player_local_lic_2"))
            visitor.append(im.get("player_visitor_lic_2"))
        for lic in local:
            if lic and local_club:
                tally[(fed, str(lic), season)][local_club] += 1
        for lic in visitor:
            if lic and visitor_club:
                tally[(fed, str(lic), season)][visitor_club] += 1

    rows: list[tuple] = []   # (fed, lic, season, club, matches, clubs)
    multi = 0
    for (fed, lic, season), ctr in tally.items():
        club, matches = ctr.most_common(1)[0]
        rows.append((fed, lic, season, club, matches, len(ctr)))
        if len(ctr) > 1:
            multi += 1

    print(f"\n📊 Filas (licencia×temporada): {len(rows)} · con >1 club esa "
          f"temporada: {multi}")
    by_fed: Counter = Counter(r[0] for r in rows)
    print(f"   por federación: {dict(by_fed)}")

    if args.dry_run:
        print("\n(dry-run: no se escribe .sql)")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    out_path = SQL_OUT / f"player_club_season_{stamp}.sql"
    SQL_OUT.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for fed, lic, season, club, matches, clubs in rows:
            fh.write(
                "INSERT INTO player_club_season (federation, lic, season, club, "
                "matches, clubs, updated_at) VALUES "
                f"('{esc(fed)}', '{esc(lic)}', '{esc(season)}', '{esc(club)}', "
                f"{matches}, {clubs}, '{now_iso}') "
                "ON CONFLICT(federation, lic, season) DO UPDATE SET club=excluded.club, "
                "matches=excluded.matches, clubs=excluded.clubs, updated_at=excluded.updated_at;\n"
            )
    print(f"\n💾 {out_path.name}: {len(rows)} filas. Cárgalo con d1/load_chunks.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
