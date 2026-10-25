#!/usr/bin/env python3
"""Carga por tandas de un .sql de D1 (generado por D1Client) contra la base
remota, troceando en lotes de N statements (plan_migracion_d1.md §4/§7).

Por qué trocear: `wrangler d1 execute --remote --file` con ficheros de decenas
de miles de statements falla/agota; y la cuota gratuita son 100.000 filas
escritas/día por CUENTA (compartida con Kanto), reset a las 00:00 UTC. Cada
lote es idempotente (`INSERT … ON CONFLICT`), así que trocear es seguro y
reanudable: si un lote falla por cuota, se para y se retoma otro día con
`--start`.

Uso (desde tt-scraper/):
    python d1/load_chunks.py output/sql/load_XXXX.sql --db tt-data \
        [--chunk 2500] [--start 1] [--limit N] [--dry-run] [--analyze]

`--analyze`: al terminar el ÚLTIMO lote del fichero, lanza `ANALYZE;` para
refrescar sqlite_stat1 y que el planificador de D1 elija los índices buenos
(evita escaneos de tablas enteras que agotan la cuota de lecturas). Ver
design/PETICION-A-TT-SCRAPER.md. Solo dispara si esta invocación llega al
último lote (con reanudaciones por cuota, úsalo en la que cierra la carga).

- Cada lote se escribe a output/sql/chunks/<fichero>/NNNN.sql con la cabecera
  `PRAGMA foreign_keys=OFF;` y se ejecuta con wrangler (--json).
- Suma `rows_written` reportado por D1 y para si un lote da error de cuota,
  imprimiendo el nº de lote para reanudar.
"""
from __future__ import annotations
import os

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# wrangler vive en apps/api (monorepo); se invoca con ese cwd para que resuelva
# la base por nombre con el token de la cuenta.
REPO = Path(__file__).resolve().parents[2]
# wrangler vive en apps/api (monorepo) por defecto; en otro despliegue (VPS) se
# apunta con TTS_WRANGLER_CWD (dir con wrangler.toml + node_modules) y, si hace
# falta, TTS_WRANGLER (ruta al binario).
WRANGLER_CWD = Path(os.environ.get("TTS_WRANGLER_CWD") or (REPO / "apps" / "api"))
WRANGLER = Path(os.environ.get("TTS_WRANGLER") or (WRANGLER_CWD / "node_modules" / ".bin" / "wrangler"))

# Señales de "cuota diaria agotada" en el error de la API de D1.
QUOTA_MARKERS = ("exceeded", "limit", "quota", "too many write")


def read_statements(path: Path) -> list[str]:
    """Statements INSERT del fichero (D1Client emite uno por línea).

    Reensambla por si un valor trae un salto de línea embebido (p.ej. una
    dirección): las líneas que NO empiezan por 'INSERT INTO' son continuación
    del statement en curso y se reúnen con un espacio, para no truncarlo. Las
    líneas de cabecera (PRAGMA, comentarios) previas al primer INSERT se ignoran.
    """
    out: list[str] = []
    cur: str | None = None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            # Inicio de sentencia: INSERT (el caso normal) o DELETE (borrado de
            # huérfanas en build_person). Lo demás es continuación de un valor
            # con salto de línea embebido (p.ej. una dirección), que se reúne.
            if s.startswith("INSERT INTO") or s.startswith("DELETE FROM"):
                if cur is not None:
                    out.append(cur)
                cur = s
            elif cur is not None and s:
                cur += " " + s
        if cur is not None:
            out.append(cur)
    return out


def _run_wrangler(exec_args: list[str]) -> dict:
    """Invoca `wrangler d1 execute <db> --remote --json <exec_args>` y parsea la
    respuesta. Devuelve {ok, rows_written, rows_read, error}."""
    proc = subprocess.run(
        [str(WRANGLER), "d1", "execute", "--remote", "--json", *exec_args],
        cwd=str(WRANGLER_CWD),
        capture_output=True,
        text=True,
    )
    raw = proc.stdout.strip() or proc.stderr.strip()
    # wrangler antepone líneas de progreso ("Checking/Uploading…") al JSON en
    # ficheros grandes. Extraemos el JSON desde el primer '[' o '{'.
    starts = [p for p in (raw.find("["), raw.find("{")) if p != -1]
    data = None
    if starts:
        try:
            data = json.loads(raw[min(starts):])
        except json.JSONDecodeError:
            data = None
    if data is None:
        return {"ok": False, "rows_written": 0, "rows_read": 0, "error": raw[:400]}
    # wrangler --json devuelve lista de resultados o {error:{...}}.
    if isinstance(data, dict) and data.get("error"):
        return {"ok": False, "rows_written": 0, "rows_read": 0,
                "error": json.dumps(data["error"])[:400]}
    written = read = 0
    results = data if isinstance(data, list) else [data]
    for r in results:
        r = r or {}
        # Forma --command: meta.rows_written/rows_read. Forma --file: results[].["Rows written"].
        meta = r.get("meta") or {}
        written += int(meta.get("rows_written") or 0)
        read += int(meta.get("rows_read") or 0)
        for inner in (r.get("results") or []):
            if isinstance(inner, dict) and "Rows written" in inner:
                written += int(inner.get("Rows written") or 0)
    return {"ok": True, "rows_written": written, "rows_read": read, "error": None}


