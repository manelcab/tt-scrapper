#!/usr/bin/env python3
"""Construye la capa de identidad de persona (`person` + `person_licence`):

- CONFIRMADO: pares de `player_link` (partidos de liga nacional) y licencias
  sueltas (singletons) — Fase 1.
- PROBABLE: pares FCTT↔RFETM con nombre exacto igual y club equivalente
  (resolviendo el código FCTT con `club_link`) que `player_link` no explica —
  Fase 5. Salvaguardas: se saltan licencias ya confirmadas-enlazadas (la única
  contradicción conocida la separa `player_link`) y los emparejamientos ambiguos
  (una licencia con más de un candidato). Desactivable con --no-probable.

Ver design/plan_identidad_persona.md.

Idea: cada componente conexa sobre los pares de `player_link` es una persona
(sus licencias FCTT/RFETM equivalentes). Cada licencia de `player` que no está
en ningún par es una persona de una sola licencia. NO se casa por nombre: eso es
la Fase 3 (`probable`), que llega después de capturar birth_year de la FCTT.

Estabilidad del id (plan §3):
  - Se LEE primero `person_licence` existente: toda licencia ya asignada conserva
    su `person_id`.
  - Un cluster nuevo recibe `p_<sha1(federation:lic del miembro canónico)>`.
  - Si un cluster reúne licencias que hoy tienen person_ids distintos (fusión),
    se elige un superviviente (el que cubre más licencias del cluster; empate →
    id menor) y los demás se registran en `person_id_alias` (nunca se reescribe
    en silencio).

Correcciones manuales (plan §4/§6): el emit NO pisa filas con
`decided_by <> 'scraper'` (lo manual manda). El fichero versionado
`d1/identity_overrides.csv` se aplicará en un paso posterior.

Uso (desde tt-scraper/):
    NODE_OPTIONS=--dns-result-order=ipv4first \\
        python d1/build_person.py --db tt-data [--dry-run]
Luego cargar el .sql con d1/load_chunks.py.
"""
from __future__ import annotations
import os

import argparse
import csv
import datetime as dt
import hashlib
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
SQL_OUT = Path(__file__).resolve().parents[1] / "output" / "sql"

LINKS_SQL = (
    "SELECT federation_a, lic_a, federation_b, lic_b, confidence "
    "FROM player_link"
)
PLAYERS_SQL = "SELECT federation, lic, name, club, sex, birth_year FROM player"
EXISTING_SQL = "SELECT person_id, federation, lic, status, decided_by FROM person_licence"
CLUB_LINK_SQL = "SELECT fctt_code, rfetm_name FROM club_link"
PCS_SQL = "SELECT federation, lic, season, club FROM player_club_season"

# Correcciones manuales versionadas (paso 6): el admin afirma que dos licencias
# SÍ ('confirmed') o NO ('rejected') son la misma persona. Mandan sobre lo
# deducido y sobreviven a cada pase.
OVERRIDES = Path(__file__).resolve().parent / "identity_overrides.csv"

Lic = tuple[str, str]  # (federation, lic)


