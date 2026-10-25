"""Parser de los rànquings de la RFETM (HTML).

`https://rfetm.es/ranking/?accion=…` con `year`, `mes`, `sexo` y `temporada`:

    accion=0   índice: todos los meses publicados de todas las temporadas
    accion=3   ranking global del mes — el que usamos
    accion=4   ranking de una categoría (redundante: se deriva de accion=3)
    accion=5   partidos de un jugador en ese mes (fase 2)

`accion=3` devuelve una tabla de 31 columnas con una fila por jugador:

    LIC · NOMBRE · CLUB · FA · CAT · AÑO · ANT · ACT ·
    LLNN · C.ESP · T.EST · TOP · SP.M · PJ · PG · PP · CLAF ·
    NAC · SEN · S21 · JUV · CAD · INF · ALE · BEN · V40 · V50 · V60 · V65 · V70 · V75

**La cabecera de la tabla viene rota**: cada `<th>` contiene la concatenación de
todos los encabezados que quedan a su derecha (`'NAC LIC. NOMBRE CLUB…'`). No se
puede parsear por nombre de columna, así que se va por posición y se comprueba
el ancho: si la fuente cambia de formato, es mejor abortar que colocar los datos
en el campo equivocado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from models.rfetm_ranking import CATEGORIES, NOT_RANKED, RfetmEntry
from utils.logger import get_logger

logger = get_logger(__name__)

_COLUMNS = 31

#: Primera columna de posición por categoría.
_FIRST_CATEGORY = 17

_MONTHS = {
    1: "gener", 2: "febrer", 3: "març", 4: "abril", 5: "maig", 6: "juny",
    7: "juliol", 8: "agost", 9: "setembre", 10: "octubre", 11: "novembre",
    12: "desembre",
}


class RfetmFormatError(RuntimeError):
    """La tabla no tiene la forma esperada — mejor parar que inventar."""


@dataclass(frozen=True)
class RfetmEditionRef:
    """Un mes publicado, tal y como lo anuncia el índice."""

    id: str            # "2026-06"
    year: int
    month: int
    temporada: int

    @property
    def label(self) -> str:
        return f"{_MONTHS.get(self.month, self.month)} {self.year}"


# ── Índice ────────────────────────────────────────────────────────────────────


def parse_index(html: str, temporada: int) -> list[RfetmEditionRef]:
    """Meses publicados de una temporada, del más antiguo al más reciente.

    El índice lista las dos temporadas disponibles mezcladas, así que se filtra
    por la que interesa. `mes=0`, cuando existe, es el ranking inicial.
    """
    soup = BeautifulSoup(html, "html.parser")
    encontrados: dict[str, RfetmEditionRef] = {}

    for a in soup.find_all("a", href=True):
        m = re.search(
            r"accion=2&year=(\d+)&mes=(\d+)&sexo=\w&temporada=(\d+)", a["href"]
        )
        if not m:
            continue
        year, mes, temp = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if temp != temporada:
            continue
        ref = RfetmEditionRef(
            id=f"{year}-{mes:02d}", year=year, month=mes, temporada=temp
        )
        encontrados.setdefault(ref.id, ref)

    return sorted(encontrados.values(), key=lambda r: (r.year, r.month))


# ── Ranking ───────────────────────────────────────────────────────────────────


def parse_ranking(html: str, sex: str) -> list[RfetmEntry]:
    """Filas del ranking global de un mes (`accion=3`)."""
    soup = BeautifulSoup(html, "html.parser")
    tablas = soup.find_all("table")
    if not tablas:
        raise RfetmFormatError("La página no trae ninguna tabla")

    entries: list[RfetmEntry] = []
    descartadas = 0

    for tr in tablas[-1].find_all("tr"):
        celdas = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if not celdas or not celdas[0].isdigit():
            continue                       # cabecera o fila vacía
        if len(celdas) != _COLUMNS:
            descartadas += 1
            continue
        entries.append(_entry_from(celdas, sex))

    if descartadas:
        raise RfetmFormatError(
            f"{descartadas} filas con un número de columnas distinto de "
            f"{_COLUMNS}: la fuente ha cambiado de formato"
        )
    return entries


def _entry_from(c: list[str], sex: str) -> RfetmEntry:
    # 0 LIC · 1 NOMBRE · 2 CLUB · 3 FA · 4 CAT · 5 AÑO · 6 ANT · 7 ACT
    # 8 LLNN · 9 C.ESP · 10 T.EST · 11 TOP · 12 SP.M · 13 PJ · 14 PG · 15 PP
    # 16 CLAF · 17..30 posición en cada categoría
    by_event = {
        clave: _float(c[i])
        for clave, i in (
            ("llnn", 8), ("c_esp", 9), ("t_est", 10), ("top", 11), ("sp_m", 12)
        )
        if _float(c[i])
    }

    positions = {}
    for offset, category in enumerate(CATEGORIES):
        pos = _int(c[_FIRST_CATEGORY + offset])
        # `99999` significa "no compite en esta categoría". Guardarlo sería
        # decir que es el 99.999º, que no es lo mismo que no estar.
        if pos and pos != NOT_RANKED:
            positions[category] = pos

    return RfetmEntry(
        lic=c[0],
        name=c[1],
        club=c[2],
        fa=c[3].strip("-"),
        cat=c[4],
        birth_year=_int(c[5]),
        sex=sex,
        pts_prev=_float(c[6]),
        pts=_float(c[7]),
        pj=_int(c[13]),
        pg=_int(c[14]),
        pp=_int(c[15]),
        by_event=by_event,
        positions=positions,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _float(text: str) -> float:
    """La RFETM usa punto decimal, al revés que la FCTT."""
    try:
        return float(text)
    except (ValueError, AttributeError):
        return 0.0


def _int(text: str) -> int:
    try:
        return int(text)
    except (ValueError, TypeError):
        return 0