def run_chunk(chunk_file: Path, db: str) -> dict:
    """Ejecuta un lote con wrangler --remote --json (por fichero)."""
    return _run_wrangler([db, "--file", str(chunk_file.resolve())])


def run_analyze(db: str) -> dict:
    """Lanza `ANALYZE;` en la base remota para refrescar sqlite_stat1 y que el
    planificador elija los índices buenos (design/PETICION-A-TT-SCRAPER.md)."""
    return _run_wrangler([db, "--command", "ANALYZE;"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sql", type=Path, help="Fichero .sql a cargar")
    ap.add_argument("--db", default="tt-data")
    ap.add_argument("--chunk", type=int, default=2500, help="statements por lote")
    ap.add_argument("--start", type=int, default=1, help="primer lote (1-based) para reanudar")
    ap.add_argument("--limit", type=int, default=0, help="máx. lotes a ejecutar (0 = todos)")
    ap.add_argument("--dry-run", action="store_true", help="solo trocear, sin ejecutar")
    ap.add_argument("--analyze", action="store_true",
                    help="tras ejecutar el ÚLTIMO lote, lanzar ANALYZE; para "
                         "refrescar sqlite_stat1 (ver design/PETICION-A-TT-SCRAPER.md)")
    args = ap.parse_args()

    if not args.sql.exists():
        print(f"No existe: {args.sql}", file=sys.stderr)
        return 2

    stmts = read_statements(args.sql)
    total = len(stmts)
    n_chunks = (total + args.chunk - 1) // args.chunk
    print(f"📄 {args.sql.name}: {total} statements → {n_chunks} lotes de {args.chunk}")

    chunk_dir = args.sql.parent / "chunks" / args.sql.stem
    chunk_dir.mkdir(parents=True, exist_ok=True)

    written_total = 0
    done = 0
    last_chunk_done = False  # ¿se ejecutó el lote final en esta invocación?
    for i in range(n_chunks):
        idx = i + 1  # 1-based
        if idx < args.start:
            continue
        if args.limit and done >= args.limit:
            print(f"⏹️  Alcanzado --limit {args.limit}. Reanuda con --start {idx}.")
            break
        part = stmts[i * args.chunk : (i + 1) * args.chunk]
        chunk_file = chunk_dir / f"{idx:04d}.sql"
        chunk_file.write_text("PRAGMA foreign_keys=OFF;\n" + "\n".join(part) + "\n", encoding="utf-8")
        if args.dry_run:
            print(f"  · lote {idx}/{n_chunks}: {len(part)} stmts → {chunk_file} (dry-run)")
            done += 1
            continue

        # Reintenta errores transitorios (red); para en cuota.
        attempt = 0
        while True:
            attempt += 1
            res = run_chunk(chunk_file, args.db)
            if res["ok"]:
                written_total += res["rows_written"]
                if idx == n_chunks:
                    last_chunk_done = True
                print(f"  ✅ lote {idx}/{n_chunks}: +{res['rows_written']} filas "
                      f"(acum {written_total})")
                break
            err = (res["error"] or "").lower()
            if any(m in err for m in QUOTA_MARKERS):
                print(f"  🛑 lote {idx}: cuota diaria agotada. Reanuda mañana con "
                      f"--start {idx}. Detalle: {res['error']}")
                return 3
            if attempt >= 6:
                print(f"  ❌ lote {idx} falló tras {attempt} intentos: {res['error']}")
                return 4
            # Errores transitorios de D1 (reset de conexión, "fetch failed",
            # timeouts): reintento con backoff creciente (hasta ~30s).
            print(f"  ⚠️  lote {idx} intento {attempt} falló, reintento… {res['error'][:120]}")
            time.sleep(min(5 * attempt, 30))
        done += 1

    print(f"🏁 Hecho. Lotes ejecutados: {done}. Filas escritas (D1): {written_total}.")

    # ANALYZE solo tras completar el ÚLTIMO lote del fichero (no en dry-run ni
    # en resúmenes parciales por --limit): recrear estadísticas a medio volcado
    # no aporta y gasta lecturas. Idempotente y barato (~1,7 M lecturas, una vez).
    if args.analyze and not args.dry_run:
        if not last_chunk_done:
            print("ℹ️  --analyze omitido: esta ejecución no llegó al último lote "
                  "(reanuda hasta el final y lánzalo entonces).")
            return 0
        print("🔎 Lanzando ANALYZE; para refrescar sqlite_stat1…")
        res = run_analyze(args.db)
        if res["ok"]:
            print(f"  ✅ ANALYZE ok (leídas {res['rows_read']}, escritas "
                  f"{res['rows_written']}).")
        else:
            print(f"  ❌ ANALYZE falló: {res['error']}")
            return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
