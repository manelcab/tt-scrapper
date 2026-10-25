from __future__ import annotations

from typing import TYPE_CHECKING

from models.competition import Category, Group
from models.match import Matchday
from parsers.rfetm_html_parser import (
    RFETM_INDEX,
    parse_fixtures,
    parse_index,
    parse_matches,
    parse_max_jornada,
    parse_standings,
)
from scrapers.base import CompetitionScraper
from sources import RFETM, date_in_season
from utils.hashing import md5_str
from utils.logger import get_logger

if TYPE_CHECKING:
    from storage.firestore_client import FirestoreClient
    from storage.local_cache import LocalCache
    from utils.http_client import HttpClient

logger = get_logger(__name__)

_COMPETITION_ID = RFETM.id


class RfetmScraper(CompetitionScraper):
    def __init__(self, http: "HttpClient") -> None:
        self._http = http

    def get_source_name(self) -> str:
        return "RFETM"

    def get_index(self) -> list[Category]:
        logger.info("⬇️  Descargando índice RFETM...")
        try:
            html = self._http.fetch_text(RFETM_INDEX)
        except RuntimeError as exc:
            logger.error(f"No se pudo descargar el índice RFETM: {exc}")
            return []
        categories = parse_index(html)
        total_groups = sum(len(c.groups) for c in categories)
        logger.info(f"✅ {len(categories)} categorías, {total_groups} grupos")
        return categories

    def scrape_group(
        self,
        group: Group,
        cache: "LocalCache",
        firestore: "FirestoreClient",
        dry_run: bool,
        only_jornada: int = 0,
    ) -> dict:
        stats: dict = {
            "matches": 0,
            "individual_matches": 0,
            "fixtures": 0,
            "matchdays": 0,
            "skipped": False,
            "errors": [],
        }

        logger.info(f"🔎 Procesando grupo {group.id}...")

        # Fetch standings page (jornada=0)
        try:
            html0 = self._http.fetch_text(group.source_url)
        except RuntimeError as exc:
            msg = f"Error descargando {group.id}: {exc}"
            logger.warning(f"   └─ ⚠️  {msg}")
            stats["errors"].append(msg)
            return stats

        # Save group metadata
        try:
            firestore.save_group(_COMPETITION_ID, group.category_id, group, dry_run)
        except Exception as exc:
            logger.warning(f"   └─ Error guardando grupo en Firestore: {exc}")

        # Parse and save standings
        teams = parse_standings(html0)
        if teams:
            try:
                firestore.save_teams(
                    _COMPETITION_ID, group.category_id, group.id, teams, dry_run
                )
                logger.info(f"   └─ 📊 {len(teams)} equipos en clasificación")
            except Exception as exc:
                logger.warning(f"   └─ Error guardando clasificación: {exc}")

        # Discover available jornadas
        max_jornada = parse_max_jornada(html0)
        if max_jornada == 0:
            logger.info(f"   └─ Sin jornadas disponibles")
            return stats

        logger.info(f"   └─ {max_jornada} jornadas disponibles")

        if only_jornada:
            if only_jornada > max_jornada:
                logger.warning(
                    f"   └─ ⚠️  Jornada {only_jornada} solicitada pero solo hay {max_jornada}"
                )
                return stats
            jornada_range: list[int] | range = [only_jornada]
            logger.info(f"   └─ Procesando solo jornada {only_jornada} (hash ignorado)")
        else:
            jornada_range = range(1, max_jornada + 1)

        new_jornadas = 0
        skipped_jornadas = 0

        for jornada_n in jornada_range:
            jornada_url = group.source_url.replace(
                "jornada=0", f"jornada={jornada_n}"
            )
            cache_key = f"rfetm_{group.id}_jornada{jornada_n}"

            if only_jornada:
                cache.set_matchday_hash(cache_key, "")

            try:
                html_j = self._http.fetch_text(jornada_url)
            except RuntimeError as exc:
                msg = f"Error descargando jornada {jornada_n} de {group.id}: {exc}"
                logger.warning(f"   └─ ⚠️  {msg}")
                stats["errors"].append(msg)
                continue

            page_hash = md5_str(html_j)
            if cache.get_matchday_hash(cache_key) == page_hash:
                skipped_jornadas += 1
                continue

            matches = parse_matches(html_j, jornada_n)
            completed = [m for m in matches if m.status == "completed"]

            # Partidos de calendario aún no jugados (para "próximos partidos").
            # Comparten id compuesto con su versión ya disputada, así que se
            # descartan los que ya aparecen como completed en esta misma jornada.
            played_ids = {m.id for m in completed}
            scheduled = [f for f in parse_fixtures(html_j, jornada_n) if f.id not in played_ids]
            to_save = completed + scheduled

            if not to_save:
                if not dry_run:
                    cache.set_matchday_hash(cache_key, page_hash)
                continue

            # Comprobación de cordura: fechas fuera de la temporada = error de la
            # fuente (la RFETM publica algún encuentro con el año mal, p. ej. 2031).
            off = [m for m in to_save if not date_in_season(m.date)]
            if off:
                ej = ", ".join(f"{m.id}={m.date.date()}" for m in off[:3])
                logger.warning(f"   └─ ⚠️  {len(off)} fecha(s) fuera de temporada en {group.id} J{jornada_n}: {ej}")

            n_individual = sum(len(m.individual_matches) for m in completed)
            logger.info(
                f"   └─ Jornada {jornada_n}: {len(completed)} disputados, "
                f"{len(scheduled)} de calendario, {n_individual} ind. ← {jornada_url}"
            )

            # Fecha de referencia y estado de la jornada: jugada, por jugar o mixta.
            match_date = (
                next((m.date for m in completed if m.date), None)
                or next((f.date for f in scheduled if f.date), None)
            )
            if completed and scheduled:
                md_status = "in_progress"
            elif completed:
                md_status = "completed"
            else:
                md_status = "scheduled"
            matchday = Matchday(
                number=jornada_n,
                date=match_date,
                pdf_url="",
                pdf_hash="",
                status=md_status,
            )

            try:
                firestore.save_matchday_with_matches(
                    _COMPETITION_ID,
                    group.category_id,
                    group.id,
                    matchday,
                    to_save,
                    dry_run,
                )
                logger.info(f"   └─ 💾 Jornada {jornada_n} guardada")
            except Exception as exc:
                msg = f"Error Firestore jornada {jornada_n} grupo {group.id}: {exc}"
                logger.warning(f"   └─ ⚠️  {msg}")
                stats["errors"].append(msg)
                continue

            if not dry_run:
                cache.set_matchday_hash(cache_key, page_hash)
            new_jornadas += 1
            stats["matchdays"] += 1
            stats["matches"] += len(completed)
            stats["individual_matches"] += n_individual
            stats["fixtures"] += len(scheduled)

        if skipped_jornadas > 0:
            logger.info(f"   └─ {skipped_jornadas} jornadas sin cambios (hash)")

        return stats
