from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from models.player_match import MatchLog, PlayerMatch, build_record
from parsers.fctt_partits_parser import (
    PartitsRef,
    RawRow,
    classify_event,
    parse_index,
    parse_partits,
    resolve_opponent_codes,
)
from sources import FCTT
from utils.hashing import md5_bytes
from utils.logger import get_logger

if TYPE_CHECKING:
    from storage.local_cache import LocalCache
    from utils.http_client import HttpClient

logger = get_logger(__name__)


class FcttPartitsScraper:
    """`Partits_Jugats`: el histórico partido a partido de la FCTT.

    Un PDF por revisión mensual, publicado en el mismo `/ranquings/` que los
    rànquings. La `NP`/`LLIC` de estos PDFs es el mismo `codi_jugador` de las
    páginas de liga y de `rankings/players`, así que todo enlaza sin casar
    nombres.

    **La caché aquí solo ahorra descargas y escrituras, no parseo.** Los
    partidos de todas las ediciones hacen falta en memoria para recalcular el
    `record` de temporada del jugador; en cambio los documentos de una edición
    que no ha cambiado no se reescriben.
    """

    def __init__(self, http: "HttpClient", pdfs_dir: str) -> None:
        self._http = http
        self._dir = Path(pdfs_dir) / "partits"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── Índice ────────────────────────────────────────────────────────────────

    def get_index(self) -> list[PartitsRef]:
        logger.info("⬇️  Descargando índice de rànquings FCTT...")
        try:
            html = self._http.fetch_text(FCTT.rankings_index)
        except RuntimeError as exc:
            logger.error(f"No se pudo descargar el índice: {exc}")
            return []

        refs = parse_index(html)
        logger.info(f"✅ {len(refs)} ediciones con partidos jugados")
        return refs

    # ── Descarga y parseo ─────────────────────────────────────────────────────

    def scrape_edition(
        self,
        ref: PartitsRef,
        cache: "LocalCache",
        force: bool = False,
    ) -> tuple[list[MatchLog], dict]:
        """Descarga, parsea y agrupa por jugador los partidos de una edición.

        `stats["changed"]` dice si hay que reescribir los documentos: si el PDF
        es idéntico al de la última ejecución, los partidos se devuelven igual
        —hacen falta para el `record`— pero no hay nada que guardar.

        Los hashes se devuelven en `stats["hashes"]` en vez de guardarse aquí,
        para que el llamador los confirme solo si la escritura ha ido bien.
        """
        stats = {
            "rows": 0,
            "matches": 0,
            "external": 0,
            "players": 0,
            "changed": False,
            "errors": [],
            "hashes": {},
        }

        logger.info(f"🔎 Edición {ref.number} — {ref.label}")

        # Con --force se vuelve a bajar: la FCTT republica revisiones bajo el
        # mismo nombre y con la copia local nunca veríamos la corrección.
        pdf_path = self._download(ref.url, f"{ref.number}.pdf", refresh=force)
        if pdf_path is None:
            stats["errors"].append(f"No se pudo descargar {ref.url}")
            return [], stats

        try:
            rows = parse_partits(pdf_path)
        except Exception as exc:  # pdfplumber puede fallar en un PDF corrupto
            msg = f"Error parseando la edición {ref.number}: {exc}"
            logger.warning(f"   └─ ⚠️  {msg}")
            stats["errors"].append(msg)
            return [], stats

        if not rows:
            msg = f"Edición {ref.number}: 0 filas parseadas"
            logger.warning(f"   └─ ⚠️  {msg}")
            stats["errors"].append(msg)
            return [], stats

        cache_key = f"fctt_pj_{ref.number}"
        pdf_hash = md5_bytes(Path(pdf_path).read_bytes())
        changed = force or cache.get_matchday_hash(cache_key) != pdf_hash

        logs = build_match_logs(rows, ref)
        external = sum(
            1 for log in logs for m in log.matches if not m.opponent_code
        )

        stats.update(
            rows=len(rows),
            matches=sum(len(log.matches) for log in logs),
            external=external,
            players=len(logs),
            changed=changed,
        )
        if changed:
            stats["hashes"][cache_key] = pdf_hash

        logger.info(
            f"   └─ 📄 {len(rows)} filas · {len(logs)} jugadores · "
            f"{external} rivales sin licencia FCTT"
            + ("" if changed else "  (sin cambios)")
        )
        return logs, stats

    def _download(self, url: str, filename: str, refresh: bool = False) -> Optional[str]:
        """Descarga el PDF si no está ya en local. Devuelve la ruta."""
        path = self._dir / filename
        if path.exists() and not refresh:
            logger.debug(f"   └─ PDF {filename} ya en caché local (origen: {url})")
            return str(path)

        logger.info(f"   └─ ⬇️  {url}")
        try:
            data = self._http.fetch(url)
        except RuntimeError as exc:
            logger.warning(f"   └─ ⚠️  No se pudo descargar {url}: {exc}")
            return None

        path.write_bytes(data)
        return str(path)


# ── Construcción de documentos ────────────────────────────────────────────────


def build_match_logs(rows: list[RawRow], ref: PartitsRef) -> list[MatchLog]:
    """Agrupa las filas de una edición por jugador.

    Cada partido aparece dos veces en el PDF, una desde cada jugador, y **las
    dos se conservan**: cada una va al `match_log` de su jugador. No hay nada
    que deduplicar; el espejo solo se usa para averiguar la licencia del rival.
    """
    codes = resolve_opponent_codes(rows)
    logs: dict[str, MatchLog] = {}

    for row, opponent_code in zip(rows, codes):
        log = logs.setdefault(
            row.licence,
            MatchLog(player_code=row.licence, edition=ref.number, label=ref.label),
        )
        log.matches.append(
            PlayerMatch(
                date=row.date,
                event=row.event,
                event_type=classify_event(row.event),
                category=row.category,
                opponent_name=row.opponent_name,
                opponent_code=opponent_code or "",
                sets=row.sets,
                opp_sets=row.opp_sets,
                rk=row.rk,
                opp_rk=row.opp_rk,
                d_rk=row.d_rk,
                rd=row.rd,
            )
        )

    return list(logs.values())


def build_records(all_logs: list[MatchLog], only: set[str]) -> dict[str, dict]:
    """Resumen de temporada por jugador, a partir de **todas** las ediciones.

    `only` limita a quién se le reescribe el resumen —los jugadores de las
    ediciones que han cambiado—, pero el cálculo usa siempre el histórico
    completo: un `record` construido con media temporada sería sencillamente
    falso.
    """
    por_jugador: dict[str, list[MatchLog]] = {}
    for log in all_logs:
        por_jugador.setdefault(log.player_code, []).append(log)

    return {
        code: record
        for code, logs in por_jugador.items()
        if code in only and (record := build_record(logs))
    }
