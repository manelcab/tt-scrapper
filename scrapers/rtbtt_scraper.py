from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from models.competition import Category, CompetitionIndex
from models.match import Match, Matchday
from parsers.html_parser import HtmlParser
from parsers.pdf_parser import PdfParser
from scrapers.base import CompetitionScraper
from utils.hashing import md5_bytes, md5_str
from utils.logger import get_logger

if TYPE_CHECKING:
    from config import Config
    from storage.firestore_client import FirestoreClient
    from storage.local_cache import LocalCache
    from utils.http_client import HttpClient

logger = get_logger(__name__)


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    lower = ascii_text.lower()
    return re.sub(r"[\s\-]+", "_", lower).strip("_")


class RtbttScraper(CompetitionScraper):
    def __init__(self, config: "Config", http: "HttpClient") -> None:
        self._config = config
        self._http = http
        self._html_parser = HtmlParser()
        self._pdf_parser = PdfParser()

    def get_source_name(self) -> str:
        return "RTBTT"

    def get_index(self) -> list[Category]:
        logger.info("⬇️  Descargando índice de competición...")
        try:
            html = self._http.fetch_text(self._config.index_url)
        except RuntimeError as exc:
            logger.error(f"No se pudo descargar el índice: {exc}")
            return []

        categories = HtmlParser.parse_index(html, self._config.base_url)
        total_groups = sum(len(c.groups) for c in categories)
        logger.info(
            f"✅ Encontradas {len(categories)} categorías, {total_groups} grupos"
        )
        return categories

    def scrape_group(
        self,
        group,
        cache: "LocalCache",
        firestore: "FirestoreClient",
        dry_run: bool,
        only_jornada: int = 0,
    ) -> dict:
        stats = {
            "matches": 0,
            "individual_matches": 0,
            "matchdays": 0,
            "skipped": False,
            "errors": [],
        }

        logger.info(f"🔎 Procesando grupo {group.id}...")

        # --- Download group HTML ---
        try:
            html = self._http.fetch_text(group.source_url)
        except RuntimeError as exc:
            msg = f"No se pudo descargar HTML del grupo {group.id}: {exc}"
            logger.warning(f"   └─ ⚠️  {msg}")
            stats["errors"].append(msg)
            return stats

        html_hash = md5_str(html)

        if only_jornada:
            logger.info(f"   └─ Procesando solo jornada {only_jornada} (hash de grupo ignorado)")
        else:
            cached_hash = cache.get_group_hash(group.id)
            if cached_hash == html_hash:
                logger.info(f"   └─ Sin cambios desde última ejecución (hash coincide)")
                stats["skipped"] = True
                return stats
            logger.info(f"   └─ ⚠️  Cambios detectados, descargando jornadas nuevas...")

        # --- Parse group HTML ---
        matchdays_info, teams = HtmlParser.parse_group_page(
            html, self._config.base_url, group.id
        )

        # Save group and teams to Firestore
        try:
            firestore.save_group(
                self._config.competition_id, group.category_id, group, dry_run
            )
            if teams:
                firestore.save_teams(
                    self._config.competition_id,
                    group.category_id,
                    group.id,
                    teams,
                    dry_run,
                )
        except Exception as exc:
            logger.warning(f"   └─ Error guardando grupo/equipos en Firestore: {exc}")

        # Deduplicate matchdays: one row per match in the HTML, but all share the same PDF.
        # Keep only one entry per unique acta_url (first occurrence wins).
        seen_urls: set[str] = set()
        unique_matchdays: list[dict] = []
        for info in matchdays_info:
            if info["acta_url"] and info["acta_url"] not in seen_urls:
                seen_urls.add(info["acta_url"])
                unique_matchdays.append(info)

        # --- Process each matchday ---
        if only_jornada:
            unique_matchdays = [m for m in unique_matchdays if m["number"] == only_jornada]
            if not unique_matchdays:
                logger.warning(f"   └─ ⚠️  Jornada {only_jornada} no encontrada en el HTML del grupo")
                return stats

        for info in unique_matchdays:
            matchday_n = info["number"]
            acta_url = info["acta_url"]
            cache_key = f"{group.id}_jornada{matchday_n}"

            if not acta_url:
                logger.warning(f"   └─ ⚠️  Jornada {matchday_n} sin acta URL todavía")
                continue

            # When targeting a specific jornada, force re-download and re-process
            if only_jornada:
                pdf_filename = f"{group.id}_jornada{matchday_n}.pdf"
                pdf_file = Path(self._config.pdfs_dir) / pdf_filename
                if pdf_file.exists():
                    pdf_file.unlink()
                    logger.debug(f"   └─ PDF local eliminado para forzar re-descarga")
                cache.set_matchday_hash(cache_key, "")

            # Download PDF
            pdf_path = self._download_pdf(acta_url, group.id, matchday_n, cache)
            if not pdf_path:
                stats["errors"].append(
                    f"No se pudo descargar PDF jornada {matchday_n} del grupo {group.id}"
                )
                continue

            # Check PDF hash
            try:
                pdf_bytes = Path(pdf_path).read_bytes()
            except OSError as exc:
                stats["errors"].append(f"No se pudo leer PDF {pdf_path}: {exc}")
                continue

            pdf_hash = md5_bytes(pdf_bytes)
            cached_pdf_hash = cache.get_matchday_hash(cache_key)
            if cached_pdf_hash == pdf_hash:
                logger.debug(f"   └─ Jornada {matchday_n}: PDF sin cambios, skip")
                continue

            # Parse PDF
            logger.info(
                f"   └─ 📄 Procesando jornada {matchday_n} "
                f"(PDF: {len(pdf_bytes) // 1024}KB) ← {acta_url}"
            )
            matches = self._pdf_parser.parse_acta(pdf_path, group.id, matchday_n)

            # Count individuals
            n_individual = sum(len(m.individual_matches) for m in matches)
            logger.info(
                f"   └─ ✅ Extraídos {len(matches)} partidos, "
                f"{n_individual} partidos individuales"
            )

            # Build Matchday object
            match_date = matches[0].date if matches else None
            matchday = Matchday(
                number=matchday_n,
                date=match_date,
                pdf_url=acta_url,
                pdf_hash=pdf_hash,
                status="completed",
            )

            # Write to Firestore
            try:
                firestore.save_matchday_with_matches(
                    self._config.competition_id,
                    group.category_id,
                    group.id,
                    matchday,
                    matches,
                    dry_run,
                )
                n_ops = 1 + len(matches)
                if not dry_run:
                    logger.info(f"   └─ 💾 Guardado en Firestore (batch de {n_ops} docs)")
            except Exception as exc:
                msg = f"Error Firestore jornada {matchday_n} grupo {group.id}: {exc}"
                logger.warning(f"   └─ ⚠️  {msg}")
                stats["errors"].append(msg)
                continue

            # Update PDF hash in cache (en dry-run la caché es readonly y esto
            # solo afecta a memoria — ver LocalCache)
            cache.set_matchday_hash(cache_key, pdf_hash)

            stats["matchdays"] += 1
            stats["matches"] += len(matches)
            stats["individual_matches"] += n_individual

        # Only update group hash when processing the full group
        if not only_jornada:
            cache.set_group_hash(group.id, html_hash)

        return stats

    def _download_pdf(
        self,
        pdf_url: str,
        group_id: str,
        matchday_n: int,
        cache: "LocalCache",
    ) -> Optional[str]:
        filename = f"{group_id}_jornada{matchday_n}.pdf"
        pdfs_dir = self._config.pdfs_dir

        if cache.is_pdf_cached(filename):
            logger.debug(f"   └─ PDF {filename} ya en caché local (origen: {pdf_url})")
            return str(Path(pdfs_dir) / filename)

        logger.info(f"   └─ ⬇️  Descargando acta: {pdf_url}")
        try:
            data = self._http.fetch(pdf_url)
        except RuntimeError as exc:
            logger.warning(f"   └─ No se pudo descargar PDF {pdf_url}: {exc}")
            return None

        cache.save_pdf(filename, data, pdfs_dir)
        return str(Path(pdfs_dir) / filename)
