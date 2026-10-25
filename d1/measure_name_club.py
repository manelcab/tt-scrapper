#!/usr/bin/env python3
"""Mide "mismo nombre y mismo club" contra `player_link`, los dos números que
pide Kayzen (PARA-TT-SCRAPER-identidad.md §5; design/plan_identidad_persona.md
Fase 2). Solo INFORMA: no escribe nada.

- RECALL: de los pares que `player_link` da por ciertos, ¿en cuántos coinciden
  además el nombre exacto y el club equivalente? (¿cuánto capturaría la regla?)
- PRECISIÓN: de los pares que "mismo nombre y mismo club" propondría, ¿cuántos
  CONTRADICEN a `player_link` (dicen A–B donde player_link dice A–C)?

Lógica de club portada de services/api/src/mismo_club.ts: se quitan palabras que
no distinguen (CTT, CLUB, TENNIS, TAULA, …) y la letra del equipo, y se exige que
un conjunto de tokens sea subconjunto del otro. Grieta conocida: dos clubes de la
misma ciudad con una palabra común pasan la regla.

Uso (desde tt-scraper/):
    NODE_OPTIONS=--dns-result-order=ipv4first \\
        python d1/measure_name_club.py --db tt-data [--samples 20]
"""
from __future__ import annotations
import os

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# wrangler vive en apps/api (monorepo) por defecto; en otro despliegue (VPS) se
# apunta con TTS_WRANGLER_CWD (dir con wrangler.toml + node_modules) y, si hace
# falta, TTS_WRANGLER (ruta al binario).
WRANGLER_CWD = Path(os.environ.get("TTS_WRANGLER_CWD") or (REPO / "apps" / "api"))
WRANGLER = Path(os.environ.get("TTS_WRANGLER") or (WRANGLER_CWD / "node_modules" / ".bin" / "wrangler"))

LINKS_SQL = (
    "SELECT federation_a, lic_a, federation_b, lic_b FROM player_link"
)
PLAYERS_SQL = "SELECT federation, lic, name, club FROM player"
CLUB_LINK_SQL = "SELECT fctt_code, rfetm_name FROM club_link"

# Palabras que no distinguen un club (mismo_club.ts). Se quitan antes de comparar.
_CLUB_STOP = {
    "CTT", "CT", "CLUB", "TENNIS", "TENIS", "TAULA", "TM", "DE", "DEL", "LA",
    "EL", "LES", "ELS", "SECCIO", "SECCION", "SEC", "AE", "CE", "AD", "CD",
}


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


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def norm_name(s: str) -> str:
    """Nombre canónico: sin tildes, mayúsculas, espacios colapsados."""
    return re.sub(r"\s+", " ", _strip_accents(s or "").upper()).strip()


def club_tokens(s: str) -> frozenset[str]:
    """Tokens significativos del club: sin tildes, sin palabras genéricas ni
    letras sueltas (la letra del equipo)."""
    words = re.split(r"[^A-Z0-9]+", _strip_accents(s or "").upper())
    return frozenset(
        w for w in words if w and len(w) > 1 and w not in _CLUB_STOP
    )


def club_equiv(a: str, b: str) -> bool:
    """Un conjunto de tokens es subconjunto del otro (ambos no vacíos)."""
    ta, tb = club_tokens(a), club_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


