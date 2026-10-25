from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from models.competition import Category, Group
from models.match import Matchday
from parsers.fctt_html_parser import (
    parse_calendar_fixtures,
    parse_group_meta,
    parse_index,
    parse_jornada,
    parse_jornada_numbers,
    parse_season_label,
    parse_standings,
    parse_team_actas,
)
from scrapers.base import CompetitionScraper
from sources import FCTT, date_in_season
from utils.hashing import md5_str
from utils.logger import get_logger

if TYPE_CHECKING:
    from storage.firestore_client import FirestoreClient
    from storage.local_cache import LocalCache
    from utils.http_client import HttpClient

logger = get_logger(__name__)

_COMPETITION_ID = FCTT.id

#: Esperas entre reintentos cuando la web devuelve una página incompleta.
_RETRY_WAITS = (5, 15, 30)
_MAX_ATTEMPTS = len(_RETRY_WAITS)


def _missing_content(html: str, expect_players: bool) -> Optional[str]:
    """Qué le falta a la página para considerarla completa, o None si está bien.

    `expect_players` solo vale para las páginas de jornada. La página de grupo
    lista los encuentros de toda la temporada con su marcador pero **sin** el
    detalle de jugadores: ahí la ausencia de `codi_jugador` es normal.
    """
    if "br-apic-resultats" not in html:
        return "sin clasificación"
    if expect_players and "match-results-table" in html and "codi_jugador" not in html:
        return "actas sin enlaces de jugador"
    return None


class SeasonMismatchError(RuntimeError):
    """La web sirve una temporada distinta de la configurada en SEASON."""


