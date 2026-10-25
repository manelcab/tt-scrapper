#!/usr/bin/env python
"""Detecta equipos HUÉRFANOS en tt-data: los que siguen en la base de datos pero
YA NO están en la clasificación en vivo de fctt.cat (equipos retirados a mitad de
temporada, o grupos que la federación ha recompuesto). Nuestros upserts nunca
borran, así que esos equipos y sus encuentros quedan como fantasmas —los verás en
el validador como "pendientes" que no cuadran (p. ej. un equipo que en realidad
descansa emparejado con otro que no está en el grupo).

Compara, grupo a grupo, el roster vivo (parse_standings sobre la página de grupo)
contra los equipos de la BD. Ambos usan el mismo id `{group_id}_{slug(nombre)}`,
así que un equipo en la BD cuyo slug no está en la clasificación viva es huérfano.

Con --fix genera un SQL de borrado DIRIGIDO en output/sql/ para que lo revises y lo
cargues tú (no borra nada solo). Es una auditoría bajo demanda (hace una petición
por grupo), no forma parte de la carga semanal.

Uso:
  venv/bin/python scripts/detect_orphans.py [--rep all|estatals|rtb|rtg|rtt] [--fix]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# El script vive en scripts/; añade la raíz de tt-scraper al path para importar el proyecto.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parsers.fctt_html_parser import parse_standings
from scrapers.fctt_scraper import FcttScraper
from sources import (
    FCTT,
    FCTT_TERRITORIALS,
    SEASON,
    SEASON_SHORT,
    territorial_group_url,
)
from utils.http_client import HttpClient

API_DIR = Path(__file__).resolve().parents[2] / "apps" / "api"
DB = "tt-data"


def d1(sql: str) -> list[dict]:
    """Lanza una consulta a la D1 vía wrangler y devuelve las filas."""
    r = subprocess.run(
        ["node_modules/.bin/wrangler", "d1", "execute", DB, "--remote", "--json",
         "--command", sql],
        cwd=API_DIR, capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(r.stderr[:600], file=sys.stderr)
        sys.exit(1)
    return json.loads(r.stdout)[0]["results"]


def sqlstr(s: str) -> str:
    return "'" + (s or "").replace("'", "''") + "'"


def build_targets(rep: str, scraper: FcttScraper) -> list[tuple[str, str, str]]:
    """(group_url, group_id, competition_id) por grupo, según la selección."""
    targets: list[tuple[str, str, str]] = []
    if rep in ("all", "estatals"):
        for cat in scraper.get_index():
            for g in cat.groups:
                targets.append((g.source_url, g.id, FCTT.id))
    for key in ("rtb", "rtg", "rtt"):
        if rep in ("all", key):
            terr = FCTT_TERRITORIALS[key]
            for slug in terr.slugs:
                targets.append((territorial_group_url(slug), slug, terr.id))
    return targets


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Detecta equipos huérfanos (en tt-data pero no en fctt.cat en vivo).")
    ap.add_argument("--rep", default="all",
                    choices=["all", "estatals", "rtb", "rtg", "rtt"],
                    help="Liga a auditar (por defecto todas).")
    ap.add_argument("--fix", action="store_true",
                    help="Genera el SQL de borrado dirigido en output/sql/ (no lo ejecuta).")
    args = ap.parse_args()

    comps = [FCTT.id] + [FCTT_TERRITORIALS[k].id for k in ("rtb", "rtg", "rtt")]
    in_comps = ",".join(sqlstr(c) for c in comps)
    db_rows = d1(f"SELECT competition_id AS c, group_id AS g, id, name FROM team "
                 f"WHERE competition_id IN ({in_comps})")
    db_by: dict[tuple[str, str], dict[str, str]] = {}
    for r in db_rows:
        db_by.setdefault((r["c"], r["g"]), {})[r["id"]] = r["name"]

    print(f"Auditoría de huérfanos · temporada {SEASON} · rep={args.rep}\n")

    orphans: list[tuple[str, str, str, str]] = []  # (comp, group, slug, name)
    skipped = 0
    with HttpClient(rate_limit_seconds=2.5) as http:
        scraper = FcttScraper(http)
        targets = build_targets(args.rep, scraper)
        print(f"Grupos a comprobar: {len(targets)}\n")
        for url, gid, comp in targets:
            html = scraper._fetch_valid(url)
            live = {t.id for t in parse_standings(html, gid)} if html else set()
            if not live:
                # Sin página o sin clasificación: NO se marca nada (evita falsos
                # positivos por un fetch fallido que borraría todo el grupo).
                print(f"  ⚠️  {comp}/{gid}: sin clasificación en vivo, saltado")
                skipped += 1
                continue
            for slug, name in db_by.get((comp, gid), {}).items():
                if slug not in live:
                    orphans.append((comp, gid, slug, name))

    print()
    if not orphans:
        print(f"✅ Sin huérfanos ({len(targets) - skipped} grupos comprobados): "
              "todos los equipos de la BD siguen en la clasificación en vivo.")
        return

    print(f"⚠️  {len(orphans)} equipo(s) huérfano(s) — en la BD pero NO en fctt.cat:")
    stmts: list[str] = []
    for comp, gid, slug, name in orphans:
        cnt = d1(f"SELECT COUNT(*) AS n FROM encounter WHERE competition_id={sqlstr(comp)} "
                 f"AND group_id={sqlstr(gid)} AND (team_local_name={sqlstr(name)} "
                 f"OR team_visitor_name={sqlstr(name)})")[0]["n"]
        print(f"  · {comp} / {gid}: {name}  ({cnt} encuentros fantasma)")
        stmts.append(
            f"DELETE FROM encounter WHERE competition_id={sqlstr(comp)} "
            f"AND group_id={sqlstr(gid)} AND (team_local_name={sqlstr(name)} "
            f"OR team_visitor_name={sqlstr(name)});")
        stmts.append(
            f"DELETE FROM team WHERE competition_id={sqlstr(comp)} "
            f"AND group_id={sqlstr(gid)} AND id={sqlstr(slug)};")

    if args.fix:
        out = Path("output/sql") / f"orphans_cleanup_{SEASON_SHORT}.sql"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(stmts) + "\n")
        print(f"\n📝 SQL de limpieza escrito en {out} ({len(stmts)} sentencias).")
        print(f"   Revísalo y, si está bien, cárgalo con:")
        print(f"     venv/bin/python d1/load_chunks.py {out} --db {DB}")
    else:
        print("\n(usa --fix para generar el SQL de borrado dirigido en output/sql/)")


if __name__ == "__main__":
    main()
