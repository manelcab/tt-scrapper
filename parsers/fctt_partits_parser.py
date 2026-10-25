"""Parser de `Partits_Jugats` de la FCTT (PDF).

Es el histórico partido a partido que acompaña a cada revisión del ranking:
`RK{temporada}_{edición}_Partits_Jugats.pdf`, publicado en el mismo
`/ranquings/`. Tabla única de 14 columnas:

    DATA · EVENT · CATEGORIA · LLIC 1 · JUG 1 (NOM) · JUG 2 (NOM) ·
    TOT JUG1 · TOT JUG2 · rk 1 · rk 2 · ΔRK1 · ΔRK2 · RD'1 · RD'2

Dos cosas que hay que tener presentes al leerlo:

**Es incremental.** Cada edición cubre solo el periodo desde la anterior
(la 10 va del 01/06 al 13/06/2026) y las ediciones no se solapan: la
intersección entre consecutivas es exactamente 0 filas. La unión de las 9 es la
temporada.

**Solo hay licencia del jugador 1.** El rival viene con nombre y nada más. Se
recupera del propio PDF porque cada partido aparece **dos veces**, una desde
cada jugador; en la fila espejo el rival es `JUG 1` y trae su licencia. Ver
`resolve_opponent_codes`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import pdfplumber
from bs4 import BeautifulSoup

from parsers.fctt_ranking_pdf_parser import _absolute, _float, _int
from sources import SEASON_SHORT
from utils.logger import get_logger

logger = get_logger(__name__)

#: `RK2526_06_Partits_Jugats-1.pdf` — el sufijo `-1` lo añade WordPress al
#: resubir un fichero con el mismo nombre; el contenido es el mismo.
_PARTITS_RE = re.compile(
    r"^RK(\d{4})_(\d{2})_Partits_Jugats(?:-\d+)?\.pdf$", re.IGNORECASE
)

_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")

_COLUMNS = 14

#: Eventos que son liga y no torneo. Comprobado contra el vocabulario completo
#: de la temporada 2025-2026: 64 eventos distintos, de los que estos tres
#: prefijos cubren todas las ligas —`LLIGA J*` (la de la FCTT), `Lliga RTB J*`
#: y `Lliga RTB Play-Off` (RTBTT), `LLNN J*` y `ESTATAL` (RFETM)— y ninguna
#: otra cosa. El resto son opens, circuits, campionats i copes.
_LEAGUE_PREFIXES = ("LLIGA", "LLNN", "ESTATAL")


@dataclass(frozen=True)
class PartitsRef:
    """Un PDF de `Partits_Jugats` anunciado en el índice."""

    number: str          # "02".."10"
    label: str           # "Juliol 2025/2026"
    url: str


@dataclass(frozen=True)
class RawRow:
    """Una fila del PDF, tal cual, ya tipada.

    `licence` es la del jugador 1 y es la única que trae la fuente; la del
    rival se resuelve después con `resolve_opponent_codes`.
    """

    date: str            # ISO: "2026-06-02"
    event: str
    category: str
    licence: str
    name: str
    opponent_name: str
    sets: int
    opp_sets: int
    rk: float
    opp_rk: float
    d_rk: float
    opp_d_rk: float
    rd: float
    opp_rd: float


# ── Índice ────────────────────────────────────────────────────────────────────


def parse_index(html: str) -> list[PartitsRef]:
    """PDFs de `Partits_Jugats` publicados, uno por edición."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, PartitsRef] = {}

    for h3 in soup.find_all("h3"):
        label = h3.get_text(" ", strip=True)
        for node in h3.find_all_next(["h3", "a"]):
            if node.name == "h3":
                break
            href = node.get("href", "")
            match = _PARTITS_RE.match(href.rsplit("/", 1)[-1])
            if not match:
                continue
            if match.group(1) != SEASON_SHORT:
                continue  # el índice lista varias temporadas; solo la configurada
            # El primero gana: los duplicados `-1` traen el mismo contenido.
            found.setdefault(
                match.group(2),
                PartitsRef(number=match.group(2), label=label, url=_absolute(href)),
            )

    return sorted(found.values(), key=lambda r: r.number)


# ── PDF ───────────────────────────────────────────────────────────────────────


def parse_partits(pdf_path: str) -> list[RawRow]:
    """Filas de un PDF de partidos jugados.

    Una fila de datos empieza siempre por una fecha; así se descartan cabeceras
    y las repeticiones de cabecera de cada página sin depender de su texto.
    """
    rows: list[RawRow] = []
    descartadas = invertidas = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for raw in table:
                    if not raw or not _DATE_RE.match((raw[0] or "").strip()):
                        continue
                    if len(raw) != _COLUMNS:
                        descartadas += 1
                        continue
                    row, girada = _row_from([(c or "").strip() for c in raw])
                    if row:
                        rows.append(row)
                        invertidas += girada

    fichero = pdf_path.rsplit("/", 1)[-1]
    if descartadas:
        logger.warning(
            f"   └─ ⚠️  {descartadas} filas con un número de columnas inesperado "
            f"en {fichero}"
        )
    if invertidas:
        logger.warning(
            f"   └─ ⚠️  {invertidas} filas con el marcador al revés, corregidas "
            f"por el signo del ΔRK en {fichero}"
        )
    return rows