class FcttScraper(CompetitionScraper):
    """Scraper de fctt.cat — TDM y Copa Catalana Femenina.

    Fuente HTML pura, sin PDFs: cada jornada es una página y su hash decide si
    hay algo nuevo que escribir, igual que en RFETM.
    """

    def __init__(self, http: "HttpClient") -> None:
        self._http = http

    def get_source_name(self) -> str:
        return "FCTT"

    # ── Índice ────────────────────────────────────────────────────────────────

    def get_index(self) -> list[Category]:
        logger.info("⬇️  Descargando índice FCTT...")
        try:
            html = self._http.fetch_text(FCTT.index_url)
        except RuntimeError as exc:
            logger.error(f"No se pudo descargar el índice FCTT: {exc}")
            return []

        categories = parse_index(html)
        total_groups = sum(len(c.groups) for c in categories)
        logger.info(f"✅ {len(categories)} categorías, {total_groups} grupos")
        return categories

    # ── Grupo ─────────────────────────────────────────────────────────────────

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
            "matchdays": 0,
            "skipped": False,
            "errors": [],
        }

        logger.info(f"🔎 Procesando grupo {group.id}...")

        html = self._fetch_valid(group.source_url)
        if html is None:
            msg = (
                f"{group.id}: la web devolvió páginas incompletas tras "
                f"{_MAX_ATTEMPTS} intentos (throttling); se reintentará en la "
                f"próxima ejecución"
            )
            logger.warning(f"   └─ ⚠️  {msg}")
            stats["errors"].append(msg)
            stats["skipped"] = True
            return stats

        self._assert_season(html, group)

        try:
            firestore.save_group(_COMPETITION_ID, group.category_id, group, dry_run)
        except Exception as exc:
            logger.warning(f"   └─ Error guardando grupo en Firestore: {exc}")

        # Clasificación: FCTT la publica en la página de grupo y en cada jornada.
        teams = parse_standings(html, group.id)
        if teams:
            try:
                firestore.save_teams(
                    _COMPETITION_ID, group.category_id, group.id, teams, dry_run
                )
                logger.info(f"   └─ 📊 {len(teams)} equipos en clasificación")
            except Exception as exc:
                logger.warning(f"   └─ Error guardando clasificación: {exc}")

        jornadas = parse_jornada_numbers(html)
        if not jornadas:
            logger.info("   └─ Sin jornadas disponibles")
            return stats
        logger.info(f"   └─ {len(jornadas)} jornadas disponibles")

        if only_jornada:
            if only_jornada not in jornadas:
                logger.warning(
                    f"   └─ ⚠️  Jornada {only_jornada} no publicada en {group.id}"
                )
                return stats
            jornadas = [only_jornada]
            logger.info(f"   └─ Procesando solo jornada {only_jornada} (hash ignorado)")

        skipped = 0
        for jornada_n in jornadas:
            jornada_url = FCTT.jornada_pattern.format(
                group_url=group.source_url.rstrip("/") + "/", n=jornada_n
            )
            cache_key = f"fctt_{group.id}_jornada{jornada_n}"

            if only_jornada:
                cache.set_matchday_hash(cache_key, "")

            html_j = self._fetch_valid(jornada_url, expect_players=True)
            if html_j is None:
                msg = (
                    f"{group.id} jornada {jornada_n}: página incompleta tras "
                    f"{_MAX_ATTEMPTS} intentos"
                )
                logger.warning(f"   └─ ⚠️  {msg}")
                stats["errors"].append(msg)
                continue

            page_hash = md5_str(html_j)
            if cache.get_matchday_hash(cache_key) == page_hash:
                skipped += 1
                continue

            matches = parse_jornada(html_j, jornada_n, group.id)
            played = [m for m in matches if m.status == "completed"]
            if not played:
                # Jornada anunciada pero todavía sin resultados. No cacheamos el
                # hash a propósito: si la página venía incompleta, la próxima
                # pasada la reintenta en vez de darla por vista para siempre.
                continue

            n_individual = sum(len(m.individual_matches) for m in played)
            walkovers = sum(1 for m in played if not m.individual_matches)
            logger.info(
                f"   └─ Jornada {jornada_n}: {len(played)} encuentros, "
                f"{n_individual} ind."
                f"{f' ({walkovers} sin acta)' if walkovers else ''}"
                f" ← {jornada_url}"
            )

            matchday = Matchday(
                number=jornada_n,
                date=next((m.date for m in played if m.date), None),
                pdf_url="",
                pdf_hash="",
                status="completed",
            )

            try:
                firestore.save_matchday_with_matches(
                    _COMPETITION_ID,
                    group.category_id,
                    group.id,
                    matchday,
                    played,
                    dry_run,
                )
                if not dry_run:
                    logger.info(f"   └─ 💾 Jornada {jornada_n} guardada")
            except Exception as exc:
                msg = f"Error Firestore jornada {jornada_n} grupo {group.id}: {exc}"
                logger.warning(f"   └─ ⚠️  {msg}")
                stats["errors"].append(msg)
                continue

            cache.set_matchday_hash(cache_key, page_hash)
            stats["matchdays"] += 1
            stats["matches"] += len(played)
            stats["individual_matches"] += n_individual

        if skipped:
            logger.info(f"   └─ {skipped} jornadas sin cambios (hash)")

        return stats

    # ── Descarga con validación ───────────────────────────────────────────────

    def _fetch_valid(self, url: str, expect_players: bool = False) -> Optional[str]:
        """Descarga hasta obtener una página completa, o devuelve None.

        fctt.cat responde HTTP 200 con la página **vacía de contenido** cuando
        se le encadenan muchas peticiones: ni tablas ni cabecera de temporada.
        Como no es un error HTTP, el reintento del HttpClient no salta y el
        scraper se creería que el grupo no tiene jornadas.

        Dos invariantes, porque la degradación tiene grados:

        1. Toda página válida —de grupo o de jornada— trae la tabla de
           clasificación.
        2. Si trae actas, sus jugadores vienen enlazados con `codi_jugador`.
           Hay un estado intermedio en el que la página sirve las tablas pero
           sin enlaces: se parsea sin error y deja los partidos sin licencia,
           que es justo lo que necesita el filtro de "Mi temporada".
        """
        for attempt, wait in enumerate(_RETRY_WAITS, start=1):
            try:
                html = self._http.fetch_text(url)
            except RuntimeError as exc:
                logger.warning(f"   └─ ⚠️  Error descargando {url}: {exc}")
                return None

            missing = _missing_content(html, expect_players)
            if missing is None:
                return html

            if attempt < _MAX_ATTEMPTS:
                logger.warning(
                    f"   └─ ⚠️  Página incompleta ({len(html) // 1024}KB, "
                    f"{missing}) — reintento {attempt}/{_MAX_ATTEMPTS - 1} "
                    f"en {wait}s: {url}"
                )
                time.sleep(wait)

        return None

    # ── Calendario (div.match-container) ──────────────────────────────────────

    def scrape_group_calendar(
        self, group: Group, cache: "LocalCache", storage, dry_run: bool, force: bool = False,
        competition_id: Optional[str] = None, derive_meta: bool = False,
    ) -> dict:
        """Carga el CALENDARIO del grupo (fixtures + resultado por equipo) desde la
        página de grupo, que trae todos los `div.match-container`. No parsea
        partidos individuales (ver parse_calendar_fixtures). Incremental por hash
        del HTML del grupo; el mismo id de encuentro funde scheduled → completed.

        `competition_id`: por defecto la FCTT estatal (fctt_*); las territoriales
        pasan su propia competición (rtbtt_*/rtg_*/rtt_*). `derive_meta`: para las
        territoriales, la categoría y el nombre del grupo se leen del `h1` (los
        slugs no los codifican); el `group.id` (el slug) se conserva.
        """
        competition_id = competition_id or _COMPETITION_ID
        stats: dict = {"matches": 0, "fixtures": 0, "matchdays": 0, "skipped": False, "errors": []}
        logger.info(f"🔎 {group.id} (calendario)...")

        html = self._fetch_valid(group.source_url)
        if html is None:
            msg = f"{group.id}: página incompleta tras {_MAX_ATTEMPTS} intentos"
            logger.warning(f"   └─ ⚠️  {msg}")
            stats["errors"].append(msg)
            stats["skipped"] = True
            return stats

        if derive_meta:
            category_id, name = parse_group_meta(html)
            group = Group(
                id=group.id, name=name or group.name,
                category_id=category_id or group.category_id,
                phase=group.phase, source_url=group.source_url,
            )

        self._assert_season(html, group)

        page_hash = md5_str(html)
        if not force and cache.get_group_hash(group.id) == page_hash:
            logger.info("   └─ Sin cambios (hash coincide)")
            stats["skipped"] = True
            return stats

        teams = parse_standings(html, group.id)
        try:
            storage.save_group(competition_id, group.category_id, group, dry_run)
            if teams:
                storage.save_teams(competition_id, group.category_id, group.id, teams, dry_run)
        except Exception as exc:
            logger.warning(f"   └─ Error guardando grupo/equipos: {exc}")

        fixtures = parse_calendar_fixtures(html, group.id)
        off = [m for m in fixtures if not date_in_season(m.date)]
        if off:
            ej = ", ".join(f"{m.id}={m.date.date()}" for m in off[:3])
            logger.warning(f"   └─ ⚠️  {len(off)} fecha(s) fuera de temporada en {group.id}: {ej}")

        by_jornada: dict[int, list] = {}
        for m in fixtures:
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
            matchday = Matchday(number=jornada, date=date, pdf_url="", pdf_hash="", status=md_status)
            try:
                storage.save_matchday_with_matches(
                    competition_id, group.category_id, group.id, matchday, ms, dry_run
                )
            except Exception as exc:
                msg = f"jornada {jornada} de {group.id}: {exc}"
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
            cache.set_group_hash(group.id, page_hash)
        return stats

    # ── Actas individuales (páginas de equipo) ────────────────────────────────

    def scrape_group_actas(
        self, group_url: str, group_id: str, competition_id: str,
        cache: "LocalCache", storage, dry_run: bool, force: bool = False,
    ) -> dict:
        """Carga los `individual_match` de un grupo desde las páginas de equipo.

        Las actas (jugadores + codi_jugador + sets) solo están en la página de cada
        equipo (`?team_name_id=`). Para no duplicar, cada encuentro se lee desde la
        página de su equipo LOCAL (ver parse_team_actas). Se visitan solo los equipos
        que ya tienen algún partido disputado como local (según el marcador del
        calendario), y cada página de equipo se cachea por hash (incremental).
        """
        stats: dict = {"encounters": 0, "individual": 0, "teams": 0, "skipped": False, "errors": []}
        logger.info(f"🔎 {group_id} (actas)...")

        html = self._fetch_valid(group_url)
        if html is None:
            msg = f"{group_id}: página incompleta tras {_MAX_ATTEMPTS} intentos"
            logger.warning(f"   └─ ⚠️  {msg}")
            stats["errors"].append(msg)
            stats["skipped"] = True
            return stats
        self._assert_season(html, Group(id=group_id, name=group_id, category_id="",
                                        phase="", source_url=group_url))

        fixtures = parse_calendar_fixtures(html, group_id)
        fix_by_j: dict[int, list] = {}
        for f in fixtures:
            fix_by_j.setdefault(f.matchday_number, []).append(f)
        home_ids = sorted({m.team_local_id for m in fixtures if m.status == "completed"})
        if not home_ids:
            logger.info("   └─ sin resultados aún")
            return stats

        collected: list = []
        for tid in home_ids:
            team_url = f"{group_url}?team_name_id={tid}"
            thtml = self._fetch_valid(team_url)
            if thtml is None:
                stats["errors"].append(f"{group_id} equipo {tid}: página incompleta")
                continue
            page_hash = md5_str(thtml)
            cache_key = f"fctt_acta_{group_id}_{tid}"
            if not force and cache.get_matchday_hash(cache_key) == page_hash:
                continue
            collected.extend(parse_team_actas(thtml, tid))
            stats["teams"] += 1
            if not dry_run:
                cache.set_matchday_hash(cache_key, page_hash)

        by_jornada: dict[int, list] = {}
        for m in collected:
            by_jornada.setdefault(m.matchday_number, []).append(m)
        for jornada in sorted(by_jornada):
            ms = by_jornada[jornada]
            jf = fix_by_j.get(jornada, [])
            comp = [x for x in jf if x.status == "completed"]
            sched = [x for x in jf if x.status == "scheduled"]
            md_status = "in_progress" if (comp and sched) else ("completed" if comp else "scheduled")
            date = (next((x.date for x in jf if x.date), None)
                    or next((x.date for x in ms if x.date), None))
            matchday = Matchday(number=jornada, date=date, pdf_url="", pdf_hash="", status=md_status)
            try:
                storage.save_matchday_with_matches(competition_id, "", group_id, matchday, ms, dry_run)
            except Exception as exc:
                msg = f"jornada {jornada} de {group_id}: {exc}"
                logger.warning(f"   └─ ⚠️  {msg}")
                stats["errors"].append(msg)
                continue
            stats["encounters"] += len(ms)
            stats["individual"] += sum(len(x.individual_matches) for x in ms)

        logger.info(
            f"   └─ 💾 {stats['teams']} equipos nuevos, {stats['encounters']} encuentros, "
            f"{stats['individual']} individuales"
        )
        return stats

    # ── Guardas ───────────────────────────────────────────────────────────────

    def _assert_season(self, html: str, group: Group) -> None:
        """Aborta si la web ya sirve otra temporada.

        Las URLs de FCTT no llevan temporada: `/lligues/grup-1/` sirve siempre la
        vigente. Sin esta comprobación, arrancar en septiembre sin haber tocado
        `SEASON` escribiría la temporada nueva sobre la anterior, que comparte
        números de jornada y por tanto las mismas rutas en Firestore.
        """
        published: Optional[str] = parse_season_label(html)
        if published is None:
            logger.warning(
                f"   └─ ⚠️  {group.id}: la página no declara temporada; "
                f"no se puede verificar que sea {FCTT.season_label}"
            )
            return
        if published != FCTT.season_label:
            raise SeasonMismatchError(
                f"{group.id}: la web publica la temporada {published} pero SEASON "
                f"está en {FCTT.season}. Actualiza SEASON en el .env antes de "
                f"escribir, o los datos nuevos machacarían competitions/{_COMPETITION_ID}."
            )
