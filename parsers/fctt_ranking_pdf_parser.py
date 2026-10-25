"""Parser de los rànquings de la FCTT (PDF).

Forma de la fuente:

    /ranquings/     h3 = mes ("Juliol 2025/2026", "Inicial 2025/2026") y debajo
                    un PDF por categoría, con nombre
                    `RK{temporada}_{edición}_{CATEGORÍA}.pdf`.

Las revisiones mensuales van numeradas (`02`=Novembre … `10`=Juliol) y todas
comparten la misma tabla de 17 columnas:

    NP · jug · SEN · EDAT FCTT · CLUB · RK anterior · ΔRK · _ · RK · RD · _ ·
    PJ · PG · PP · _ · P-any · CLASS

`NP` es el `codi_jugador` que usan también las páginas de liga, así que el
ranking enlaza con los partidos sin tener que casar nombres.

El ranking **inicial** (`Rk2526_ini.pdf`) es otro documento: una única lista
global de 8 columnas (POS, NP, JUGADOR/A, GEN, NAC, EDAT, CLUB, RANK), sin
partidos ni desviación. Se trata como la edición `ini`, categoría `GEN`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import pdfplumber
from bs4 import BeautifulSoup

from models.ranking import RankingEntry
from sources import FCTT, SEASON_SHORT
from utils.logger import get_logger

logger = get_logger(__name__)

#: `RK2526_10_SEM.pdf` — el sufijo `-1` lo añade WordPress al resubir un fichero
#: con el mismo nombre; el contenido es idéntico, así que se deduplica.
_MONTHLY_RE = re.compile(r"^RK(\d{4})_(\d{2})_(.+?)(?:-\d+)?\.pdf$", re.IGNORECASE)
_INITIAL_RE = re.compile(r"^RK(\d{4})_ini\.pdf$", re.IGNORECASE)

_HEADER_RE = re.compile(
    r"REVISI[ÓO]\s+(\d+)\s*-\s*([A-ZÀ-Ÿ]+)\.\s*TEMPORADA\s+([\d\-]+)\.\s*(v\d+)",
    re.IGNORECASE,
)

#: No es un ranking: es la relación de partidos jugados por deportista, con otro
#: formato y otro contenido (incluye torneos). Se ignora en esta fase.
_SKIP_CATEGORIES = {"partits_jugats"}

_INITIAL_CATEGORY = "GEN"

#: Prefijos para las categorías cuyo nombre en la fuente empieza por dígito.
#:
#: Los ids de categoría acaban siendo **claves de mapa** en Firestore
#: (`current.SEM.pos`), y una clave que empieza por número obliga a escapar la
#: ruta con acentos graves. Si no se escapa, la consulta devuelve cero
#: resultados **sin dar error**, que en la app se ve como "este jugador no
#: tiene ranking". Se renombran en origen para que el problema no exista.
#:
#: El prefijo sigue el vocabulario de la propia fuente, que en la columna
#: EDAT FCTT escribe `V+40` y `S21-1`.
_CATEGORY_PREFIXES = {
    "21": "S",                                    # sub-21 → S21F / S21M
    "40": "V", "50": "V", "60": "V", "65": "V", "70": "V",   # veteranos → V40M…
}


def normalize_category(raw: str) -> str:
    """`40M` → `V40M` · `21F` → `S21F` · `SEM` → `SEM` (sin cambios)."""
    prefix = _CATEGORY_PREFIXES.get(raw[:2])
    return f"{prefix}{raw}" if prefix else raw


@dataclass(frozen=True)
class EditionRef:
    """Una edición anunciada en el índice, con sus PDFs."""

    number: str                   # "ini", "02".."10"
    label: str                    # "Juliol 2025/2026"
    pdfs: dict[str, str] = field(default_factory=dict)   # categoría → URL


# ── Índice ────────────────────────────────────────────────────────────────────


def parse_index(html: str) -> list[EditionRef]:
    """Ediciones publicadas, de la más reciente a la más antigua."""
    soup = BeautifulSoup(html, "html.parser")
    ediciones: dict[str, dict] = {}

    for h3 in soup.find_all("h3"):
        label = h3.get_text(" ", strip=True)
        for node in h3.find_all_next(["h3", "a"]):
            if node.name == "h3":
                break
            href = node.get("href", "")
            if not href.lower().endswith(".pdf"):
                continue

            filename = href.rsplit("/", 1)[-1]
            parsed = _parse_filename(filename)
            if parsed is None:
                continue
            number, category = parsed

            edicion = ediciones.setdefault(number, {"label": label, "pdfs": {}})
            # El primero gana: los duplicados `-1` traen el mismo contenido.
            edicion["pdfs"].setdefault(category, _absolute(href))

    return [
        EditionRef(number=n, label=e["label"], pdfs=e["pdfs"])
        for n, e in ediciones.items()
    ]


def _parse_filename(filename: str) -> Optional[tuple[str, str]]:
    """`RK2526_10_SEM.pdf` → ("10", "SEM"). None si no es un ranking o es de OTRA
    temporada: el índice lista varias temporadas a la vez (p. ej. la 2526 completa
    y la 2627 recién empezada), así que se filtra por `SEASON_SHORT` para no mezclar
    ediciones de años distintos en la misma competición."""
    if match := _INITIAL_RE.match(filename):
        if match.group(1) != SEASON_SHORT:
            return None
        return "ini", _INITIAL_CATEGORY

    match = _MONTHLY_RE.match(filename)
    if not match:
        return None
    if match.group(1) != SEASON_SHORT:
        return None

    category = match.group(3)
    if category.lower().rstrip("-0123456789") in _SKIP_CATEGORIES:
        return None
    return match.group(2), normalize_category(category.upper())


def _absolute(href: str) -> str:
    return href if href.startswith("http") else f"{FCTT.base_url}{href}"


# ── PDF ───────────────────────────────────────────────────────────────────────


def parse_header(pdf_path: str) -> tuple[str, str, str, str]:
    """(revisión, mes, temporada, versión) de la primera línea del PDF.

    Ej.: "REVISIÓ 10 - JULIOL. TEMPORADA 2025-2026. v1."
    """
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text() or ""
    if match := _HEADER_RE.search(text):
        return match.group(1), match.group(2).upper(), match.group(3), match.group(4)
    return "", "", "", ""


def parse_title(pdf_path: str) -> str:
    """Título del ranking ("RÀNQUING SÈNIOR MASCULÍ")."""
    with pdfplumber.open(pdf_path) as pdf:
        lines = (pdf.pages[0].extract_text() or "").split("\n")
    for line in lines[:4]:
        if "RÀNQUING" in line.upper() or "RANQUING" in line.upper():
            return line.strip()
    return ""


def parse_ranking(pdf_path: str) -> list[RankingEntry]:
    """Filas de una revisión mensual (tabla de 17 columnas)."""
    entries: list[RankingEntry] = []
    for row in _table_rows(pdf_path, expected=17):
        entry = _entry_from_monthly(row)
        if entry:
            entries.append(entry)
    return entries


def parse_initial(pdf_path: str) -> list[RankingEntry]:
    """Filas del ranking inicial (tabla de 8 columnas, lista global)."""
    entries: list[RankingEntry] = []
    for row in _table_rows(pdf_path, expected=8):
        entry = _entry_from_initial(row)
        if entry:
            entries.append(entry)
    return entries


def _table_rows(pdf_path: str, expected: int) -> list[list[str]]:
    """Filas de datos de todas las páginas, descartando cabeceras.

    Una fila de datos empieza siempre por un número (el código de jugador en
    las revisiones mensuales, la posición en el inicial).
    """
    rows: list[list[str]] = []
    descartadas = 0
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row or not (row[0] or "").strip().isdigit():
                        continue
                    if len(row) != expected:
                        descartadas += 1
                        continue
                    rows.append([(c or "").strip() for c in row])
    if descartadas:
        logger.warning(
            f"   └─ ⚠️  {descartadas} filas con un número de columnas inesperado "
            f"en {pdf_path.rsplit('/', 1)[-1]}"
        )
    return rows


def _entry_from_monthly(row: list[str]) -> Optional[RankingEntry]:
    # 0 NP · 1 jug · 2 pos · 3 edat · 4 club · 5 rk_ant · 6 ΔRK · 7 _ · 8 RK
    # 9 RD · 10 _ · 11 PJ · 12 PG · 13 PP · 14 _ · 15 P-any · 16 CLASS
    if not row[0] or not row[1]:
        return None
    return RankingEntry(
        player_code=row[0],
        name=row[1],
        position=_int(row[2]),
        edat_fctt=row[3],
        club=row[4],
        d_rk=_float(row[6]),
        rk=_float(row[8]),
        rd=_float(row[9]),
        pj=_int(row[11]),
        pg=_int(row[12]),
        pp=_int(row[13]),
        p_any=_int(row[15]),
        classified=row[16].upper().startswith("CLASS"),
    )


def _entry_from_initial(row: list[str]) -> Optional[RankingEntry]:
    # 0 POS · 1 NP · 2 JUGADOR/A · 3 GEN · 4 NAC · 5 EDAT · 6 CLUB · 7 RANK
    if not row[1] or not row[2]:
        return None
    return RankingEntry(
        player_code=row[1],
        name=row[2],
        position=_int(row[0]),
        gender=row[3],
        edat_fctt=row[5],
        club=row[6],
        rk=_float(row[7]),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _float(text: str) -> float:
    """La fuente usa coma decimal: "2920,8" → 2920.8."""
    try:
        return float(text.replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return 0.0


def _int(text: str) -> int:
    try:
        return int(text)
    except (ValueError, TypeError):
        return 0