def _row_from(cells: list[str]) -> tuple[Optional[RawRow], bool]:
    """Fila tipada, y si hubo que darle la vuelta al marcador.

    **La fuente publica algunas filas con el marcador sin invertir.** Cuando
    escribe el partido desde el otro jugador cambia el nombre y la licencia de
    `JUG 1`, pero en unos pocos eventos se deja el marcador tal cual, de modo
    que la fila dice que ganó quien perdió. El ΔRK, en cambio, siempre es el del
    jugador de la fila.

    Se corrige con una heurística: ganar suele subir el rating y perder bajarlo,
    así que cuando el marcador contradice al ΔRK se gira. Afecta a 264 filas de
    99.584 (0,27 %) de la temporada 2025-2026.

    **Es una heurística, no una ley, y no acierta siempre.** Contrastada contra
    el PG/PP del ranking en tres ediciones (5.761 jugadores):

        marcador crudo .......... 98,78 % de los jugadores cuadran
        girando por el ΔRK ...... 99,53 %

    Es decir: reduce los jugadores con el balance mal de 70 a 27, pero **rompe
    11 que estaban bien**. Se aplica porque el saldo es claramente favorable, no
    porque sea correcta. La razón de que falle es que en este sistema sí se
    puede ganar y perder puntos —vencer apurado a alguien muy inferior resta—,
    así que el signo del ΔRK no determina el resultado.

    Comprobado también que comparar los dos incrementos de la fila (`ΔRK1` vs
    `ΔRK2`) da exactamente los mismos aciertos, así que no compensa cambiar de
    regla. La solución buena está apuntada en `todos_a_revisar.md` §10:
    reconciliar con el ranking, que es la fuente autoritativa.
    """
    if not cells[3] or not cells[4] or not cells[5]:
        return None, False

    sets, opp_sets = _int(cells[6]), _int(cells[7])
    d_rk = _float(cells[10])
    girada = bool(d_rk) and sets != opp_sets and (sets > opp_sets) != (d_rk > 0)
    if girada:
        sets, opp_sets = opp_sets, sets

    return RawRow(
        date=_iso_date(cells[0]),
        event=cells[1],
        category=cells[2],
        licence=cells[3],
        name=cells[4],
        opponent_name=cells[5],
        sets=sets,
        opp_sets=opp_sets,
        rk=_float(cells[8]),
        opp_rk=_float(cells[9]),
        d_rk=d_rk,
        opp_d_rk=_float(cells[11]),
        rd=_float(cells[12]),
        opp_rd=_float(cells[13]),
    ), girada


def _iso_date(text: str) -> str:
    """`02/06/2026` → `2026-06-02`, para que ordene como texto."""
    match = _DATE_RE.match(text)
    if not match:
        return text
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


# ── Emparejado del espejo ─────────────────────────────────────────────────────


def resolve_opponent_codes(rows: list[RawRow]) -> list[Optional[str]]:
    """Licencia del rival de cada fila, o `None` si no está en el PDF.

    Cada partido aparece dos veces, una desde cada jugador. La fila espejo es la
    que tiene los dos nombres intercambiados, y su `LLIC 1` es lo que buscamos.

    El emparejado **se valida**, no se cree: en el espejo el marcador tiene que
    estar invertido y los ratings cruzados. Esa validación es lo que distingue a
    los homónimos —hay 19 nombres repetidos en la edición 02— y lo que evita
    atribuirle a un jugador el partido de otro que se llama igual.

    Devuelve `None` para los rivales sin licencia FCTT. No es un fallo del
    parseo: en la edición 06 las 1.197 filas sin espejo son **todas** de
    competición estatal (`ESTATAL`, `LLNN`, `CE ABS`, `COPAS`), donde el rival
    es de otra federación y la FCTT no le calcula ranking, así que no tiene fila
    propia. Ese código no existe en ningún sitio.
    """
    index: dict[tuple, list[RawRow]] = {}
    for row in rows:
        index.setdefault((row.date, row.event, row.name, row.opponent_name), []).append(row)

    codes: list[Optional[str]] = []
    for row in rows:
        mirrors = index.get((row.date, row.event, row.opponent_name, row.name), [])
        codes.append(next((m.licence for m in mirrors if _mirrors(row, m)), None))
    return codes


def _mirrors(row: RawRow, other: RawRow) -> bool:
    """¿`other` es el mismo partido visto desde el otro lado?"""
    return (
        other.sets == row.opp_sets
        and other.opp_sets == row.sets
        and other.rk == row.opp_rk
        and other.opp_rk == row.rk
    )


# ── Clasificación de eventos ──────────────────────────────────────────────────


def classify_event(event: str) -> str:
    """`lliga` o `torneig`.

    La fuente mezcla las dos ligas que ya scrapeamos (`Lliga RTB` → RTBTT,
    `LLNN`/`ESTATAL` → RFETM) con una decena de torneos que no cubre ninguna
    otra fuente. El texto original se guarda igualmente: esto es solo para poder
    filtrar.
    """
    upper = event.upper().lstrip()
    return "lliga" if upper.startswith(_LEAGUE_PREFIXES) else "torneig"
