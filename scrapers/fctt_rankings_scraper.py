from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from models.ranking import RankingCategory, RankingEdition, RankingEntry
from parsers.fctt_ranking_pdf_parser import (
    EditionRef,
    parse_header,
    parse_index,
    parse_initial,
    parse_ranking,
    parse_title,
)
from sources import FCTT
from utils.hashing import md5_bytes
from utils.logger import get_logger

if TYPE_CHECKING:
    from storage.firestore_client import FirestoreClient
    from storage.local_cache import LocalCache
    from utils.http_client import HttpClient

logger = get_logger(__name__)

_COMPETITION_ID = FCTT.id

#: Cuántos jugadores se guardan en la lista `top` de cada categoría. Es lo que
#: permite pintar una clasificación histórica sin consultar por jugador.
_TOP_SIZE = 100


class FcttRankingsScraper:
    """Rànquings de la FCTT: un PDF por categoría y revisión mensual.

    El identificador `NP` de estos PDFs es el mismo `codi_jugador` que usan las
    páginas de liga, así que el ranking enlaza directamente con los partidos ya
    almacenados en `competitions/fctt_*`.

    Todas las ediciones se acumulan **en memoria** y cada jugador se escribe una
    sola vez. Escribiendo edición a edición serían ~28.000 escrituras en la carga
    histórica y no cabrían en la cuota diaria del plan gratuito.
    """

    def __init__(self, http: "HttpClient", pdfs_dir: str) -> None:
        self._http = http
        self._dir = Path(pdfs_dir) / "rankings"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── Índice ────────────────────────────────────────────────────────────────

    def get_index(self) -> list[EditionRef]:
        logger.info("⬇️  Descargando índice de rànquings FCTT...")
        try:
            html = self._http.fetch_text(FCTT.rankings_index)
        except RuntimeError as exc:
            logger.error(f"No se pudo descargar el índice de rànquings: {exc}")
            return []

        editions = parse_index(html)
        total = sum(len(e.pdfs) for e in editions)
        logger.info(f"✅ {len(editions)} ediciones, {total} PDFs de ranking")
        # De la más antigua a la más reciente: así `current` acaba siendo la
        # última edición procesada.
        return sorted(editions, key=_edition_sort_key)

    # ── Descarga y parseo ─────────────────────────────────────────────────────

    def scrape_edition(
        self,
        ref: EditionRef,
        cache: "LocalCache",
        only_category: str = "",
        force: bool = False,
    ) -> tuple[Optional[RankingEdition], dict]:
        """Descarga y parsea una edición completa.

        Los hashes **no** se guardan aquí: se devuelven en `stats["hashes"]` para
        que el llamador los confirme solo si la escritura en Firestore ha ido
        bien. Si se guardaran al parsear, un fallo al escribir dejaría la caché
        diciendo "ya está hecho" y la siguiente ejecución no reintentaría nada.
        """
        stats = {"categories": 0, "skipped": 0, "entries": 0, "errors": [], "hashes": {}}
        edition = RankingEdition(number=ref.number, label=ref.label)

        logger.info(f"🔎 Edición {ref.number} — {ref.label}")

        for category_id, url in sorted(ref.pdfs.items()):
            if only_category and category_id != only_category:
                continue

            cache_key = f"fctt_rk_{ref.number}_{category_id}"
            # Con --force se vuelve a bajar el PDF: la FCTT republica revisiones
            # bajo el mismo nombre (la 06 está en v2), y con la copia local
            # nunca veríamos la corrección.
            pdf_path = self._download(
                url, f"{ref.number}_{category_id}.pdf", refresh=force
            )
            if pdf_path is None:
                msg = f"No se pudo descargar {url}"
                stats["errors"].append(msg)
                continue

            pdf_hash = md5_bytes(Path(pdf_path).read_bytes())
            if not force and cache.get_matchday_hash(cache_key) == pdf_hash:
                stats["skipped"] += 1
                continue

            try:
                entries = (
                    parse_initial(pdf_path)
                    if ref.number == "ini"
                    else parse_ranking(pdf_path)
                )
            except Exception as exc:  # pdfplumber puede fallar en un PDF corrupto
                msg = f"Error parseando {category_id} de la edición {ref.number}: {exc}"
                logger.warning(f"   └─ ⚠️  {msg}")
                stats["errors"].append(msg)
                continue

            if not entries:
                msg = f"{category_id} edición {ref.number}: 0 filas parseadas"
                logger.warning(f"   └─ ⚠️  {msg}")
                stats["errors"].append(msg)
                continue

            if not edition.month:
                revision, month, season, version = parse_header(pdf_path)
                edition.month, edition.season, edition.version = month, season, version
                if revision and ref.number != "ini":
                    edition.number = revision.zfill(2)

            edition.categories.append(
                RankingCategory(
                    id=category_id,
                    title=parse_title(pdf_path),
                    source_url=url,
                    entries=entries,
                )
            )
            stats["hashes"][cache_key] = pdf_hash
            stats["categories"] += 1
            stats["entries"] += len(entries)
            logger.info(f"   └─ 📄 {category_id}: {len(entries)} jugadores")

        if stats["skipped"]:
            logger.info(f"   └─ {stats['skipped']} categorías sin cambios (hash)")

        return (edition if edition.categories else None), stats

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


# ── Acumulación por jugador ───────────────────────────────────────────────────


def build_player_documents(editions: list[RankingEdition]) -> dict[str, dict]:
    """Funde todas las ediciones en un documento por jugador.

    Las ediciones deben venir de la más antigua a la más reciente: `current` se
    va sobreescribiendo y acaba reflejando la última.
    """
    players: dict[str, dict] = {}

    for edition in editions:
        for category in edition.categories:
            for entry in category.entries:
                doc = players.setdefault(
                    entry.player_code,
                    {"code": entry.player_code, "current": {}, "history": {}},
                )
                # Datos de nivel jugador: gana siempre el más reciente.
                doc["name"] = entry.name
                if entry.club:
                    doc["club"] = entry.club
                if entry.edat_fctt:
                    doc["edat_fctt"] = entry.edat_fctt
                gender = entry.gender or _gender_from_category(category.id)
                if gender:
                    doc["gender"] = gender

                doc["history"].setdefault(category.id, {})[edition.number] = (
                    entry.to_history()
                )
                doc["current"][category.id] = {
                    "pos": entry.position,
                    "rk": entry.rk,
                    "edition": edition.number,
                }

    now = datetime.now(timezone.utc).isoformat()
    for doc in players.values():
        doc["updated_at"] = now
    return players


def build_category_documents(edition: RankingEdition) -> list[tuple[str, dict]]:
    """Un documento por categoría con su cabecera y el `top`."""
    docs = []
    for category in edition.categories:
        ordered = sorted(category.entries, key=lambda e: e.position)
        docs.append(
            (
                category.id,
                {
                    "id": category.id,
                    "title": category.title,
                    "source_url": category.source_url,
                    "player_count": len(category.entries),
                    "top": [e.to_top() for e in ordered[:_TOP_SIZE]],
                },
            )
        )
    return docs


def _gender_from_category(category_id: str) -> str:
    """SEF/40F/BEF → F · SEM/40M/BEM → M · ADA/GEN → desconocido."""
    if category_id in ("ADA", "GEN"):
        return ""
    if category_id.endswith("F"):
        return "F"
    if category_id.endswith("M"):
        return "M"
    return ""


def _edition_sort_key(ref: EditionRef) -> tuple[int, str]:
    """La inicial va primero; el resto por número de revisión."""
    return (0, "") if ref.number == "ini" else (1, ref.number)
