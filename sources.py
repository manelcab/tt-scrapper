"""Única fuente de verdad de las fuentes de scraping.

Todo lo que sea "dónde vive el dato en internet y cómo se llama en Firestore"
se declara aquí: dominios, índices, plantillas de URL de jornada e id de
competición. El resto del código importa de este módulo y no escribe nunca una
URL a mano.

La temporada llega del `.env` (`SEASON`). Cargamos el `.env` aquí mismo porque
este módulo lo importan otros que se cargan antes de que `Config.from_env()`
haya corrido; `load_dotenv` es idempotente y no pisa lo que ya esté en el
entorno, así que llamarlo dos veces es inofensivo.

Cambiar de temporada = cambiar `SEASON` en el `.env`. Eso mueve a la vez las
URLs y los ids de competición, de forma que la temporada anterior queda
intacta en Firestore bajo su propio `competitions/{id}`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv(override=False)

SEASON = os.getenv("SEASON", "2025-2026")          # "2025-2026"
SEASON_SHORT = f"{SEASON[2:4]}{SEASON[-2:]}"       # "2526"
SEASON_START_YEAR = int(SEASON[:4])                # 2025


def season_year_for_month(month: int) -> int:
    """Año natural de un mes dentro de la temporada, que cruza el cambio de año:
    septiembre–diciembre → año de inicio; enero–agosto → año siguiente.

    Se usa para DERIVAR el año de una fecha de calendario a partir del mes, en vez
    de fiarse del que imprime la fuente: la RTBTT (y a veces la RFETM) lo teclean
    mal —p. ej. la última jornada de veteranos fechada '17/02/26' cuando es 2027, o
    encuentros de RFETM con '2031'—. Como una temporada solo cruza un cambio de
    año, el mes determina el año sin ambigüedad para todo el calendario de liga.
    """
    return SEASON_START_YEAR if month >= 8 else SEASON_START_YEAR + 1


def date_in_season(dt) -> bool:
    """¿Cae la fecha dentro de la ventana plausible de la temporada?
    [1-sep del año de inicio, 1-jul del siguiente]. Sirve como comprobación de
    cordura al cargar: una fecha fuera de esta ventana es casi seguro un error de
    la fuente (mes disparatado) que conviene avisar en vez de escribir en silencio.
    """
    from datetime import datetime
    if dt is None:
        return True  # fecha no publicada: es válido (se guarda NULL)
    return datetime(SEASON_START_YEAR, 9, 1) <= dt < datetime(SEASON_START_YEAR + 1, 7, 1)


# Meses en catalán, como los titula la FCTT ("Setembre 2026/2027"). Se aceptan
# variantes sin diacrítico (MARC/MARÇ) por robustez ante el OCR/tecleo.
_CATALAN_MONTHS: dict[str, int] = {
    "GENER": 1, "FEBRER": 2, "MARÇ": 3, "MARC": 3, "ABRIL": 4, "MAIG": 5,
    "JUNY": 6, "JULIOL": 7, "AGOST": 8, "SETEMBRE": 9, "OCTUBRE": 10,
    "NOVEMBRE": 11, "DESEMBRE": 12,
}


def parse_ranking_label(label: str) -> tuple[Optional[int], Optional[int]]:
    """De la etiqueta de una edición de ranking FCTT ("Setembre 2026/2027") saca
    `(year, month)` en enteros, o `(None, None)` si no se reconoce.

    El mes sale del nombre catalán; el año, del año de temporada del propio label
    aplicando la misma regla que `season_year_for_month` (mes ≥ agosto → primer
    año de la temporada; si no, el siguiente). Se deriva del label —no del entorno—
    para que un backfill de ediciones de otras temporadas también acierte.

    La FCTT publica el mes como nombre y no imprime el año natural, así que sin
    esto `ranking_edition.year`/`month` quedan a NULL (a diferencia de la RFETM, que
    sí los trae). Ver docs/PARA-KAYZEN-licencias.
    """
    if not label:
        return None, None
    tokens = label.strip().split()
    month = _CATALAN_MONTHS.get(tokens[0].upper()) if tokens else None
    m_year = re.search(r"\b(\d{4})\b", label)
    if month is None or m_year is None:
        return None, None
    start = int(m_year.group(1))
    year = start if month >= 8 else start + 1
    return year, month


@dataclass(frozen=True)
class SourceDef:
    """Una competición scrapeable de una temporada concreta."""

    id: str                      # id del documento en `competitions/{id}`
    name: str
    organization: str
    season: str
    base_url: str
    index_url: str

    #: Plantilla para construir la URL de una jornada concreta. Vacía si la
    #: fuente no publica las jornadas por URL (RTBTT las sirve como PDF).
    jornada_pattern: str = ""

    #: Segmento de ruta donde la fuente publica las actas, cuando aplica.
    acta_path: str = ""

    #: Cómo escribe la fuente la temporada en sus páginas. Sirve para que el
    #: scraper aborte si la web ya sirve una temporada distinta de la
    #: configurada — imprescindible cuando la URL no lleva la temporada.
    season_label: str = ""

    #: Índice de rànquings, cuando la fuente publica uno.
    rankings_index: str = ""


RTBTT = SourceDef(
    id=f"rtbtt_{SEASON_SHORT}",
    name=f"Liga Territorial Barcelona {SEASON}",
    organization="RTBTT",
    season=SEASON,
    base_url="https://www.rtbtt.com",
    index_url=f"https://www.rtbtt.com/actes_{SEASON_SHORT}.html",
    acta_path=f"actes{SEASON_SHORT}",
)

#: Índice de CALENDARIOS de la RTBTT (distinto del de actas `index_url`): enlaza
#: un PDF por grupo en `lligues{temp}/`. Los PDF traen fixtures + resultado por
#: equipo (se actualizan cada semana), no partidos individuales. Se publica antes
#: que las actas, así que es la vía para el calendario de la temporada nueva.
RTBTT_CALENDAR_INDEX = f"https://www.rtbtt.com/calendaris_{SEASON_SHORT}.html"
RTBTT_LLIGUES_PATH = f"lligues{SEASON_SHORT}"

#: La RFETM numera las temporadas con un contador propio: la 2025-2026 es la 11.
#: Comprobado también con la 2024-2025 (`temporada=10`), que es la más antigua
#: publicada.
RFETM_TEMPORADA = int(SEASON[:4]) - 2014

RFETM = SourceDef(
    id=f"rfetm_{SEASON_SHORT}",
    name=f"RFETM Liga Nacional {SEASON}",
    organization="RFETM",
    season=SEASON,
    base_url=f"https://www.rfetm.es/public/resultados/{SEASON}",
    index_url=f"https://www.rfetm.es/public/resultados/{SEASON}/index.php",
    jornada_pattern="jornada={n}",
    # `accion=0` lista todos los meses publicados de todas las temporadas; de
    # ahí sale qué hay que descargar.
    rankings_index="https://rfetm.es/ranking/?accion=0",
)

#: Ranking global de un mes y un sexo, con **todo** dentro: identidad, año de
#: nacimiento, puntos anterior y actual, desglose por competición, PJ/PG/PP y la
#: posición en las 14 categorías. Una petición por sexo y mes basta; las URLs
#: por categoría (`accion=4`) son redundantes porque sus rànquings se derivan
#: ordenando por la columna correspondiente.
RFETM_RANKING_URL = (
    "https://rfetm.es/ranking/?accion=3"
    "&year={year}&mes={mes}&sexo={sexo}&temporada={temporada}"
)

#: Partidos de un jugador en un mes. Fase 2, todavía sin cargar.
RFETM_PLAYER_MATCHES_URL = (
    "https://rfetm.es/ranking/?accion=5"
    "&jugador={lic}&year={year}&mes={mes}&sexo={sexo}&temporada={temporada}"
)

FCTT = SourceDef(
    id=f"fctt_{SEASON_SHORT}",
    name=f"Lligues FCTT {SEASON}",
    organization="FCTT",
    season=SEASON,
    base_url="https://fctt.cat",
    index_url="https://fctt.cat/competicions-estatals/",
    jornada_pattern="{group_url}?jornada={n}",
    # La URL de FCTT no lleva la temporada: la misma dirección sirve la
    # temporada en curso. Sin esta comprobación, no bumpear SEASON a tiempo
    # machacaría la temporada anterior en Firestore (mismos números de jornada).
    season_label=SEASON.replace("-", "/"),
    rankings_index="https://fctt.cat/ranquings/",
)

SOURCES: dict[str, SourceDef] = {s.id: s for s in (RTBTT, RFETM, FCTT)}


# ─────────────────────────────────────────── ligas territoriales en fctt.cat
# La FCTT ha integrado en su web (mismo sistema que TDM: /lligues/<slug>/) los
# calendarios y actas de las representaciones territoriales. Barcelona (RTB) es
# la que antes se cargaba de rtbtt.com; ahora sale de aquí, con el detalle
# individual que el PDF no daba.
#
# Los slugs NO codifican de forma fiable la categoría/grupo (la FCTT los generó
# irregulares: p. ej. `rtb-seg-a-2` es el GRUPO 1). Por eso la categoría y el
# nombre se derivan del `h1` de cada página en el scrapeo, y el `group_id` es el
# propio slug (único y estable por URL). Si la FCTT corrige los slugs, hay que
# actualizar esta lista (los ids de grupo cambiarían).
@dataclass(frozen=True)
class TerritorialDef:
    key: str                 # 'rtb' | 'rtg' | 'rtt'
    id: str                  # competición: 'rtbtt_2627' | 'rtg_2627' | 'rtt_2627'
    name: str
    organization: str
    slugs: tuple[str, ...]


FCTT_TERRITORIALS: dict[str, TerritorialDef] = {
    "rtb": TerritorialDef(
        key="rtb",
        id=f"rtbtt_{SEASON_SHORT}",   # continuidad con rtbtt_2526 (antes de rtbtt.com)
        name=f"Lliga RTB {SEASON}",
        organization="RTBTT",
        slugs=(
            "rtb-pref", "rtb-pref-2", "rtb-pref-3",
            "rtb-prim", "rtb-prim-2", "rtb-prim-3",
            "rtb-seg-a-2", "rtb-seg-a", "rtb-seg-a-2-2",
            "rtb-seg-b", "rtb-seg-b-2", "rtb-seg-b-3",
            "rtb-ter-a", "rtb-ter-a-2", "rtb-ter-a-3",
            "rtb-ter-b", "rtb-ter-b-2", "rtb-ter-b-3",
            "rtb-1a-com", "rtb-1a-com-2", "rtb-1a-com-3",
            "rtb-2a-com", "rtb-2a-com-2",
            "rtb-vet-1a", "rtb-vet-1a-2",
            "rtb-vet-2aa", "rtb-vet-2aa-2",
            "rtb-vet-2ab", "rtb-vet-2ab-2",
            "rtb-vet-3aa", "rtb-vet-3aa-2",
            "rtb-vet-3ab", "rtb-vet-3ab-2",
            "rtb-vet-4aa", "rtb-vet-4aa-2",
            "rtb-vet-4ab", "rtb-vet-4ab-2",
            "rtb-vet-4ac", "rtb-vet-4ac-2", "rtb-vet-4ac-3",
        ),
    ),
    "rtg": TerritorialDef(
        key="rtg",
        id=f"rtg_{SEASON_SHORT}",
        name=f"Lliga RTG (Girona) {SEASON}",
        organization="RTG",
        slugs=("rtg-preferent", "rtg-primera", "rtg-segona", "rtg-tercera"),
    ),
    "rtt": TerritorialDef(
        key="rtt",
        id=f"rtt_{SEASON_SHORT}",
        name=f"Lliga RTT (Tarragona) {SEASON}",
        organization="RTT",
        slugs=(
            "preferent-rtt", "primera-provincial-rtt", "segona-provincial-rtt",
            "tercera-provincial-rtt", "quarta-provincial-rtt",
        ),
    ),
}


def territorial_group_url(slug: str) -> str:
    return f"{FCTT.base_url}/lligues/{slug}/"