def club_fragile(a: str, b: str) -> bool:
    """Match de club FRÁGIL: pasa por subconjunto pero uno de los conjuntos tiene
    una sola palabra significativa (p. ej. el pueblo) y no son iguales. Es la
    grieta de Kayzen: dos clubes distintos de la misma ciudad."""
    ta, tb = club_tokens(a), club_tokens(b)
    if not ta or not tb or ta == tb:
        return False
    return (ta <= tb or tb <= ta) and min(len(ta), len(tb)) == 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="tt-data")
    ap.add_argument("--samples", type=int, default=15, help="ejemplos a mostrar")
    ap.add_argument("--use-club-map", action="store_true",
                    help="resuelve el código de club FCTT con la tabla club_link "
                         "antes de comparar (mide el recall con el paso 7)")
    args = ap.parse_args()

    print("⬇️  Leyendo player_link y player…")
    links_raw = query(args.db, LINKS_SQL)
    players = query(args.db, PLAYERS_SQL)

    # Mapa club FCTT-código → RFETM-nombre (paso 7). Resuelve el código antes de
    # comparar: así "mismo club" deja de ser código-vs-nombre.
    club_map: dict[str, str] = {}
    if args.use_club_map:
        for r in query(args.db, CLUB_LINK_SQL):
            club_map[str(r["fctt_code"])] = str(r["rfetm_name"])
        print(f"   club_link: {len(club_map)} equivalencias (resolviendo código FCTT)")

    def same_club(fctt_club: str, rfetm_club: str) -> bool:
        resolved = club_map.get((fctt_club or "").strip(), fctt_club)
        return club_equiv(resolved, rfetm_club)

    # Ficha por licencia y federación.
    fichas: dict[tuple[str, str], dict] = {}
    fctt: dict[str, dict] = {}
    rfetm: dict[str, dict] = {}
    for p in players:
        fed, lic = str(p["federation"]), str(p["lic"])
        row = {"name": p.get("name") or "", "club": p.get("club") or ""}
        fichas[(fed, lic)] = row
        (fctt if fed == "FCTT" else rfetm if fed == "RFETM" else {})[lic] = row

    # Pares de player_link normalizados a (fctt_lic, rfetm_lic).
    links: set[tuple[str, str]] = set()
    for ln in links_raw:
        fa, la = str(ln["federation_a"]), str(ln["lic_a"])
        fb, lb = str(ln["federation_b"]), str(ln["lic_b"])
        if fa == "FCTT" and fb == "RFETM":
            links.add((la, lb))
        elif fa == "RFETM" and fb == "FCTT":
            links.add((lb, la))
    f_to_r: dict[str, set[str]] = defaultdict(set)
    r_to_f: dict[str, set[str]] = defaultdict(set)
    for f, r in links:
        f_to_r[f].add(r)
        r_to_f[r].add(f)
    print(f"   player_link FCTT↔RFETM: {len(links)} · FCTT players: {len(fctt)} · "
          f"RFETM players: {len(rfetm)}")

    # ── RECALL ────────────────────────────────────────────────────────────
    hit_name = hit_name_club = 0
    recall_miss_samples = []
    for f, r in links:
        nf, nr = fichas.get(("FCTT", f)), fichas.get(("RFETM", r))
        if not nf or not nr:
            continue
        same_name = norm_name(nf["name"]) == norm_name(nr["name"]) and nf["name"]
        if same_name:
            hit_name += 1
            if same_club(nf["club"], nr["club"]):
                hit_name_club += 1
            elif len(recall_miss_samples) < args.samples:
                recall_miss_samples.append(
                    f"      · nombre OK, club NO: «{nf['name']}» "
                    f"FCTT[{nf['club']}] vs RFETM[{nr['club']}]")
    total_links = sum(
        1 for f, r in links
        if fichas.get(("FCTT", f)) and fichas.get(("RFETM", r)))
    print("\n── RECALL (de los pares ciertos de player_link) ──")
    if total_links:
        print(f"   con nombre exacto igual:        {hit_name}/{total_links} "
              f"({100*hit_name/total_links:.1f}%)")
        print(f"   con nombre exacto Y club equiv: {hit_name_club}/{total_links} "
              f"({100*hit_name_club/total_links:.1f}%)  ← recall de la regla")
    for s in recall_miss_samples:
        print(s)

    # ── PRECISIÓN ───────────────────────────────────────────────────────────
    # Candidatos: nombre exacto igual entre FCTT y RFETM + club equivalente.
    rfetm_by_name: dict[str, list[str]] = defaultdict(list)
    for lic, row in rfetm.items():
        if row["name"]:
            rfetm_by_name[norm_name(row["name"])].append(lic)

    agree = contradict = unknown = 0
    fragile = 0
    contradict_samples = []
    fragile_samples = []
    for flic, frow in fctt.items():
        if not frow["name"]:
            continue
        for rlic in rfetm_by_name.get(norm_name(frow["name"]), ()):  # noqa: SIM118
            rrow = rfetm[rlic]
            if not same_club(frow["club"], rrow["club"]):
                continue
            resolved_f = club_map.get(frow["club"], frow["club"])
            if club_fragile(resolved_f, rrow["club"]):
                fragile += 1
                if len(fragile_samples) < args.samples:
                    fragile_samples.append(
                        f"      · «{frow['name']}» [{resolved_f} ≈ {rrow['club']}] "
                        f"FCTT {flic} / RFETM {rlic}")
            if (flic, rlic) in links:
                agree += 1
            elif flic in f_to_r or rlic in r_to_f:
                contradict += 1
                if len(contradict_samples) < args.samples:
                    other_r = f_to_r.get(flic, set())
                    other_f = r_to_f.get(rlic, set())
                    contradict_samples.append(
                        f"      · «{frow['name']}» FCTT {flic} + RFETM {rlic} "
                        f"[{frow['club']}≈{rrow['club']}], pero player_link dice "
                        f"FCTT{flic}→{sorted(other_r) or '—'} / RFETM{rlic}→{sorted(other_f) or '—'}")
            else:
                unknown += 1
    candidates = agree + contradict + unknown
    print("\n── PRECISIÓN (de lo que propondría «nombre+club») ──")
    print(f"   candidatos propuestos:      {candidates}")
    print(f"   coinciden con player_link:  {agree}")
    print(f"   CONTRADICEN a player_link:  {contradict}")
    print(f"   sin dato en player_link:    {unknown}  (ni confirman ni contradicen)")
    if agree + contradict:
        print(f"   precisión (agree/(agree+contradict)): "
              f"{100*agree/(agree+contradict):.1f}%  ← el número que decide")
    for s in contradict_samples:
        print(s)

    print("\n── GRIETA (match de club por UNA sola palabra: riesgo mismo pueblo) ──")
    print(f"   candidatos frágiles: {fragile}/{candidates}"
          f"{f' ({100*fragile/candidates:.1f}%)' if candidates else ''}")
    for s in fragile_samples:
        print(s)
    print("\nℹ️  Un candidato frágil casa porque el club de un lado es una sola "
          "palabra significativa subconjunto del otro (p. ej. el pueblo). Si son "
          "muchos, afinar _CLUB_STOP o exigir una señal extra antes de proponer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
