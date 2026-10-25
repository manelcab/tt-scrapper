from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from models.rfetm_ranking import RfetmEdition, RfetmEntry
from parsers.rfetm_ranking_parser import (
    RfetmEditionRef,
    RfetmFormatError,
    parse_index,
    parse_ranking,
)
from sources import RFETM, RFETM_RANKING_URL, RFETM_TEMPORADA
from utils.hashing import md5_bytes
from utils.logger import get_logger

if TYPE_CHECKING:
    from storage.local_cache import LocalCache
    from utils.http_client import HttpClient

logger = get_logger(__name__)

_TOP_SIZE = 100

_SEXES = ("M", "F")


class RfetmRankingsScraper:
    """Rànquings mensuales de la RFETM.

    Una petición por sexo y mes: `accion=3` devuelve el ranking global con la
    posición del jugador en las 14 categorías, así que los rànquings por
    categoría se **derivan** en vez de descargarse. La temporada entera son 20
    peticiones.
    """

    def __init__(self, http: "HttpClient") -> None:
        self._http = http

    # ── Índice ────────────────────────────────────────────────────────────────

    def get_index(self) -> list[RfetmEditionRef]:
        logger.info("⬇️  Descargando índice de rànquings RFETM...")
        try:
            html = self._http.fetch_text(RFETM.rankings_index)
        except RuntimeError as exc:
            logger.error(f"No se pudo descargar el índice: {exc}")
            return []

        refs = parse_index(html, RFETM_TEMPORADA)
        logger.info(
            f"✅ {len(refs)} meses publicados en la temporada {RFETM.season} "
            f"(temporada={RFETM_TEMPORADA})"
        )
        return refs

    # ── Descarga y parseo ─────────────────────────────────────────────────────

    def scrape_edition(
        self,
        ref: RfetmEditionRef,
        cache: "LocalCache",
        force: bool = False,
    ) -> tuple[Optional[RfetmEdition], dict]:
        """Descarga y parsea un mes completo — las dos tablas, M y F.

        Los hashes se devuelven en `stats["hashes"]` para que el llamador los
        confirme solo si la escritura ha ido bien.
        """
        stats = {"players": 0, "skipped": 0, "errors": [], "hashes": {}}
        edition = RfetmEdition(
            id=ref.id, year=ref.year, month=ref.month, label=ref.label
        )

        logger.info(f"🔎 {ref.id} — {ref.label}")

        for sex in _SEXES:
            url = RFETM_RANKING_URL.format(
                year=ref.year, mes=ref.month, sexo=sex, temporada=ref.temporada
            )
            try:
                html = self._http.fetch_text(url)
            except RuntimeError as exc:
                stats["errors"].append(f"{ref.id} {sex}: no se pudo descargar ({exc})")
                continue

            cache_key = f"rfetm_rk_{ref.id}_{sex}"
            page_hash = md5_bytes(html.encode())
            if not force and cache.get_matchday_hash(cache_key) == page_hash:
                stats["skipped"] += 1
                logger.debug(f"   └─ {sex} sin cambios (hash)")
                continue

            try:
                entries = parse_ranking(html, sex)
            except RfetmFormatError as exc:
                stats["errors"].append(f"{ref.id} {sex}: {exc}")
                logger.warning(f"   └─ ⚠️  {exc}")
                continue

            if not entries:
                stats["errors"].append(f"{ref.id} {sex}: 0 filas parseadas")
                logger.warning(f"   └─ ⚠️  {ref.id} {sex}: 0 filas")
                continue

            edition.entries.extend(entries)
            stats["hashes"][cache_key] = page_hash
            stats["players"] += len(entries)
            logger.info(f"   └─ 📄 {sex}: {len(entries)} jugadores")

        return (edition if edition.entries else None), stats


# ── Construcción de documentos ────────────────────────────────────────────────


def category_key(category: str, sex: str) -> str:
    """`NAC` + `M` → `NAC_M`.

    **El sexo tiene que formar parte de la clave.** Las categorías de la RFETM
    (`NAC`, `SEN`, `V40`…) son las mismas para hombres y mujeres, así que sin él
    `orderBy('current.NAC.pos')` mezcla las dos listas y devuelve dos primeros.
    Separarlo con `where('sex','==','M')` exigiría un índice compuesto por cada
    categoría; metiéndolo en la clave, no hace falta ninguno.

    El separador es `_` y no `-` a propósito: un guion en una ruta de campo hay
    que escaparlo con acentos graves, y sin escapar la consulta falla.
    """
    return f"{category}_{sex}"


def build_player_documents(editions: list[RfetmEdition]) -> dict[str, dict]:
    """Funde todas las ediciones en un documento por jugador.

    Las ediciones tienen que venir de la más antigua a la más reciente: la
    última es la que define `current`.
    """
    players: dict[str, dict] = {}
    ultima = editions[-1].id if editions else ""

    for edition in editions:
        for entry in edition.entries:
            doc = players.setdefault(
                entry.lic, {"lic": entry.lic, "current": {}, "history": {}}
            )
            # Datos de nivel jugador: gana el más reciente, que es el bueno si
            # ha cambiado de club.
            doc["name"] = entry.name
            doc["sex"] = entry.sex
            if entry.club:
                doc["club"] = entry.club
            if entry.fa:
                doc["fa"] = entry.fa
            if entry.cat:
                doc["cat"] = entry.cat
            if entry.birth_year:
                doc["birth_year"] = entry.birth_year

            doc["history"][edition.id] = entry.to_history()

            # `current` **solo** se llena desde la última edición. Si se fuera
            # sobreescribiendo edición a edición, un jugador que dejó de estar
            # en el ranking conservaría para siempre su última posición y
            # aparecería como nº1 vigente meses después de haber desaparecido.
            # Quien no está en el ranking del mes se queda sin `current`, que es
            # justo lo que significa.
            if edition.id == ultima:
                doc["current"] = {
                    category_key(category, entry.sex): {
                        "pos": pos,
                        "pts": entry.pts,
                        "edition": edition.id,
                    }
                    for category, pos in entry.positions.items()
                }

    ahora = datetime.now(timezone.utc).isoformat()
    for doc in players.values():
        doc["updated_at"] = ahora
    return players


def build_category_documents(edition: RfetmEdition) -> list[tuple[str, dict]]:
    """Un documento por categoría y sexo, con su `top`.

    No se descarga nada: las posiciones ya vienen en la tabla global, así que
    los 28 rànquings del mes se derivan ordenando.
    """
    por_categoria: dict[tuple[str, str], list[RfetmEntry]] = {}
    for entry in edition.entries:
        for category in entry.positions:
            por_categoria.setdefault((category, entry.sex), []).append(entry)

    docs = []
    for (category, sex), entries in sorted(por_categoria.items()):
        ordenados = sorted(entries, key=lambda e: e.positions[category])
        category_id = category_key(category, sex)
        docs.append(
            (
                category_id,
                {
                    "id": category_id,
                    "category": category,
                    "sex": sex,
                    "player_count": len(entries),
                    "top": [e.to_top(category) for e in ordenados[:_TOP_SIZE]],
                },
            )
        )
    return docs
