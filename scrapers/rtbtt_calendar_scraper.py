"""Scraper del CALENDARIO de la RTBTT a partir de los PDF por grupo.

Fuente: `calendaris_{temp}.html` enlaza un PDF por grupo en `lligues{temp}/`.
Cada PDF trae clasificación + calendario (fixtures) y se va actualizando cada
semana con el resultado por EQUIPO. Emite `encounter` con `status='scheduled'`
(sin marcador) o `'completed'` (con marcador por equipo), pero **sin partidos
individuales** (esos viven en las actas, `actes_{temp}.html` → `parse_acta`).

Se apoya en el mismo hash de grupo de `LocalCache`: el hash es el md5 del PDF, así
que un PDF sin cambios se salta y uno actualizado se reprocesa. El id de encuentro
es compuesto (`{jornada}_{slugLocal}_{slugVisitante}`), estable entre semanas, de
modo que el upsert funde scheduled → completed sin duplicar.
"""
from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

from models.competition import Group
from models.match import Matchday
from parsers.rtbtt_calendar_pdf_parser import parse_calendar_pdf
from sources import RTBTT, RTBTT_CALENDAR_INDEX, RTBTT_LLIGUES_PATH, date_in_season
from utils.hashing import md5_bytes
from utils.logger import get_logger

if TYPE_CHECKING:
    from config import Config
    from storage.local_cache import LocalCache
    from utils.http_client import HttpClient

logger = get_logger(__name__)

_PDF_RE = re.compile(re.escape(RTBTT_LLIGUES_PATH) + r"/([A-Za-z0-9_]+)\.pdf", re.IGNORECASE)


def _category_of(stem: str) -> str:
    """'SEG_A_G1' → 'SEG_A'; 'PREF_G1' → 'PREF'; 'PRE_TITOL' → 'PRE_TITOL'."""
    m = re.match(r"^(.*)_G\d+$", stem)
    return m.group(1) if m else stem


class RtbttCalendarScraper:
    def __init__(self, config: "Config", http: "HttpClient") -> None:
        self._config = config
        self._http = http

    def get_source_name(self) -> str:
        return "RTBTT-CALENDARIO"

    def list_groups(self) -> list[Group]:
        """Grupos del índice de calendarios (uno por PDF de `lligues{temp}/`)."""
        logger.info("⬇️  Descargando índice de calendarios RTBTT...")
        html = self._http.fetch_text(RTBTT_CALENDAR_INDEX)
        stems: list[str] = []
        for m in _PDF_RE.finditer(html):
            stem = m.group(1)
            if stem not in stems:
                stems.append(stem)
        groups = [
            Group(
                id=stem,
                name=stem.replace("_", " "),  # se reemplaza por el título real del PDF
                category_id=_category_of(stem),
                phase="",
                source_url=f"{RTBTT.base_url}/{RTBTT_LLIGUES_PATH}/{stem}.pdf",
            )
            for stem in stems
        ]
        logger.info(f"✅ {len(groups)} grupos con calendario")
        return groups

    def scrape_group(
        self,
        group: Group,
        cache: "LocalCache",
        storage,
        dry_run: bool,
        force: bool = False,
    ) -> dict:
        stats: dict = {"matches": 0, "fixtures": 0, "matchdays": 0, "skipped": False, "errors": []}
        logger.info(f"🔎 {group.id}...")

        try:
            data = self._http.fetch(group.source_url)
        except RuntimeError as exc:
            msg = f"No se pudo descargar {group.id}: {exc}"
            logger.warning(f"   └─ ⚠️  {msg}")
            stats["errors"].append(msg)
            return stats

        pdf_hash = md5_bytes(data)
        if not force and cache.get_group_hash(group.id) == pdf_hash:
            logger.info("   └─ Sin cambios (hash del PDF coincide)")
            stats["skipped"] = True
            return stats

        try:
            title, teams, matches = parse_calendar_pdf(io.BytesIO(data))
        except Exception as exc:
            msg = f"Error parseando {group.id}: {exc}"
            logger.warning(f"   └─ ⚠️  {msg}")
            stats["errors"].append(msg)
            return stats

        if title:
            group.name = title

        # Comprobación de cordura: una fecha fuera de la temporada casi seguro es
        # un error de la fuente (mes disparatado que ni la derivación por mes salva).
        off = [m for m in matches if not date_in_season(m.date)]
        if off:
            ej = ", ".join(f"{m.id}={m.date.date()}" for m in off[:3])
            logger.warning(f"   └─ ⚠️  {len(off)} fecha(s) fuera de temporada en {group.id}: {ej}")

        # El id de encuentro debe incluir el GRUPO: los slugs de nombre no son
        # únicos entre grupos (un mismo club juega en varias categorías de
        # veteranos), así que sin el grupo el mismo emparejamiento en la misma
        # jornada colisionaría entre grupos y se perderían encuentros en el upsert.
        for m in matches:
            m.id = f"{group.id}_{m.id}"

        try:
            storage.save_group(RTBTT.id, group.category_id, group, dry_run)
            if teams:
                storage.save_teams(RTBTT.id, group.category_id, group.id, teams, dry_run)
        except Exception as exc:
            logger.warning(f"   └─ Error guardando grupo/equipos: {exc}")

        by_jornada: dict[int, list] = {}
        for m in matches:
            by_jornada.setdefault(m.matchday_number, []).append(m)

        for jornada in sorted(by_jornada):
            ms = by_jornada[jornada]
            completed = [x for x in ms if x.status == "completed"]
            scheduled = [x for x in ms if x.status == "scheduled"]
            date = next((x.date for x in ms if x.date), None)
            if completed and scheduled:
                md_status = "in_progress"
            elif completed:
                md_status = "completed"
            else:
                md_status = "scheduled"
            matchday = Matchday(
                number=jornada, date=date, pdf_url=group.source_url,
                pdf_hash=pdf_hash, status=md_status,
            )
            try:
                storage.save_matchday_with_matches(
                    RTBTT.id, group.category_id, group.id, matchday, ms, dry_run
                )
            except Exception as exc:
                msg = f"Error guardando jornada {jornada} de {group.id}: {exc}"
                logger.warning(f"   └─ ⚠️  {msg}")
                stats["errors"].append(msg)
                continue
            stats["matchdays"] += 1
            stats["matches"] += len(completed)
            stats["fixtures"] += len(scheduled)

        logger.info(
            f"   └─ 💾 {group.name}: {stats['matches']} jugados, "
            f"{stats['fixtures']} de calendario, {len(teams)} equipos"
        )
        if not dry_run:
            cache.set_group_hash(group.id, pdf_hash)
        return stats