def read_overrides() -> tuple[list[tuple[Lic, Lic, str]], set[frozenset]]:
    """Lee d1/identity_overrides.csv → (confirmados, rechazados).

    Formato: federation_a,lic_a,federation_b,lic_b,decision,decided_by,note
    decision = confirmed | rejected. Líneas vacías o que empiezan por # se ignoran.
    """
    confirmed: list[tuple[Lic, Lic, str]] = []
    rejected: set[frozenset] = set()
    if not OVERRIDES.exists():
        return confirmed, rejected
    with OVERRIDES.open(encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row or not row[0].strip() or row[0].strip().startswith("#"):
                continue
            if row[0].strip() == "federation_a":  # cabecera
                continue
            if len(row) < 5:
                continue
            fa, la, fb, lb, decision = (c.strip() for c in row[:5])
            decided_by = (row[5].strip() if len(row) > 5 and row[5].strip()
                          else "admin:unknown")
            a, b = (fa, la), (fb, lb)
            if decision == "confirmed":
                confirmed.append((a, b, decided_by))
            elif decision == "rejected":
                rejected.add(frozenset((a, b)))
    return confirmed, rejected


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
        # person_licence puede no existir aún en la primera ejecución.
        if "no such table" in json.dumps(data["error"]).lower():
            return []
        sys.exit(f"❌ D1 error: {json.dumps(data['error'])[:400]}")
    results = data if isinstance(data, list) else [data]
    out: list[dict] = []
    for r in results:
        out.extend((r or {}).get("results") or [])
    return out


def esc(v) -> str:
    """Escapa comillas y aplasta saltos de línea (una sentencia por línea)."""
    text = str(v).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return text.replace("'", "''")


def sql_val(v) -> str:
    """Literal SQL: NULL, número o texto escapado entre comillas."""
    if v is None or v == "":
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    return f"'{esc(v)}'"


# ── Nombre y club (solo para la Fase 'probable'; NUNCA para 'confirmed') ──────
_CLUB_STOP = {
    "CTT", "CT", "CLUB", "TENNIS", "TENIS", "TAULA", "TM", "DE", "DEL", "LA",
    "EL", "LES", "ELS", "SECCIO", "SECCION", "SEC", "AE", "CE", "AD", "CD",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def norm_name(s: str) -> str:
    """Nombre canónico: sin tildes, mayúsculas, espacios colapsados."""
    return re.sub(r"\s+", " ", _strip_accents(s or "").upper()).strip()


def club_tokens(s: str) -> frozenset[str]:
    words = re.split(r"[^A-Z0-9]+", _strip_accents(s or "").upper())
    return frozenset(w for w in words if w and len(w) > 1 and w not in _CLUB_STOP)


def club_equiv(a: str, b: str) -> bool:
    ta, tb = club_tokens(a), club_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[Lic, Lic] = {}

    def find(self, x: Lic) -> Lic:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # compresión de camino
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: Lic, b: Lic) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def person_id_for(canonical: Lic) -> str:
    seed = f"{canonical[0]}:{canonical[1]}"
    return "p_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="tt-data")
    ap.add_argument("--dry-run", action="store_true", help="no escribe .sql, solo estadísticas")
    ap.add_argument("--no-probable", action="store_true",
                    help="no proponer 'probable' por nombre+club (solo confirmado)")
    args = ap.parse_args()

    print("⬇️  Leyendo player_link, player, person_licence y club_link…")
    links = query(args.db, LINKS_SQL)
    players = query(args.db, PLAYERS_SQL)
    existing = query(args.db, EXISTING_SQL)
    club_rows = [] if args.no_probable else query(args.db, CLUB_LINK_SQL)
    pcs_rows = [] if args.no_probable else query(args.db, PCS_SQL)
    confirmed_ov, rejected_ov = read_overrides()
    print(f"   player_link: {len(links)} · player: {len(players)} · "
          f"person_licence existente: {len(existing)} · club_link: {len(club_rows)}")
    if confirmed_ov or rejected_ov:
        print(f"   overrides manuales: {len(confirmed_ov)} confirmados · "
              f"{len(rejected_ov)} rechazados")

    # ── Datos por licencia ────────────────────────────────────────────────
    info: dict[Lic, dict] = {}
    for p in players:
        key = (str(p["federation"]), str(p["lic"]))
        info[key] = {
            "name": p.get("name") or "",
            "club": (p.get("club") or "").strip(),
            "sex": p.get("sex"),
            "birth_year": p.get("birth_year"),
        }

    link_conf: dict[Lic, float] = {}     # confianza máx. de enlaces por licencia
    linked_lics: set[Lic] = set()        # licencias en algún par de player_link

    # ── Componentes conexas (union-find) sobre player_link ────────────────
    uf = UnionFind()
    for key in info:
        uf.find(key)  # cada licencia es un nodo (incluye singletons)
    for ln in links:
        a = (str(ln["federation_a"]), str(ln["lic_a"]))
        b = (str(ln["federation_b"]), str(ln["lic_b"]))
        info.setdefault(a, {"name": "", "club": "", "sex": None, "birth_year": None})
        info.setdefault(b, {"name": "", "club": "", "sex": None, "birth_year": None})
        uf.union(a, b)
        linked_lics.add(a)
        linked_lics.add(b)
        conf = ln.get("confidence")
        if conf is not None:
            link_conf[a] = max(link_conf.get(a, 0.0), float(conf))
            link_conf[b] = max(link_conf.get(b, 0.0), float(conf))

    # Overrides 'confirmed': aristas manuales (como un player_link a mano). Se
    # unen en el union-find; la licencia queda 'confirmed' con evidence='manual'.
    manual_by: dict[Lic, str] = {}
    for a, b, by in confirmed_ov:
        for k in (a, b):
            info.setdefault(k, {"name": "", "club": "", "sex": None, "birth_year": None})
        uf.union(a, b)
        manual_by.setdefault(a, by)
        manual_by.setdefault(b, by)

    # Filas 'manual' (admin) que YA NO están en el CSV: hay que revertirlas a lo
    # deducido. El emit normal no deja que el scraper pise una fila admin, así que
    # estas se reescriben sin esa guarda (ver bloque de emisión).
    # Manual = cualquier origen que NO sea el scraper (admin:…, kayzen:jugador…).
    existing_manual = {(str(e["federation"]), str(e["lic"]))
                       for e in existing
                       if str(e.get("decided_by") or "scraper") not in ("", "scraper")}
    revert_lics = {k for k in existing_manual if k not in manual_by}

    clusters: dict[Lic, list[Lic]] = defaultdict(list)
    for key in info:
        clusters[uf.find(key)].append(key)

    # Id estable: SOLO ancla en filas confirmadas. Las 'probable' no fijan id,
    # para que un probable retirado revierta a la persona propia (reversible).
    existing_pid: dict[Lic, str] = {
        (str(e["federation"]), str(e["lic"])): str(e["person_id"])
        for e in existing if e.get("status") != "probable"
    }

    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    alias_rows: list[tuple] = []         # (old_id, new_id)

    # pid y base (confirmado) por licencia, a partir de las componentes conexas.
    # Provenance por licencia (no por cluster): nat_league_match si está en un par
    # de player_link; manual si viene de un override; singleton en otro caso.
    pid_of: dict[Lic, str] = {}
    base: dict[Lic, tuple] = {}          # lic -> (status, evidence, conf, decided_by)
    n_singleton = n_linked = n_manual = 0
    for members in clusters.values():
        members_sorted = sorted(members)
        canonical = members_sorted[0]
        present_ids = [existing_pid[m] for m in members_sorted if m in existing_pid]
        if not present_ids:
            pid = person_id_for(canonical)
        else:
            counts: dict[str, int] = defaultdict(int)
            for v in present_ids:
                counts[v] += 1
            pid = min(counts, key=lambda k: (-counts[k], k))
            for other in sorted(set(present_ids) - {pid}):
                alias_rows.append((other, pid))
        n_linked += 1 if len(members_sorted) > 1 else 0
        n_singleton += 0 if len(members_sorted) > 1 else 1
        for m in members_sorted:
            pid_of[m] = pid
            if m in linked_lics:
                base[m] = ("confirmed", "nat_league_match", link_conf.get(m), "scraper")
            elif m in manual_by:
                base[m] = ("confirmed", "manual", None, manual_by[m])
                n_manual += 1
            else:
                base[m] = ("confirmed", "singleton", None, "scraper")

    # ── Fase 'probable': nombre exacto + club resuelto (FCTT↔RFETM) ──────────
    # Salvaguardas: ninguna licencia ya confirmada-enlazada (así la única
    # contradicción conocida la separa player_link) y se descartan los ambiguos.
    probable: dict[Lic, Lic] = {}        # licencia 'probable' -> licencia ancla
    n_prob = n_skip_ambiguous = 0
    if not args.no_probable:
        club_map = {str(r["fctt_code"]): str(r["rfetm_name"]) for r in club_rows}

        # Club de TEMPORADA por licencia (nombre, en ambas federaciones), el de la
        # temporada más reciente. Unifica la representación de club: evita comparar
        # el código FCTT contra un nombre RFETM.
        best_pcs: dict[Lic, str] = {}
        best_season: dict[Lic, str] = {}
        for r in pcs_rows:
            key = (str(r["federation"]), str(r["lic"]))
            season = str(r.get("season") or "")
            if r.get("club") and season >= best_season.get(key, ""):
                best_season[key] = season
                best_pcs[key] = str(r["club"])

        def eff_club(key: Lic) -> str:
            # Preferir el club de temporada (nombre); si no hay, player.club,
            # resolviendo el código FCTT con club_link.
            c = best_pcs.get(key)
            if c:
                return c
            raw = info.get(key, {}).get("club", "")
            return club_map.get(raw, raw)

        def same_club(fkey: Lic, rkey: Lic) -> bool:
            return club_equiv(eff_club(fkey), eff_club(rkey))

        # Licencias ya resueltas (no se proponen por nombre+club): las de
        # player_link y las de un override manual confirmado.
        settled = linked_lics | set(manual_by)

        rfetm_by_name: dict[str, list[str]] = defaultdict(list)
        for (fed, lic), d in info.items():
            if fed == "RFETM" and d["name"] and (fed, lic) not in settled:
                rfetm_by_name[norm_name(d["name"])].append(lic)

        candidates: list[tuple[Lic, Lic]] = []
        for (fed, lic), d in info.items():
            if fed != "FCTT" or not d["name"] or (fed, lic) in settled:
                continue
            for rlic in rfetm_by_name.get(norm_name(d["name"]), ()):  # noqa: SIM118
                rkey = ("RFETM", rlic)
                if frozenset(((fed, lic), rkey)) in rejected_ov:
                    continue  # el admin dijo que NO son la misma persona
                if same_club((fed, lic), rkey):
                    candidates.append(((fed, lic), rkey))

        deg: dict[Lic, int] = defaultdict(int)
        for f, r in candidates:
            deg[f] += 1
            deg[r] += 1
        for f, r in candidates:
            if deg[f] > 1 or deg[r] > 1:   # nombre+club ambiguo: no se propone
                n_skip_ambiguous += 1
                continue
            # La licencia ancla (menor) conserva su persona; la otra pasa a
            # 'probable' sobre esa persona. No se crea alias (es reversible).
            anchor, other = (f, r) if f <= r else (r, f)
            probable[other] = anchor
            n_prob += 1

    # Asignación final por licencia (la 'probable' re-apunta a la persona ancla).
    licence_final: dict[Lic, tuple] = {}   # lic -> (pid, status, evidence, conf, decided_by)
    for lic, (status, evidence, conf, by) in base.items():
        licence_final[lic] = (pid_of[lic], status, evidence, conf, by)
    for other, anchor in probable.items():
        licence_final[other] = (pid_of[anchor], "probable", "name_and_club", None, "scraper")

    # Personas desde la pertenencia FINAL: un ancla que pierde todas sus
    # licencias (singleton absorbido como probable) queda huérfano y no se emite.
    members_now: dict[str, list[Lic]] = defaultdict(list)
    for lic, (pid, *_rest) in licence_final.items():
        members_now[pid].append(lic)

    person_rows: list[tuple] = []        # (id, name, birth_year, sex)
    for pid, members in members_now.items():
        members_sorted = sorted(members)
        canonical = members_sorted[0]
        name = info[canonical]["name"] or next(
            (info[m]["name"] for m in members_sorted if info[m]["name"]), "")
        birth_year = next(
            (info[m]["birth_year"] for m in members_sorted if info[m]["birth_year"]), None)
        sex = next((info[m]["sex"] for m in members_sorted if info[m]["sex"]), None)
        person_rows.append((pid, name, birth_year, sex))

    licence_rows: list[tuple] = [        # (pid, fed, lic, status, evidence, conf, decided_by)
        (pid, lic[0], lic[1], status, evidence, conf, by)
        for lic, (pid, status, evidence, conf, by) in licence_final.items()
    ]

    # Personas huérfanas: ids que ya existían pero que se quedan sin ninguna
    # licencia (un singleton absorbido como 'probable', o el id viejo de una
    # fusión). Se borran para no dejar filas sin referencias; es reversible (si
    # el probable se retira, la licencia vuelve a su id propio y se recrea).
    orphan_pids = sorted(set(existing_pid.values()) - set(members_now.keys()))

    print(f"\n📊 Personas: {len(person_rows)} "
          f"(confirmadas ≥2 lic: {n_linked} · singletons: {n_singleton})")
    print(f"   licencias: {len(licence_rows)} · probable (nombre+club): {n_prob}"
          f"  (ambiguos descartados: {n_skip_ambiguous})"
          f"{f' · manuales: {n_manual}' if n_manual else ''}")
    if orphan_pids:
        print(f"   personas huérfanas a borrar: {len(orphan_pids)}")
    if revert_lics:
        print(f"   filas manuales revertidas (fuera del CSV): {len(revert_lics)}")
    if alias_rows:
        print(f"   ⚠️  Fusiones (person_id_alias): {len(alias_rows)}")

    if args.dry_run:
        print("\n(dry-run: no se escribe .sql)")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = SQL_OUT / f"person_{stamp}.sql"
    SQL_OUT.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for pid, name, by, sex in person_rows:
            fh.write(
                "INSERT INTO person (id, display_name, birth_year, sex, updated_at) VALUES "
                f"('{esc(pid)}', {sql_val(name)}, {sql_val(by)}, {sql_val(sex)}, '{now_iso}') "
                "ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name, "
                "birth_year=excluded.birth_year, sex=excluded.sex, updated_at=excluded.updated_at;\n"
            )
        for pid, fed, lic, status, evidence, conf, by in licence_rows:
            # El WHERE deja escribir a un origen manual (overrides del CSV:
            # admin:…, kayzen:jugador…) pero NO al scraper sobre una fila ya
            # decidida a mano: así lo manual persiste entre pases y solo el CSV lo
            # cambia. EXCEPCIÓN: una licencia que era manual y ya NO está en el CSV
            # se revierte sin guarda (revert_lics).
            guard = ("" if (fed, lic) in revert_lics
                     else " WHERE person_licence.decided_by='scraper' "
                          "OR excluded.decided_by<>'scraper'")
            fh.write(
                "INSERT INTO person_licence (person_id, federation, lic, status, evidence, "
                "confidence, decided_at, decided_by) VALUES "
                f"('{esc(pid)}', '{esc(fed)}', '{esc(lic)}', '{status}', '{evidence}', "
                f"{sql_val(conf)}, '{now_iso}', '{esc(by)}') "
                "ON CONFLICT(federation, lic) DO UPDATE SET person_id=excluded.person_id, "
                "status=excluded.status, evidence=excluded.evidence, confidence=excluded.confidence, "
                f"decided_at=excluded.decided_at, decided_by=excluded.decided_by{guard};\n"
            )
        for old_id, new_id in alias_rows:
            fh.write(
                "INSERT INTO person_id_alias (old_id, new_id, merged_at) VALUES "
                f"('{esc(old_id)}', '{esc(new_id)}', '{now_iso}') "
                "ON CONFLICT(old_id) DO UPDATE SET new_id=excluded.new_id, "
                "merged_at=excluded.merged_at;\n"
            )
        # Borrado de huérfanas AL FINAL: para entonces ninguna licencia las
        # referencia (ya se re-apuntaron arriba). Idempotente.
        for pid in orphan_pids:
            fh.write(f"DELETE FROM person WHERE id='{esc(pid)}';\n")
    print(f"\n💾 {out_path.name}: {len(person_rows)} personas, {len(licence_rows)} licencias"
          f"{f', {len(alias_rows)} alias' if alias_rows else ''}"
          f"{f', {len(orphan_pids)} borradas' if orphan_pids else ''}. "
          "Cárgalo con d1/load_chunks.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
