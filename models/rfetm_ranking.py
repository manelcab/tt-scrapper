"""Rànquings de la RFETM.

Modelo propio y no el de la FCTT (`models/ranking.py`) porque las dos fuentes
publican cosas distintas: aquí hay año de nacimiento, federación autonómica y
desglose de puntos por tipo de competición, y en cambio no hay `RD` ni
`P-any`. Forzar un modelo común escondería esas diferencias.

La diferencia de fondo: una fila de la RFETM trae **la posición del jugador en
las 14 categorías a la vez**, mientras que en la FCTT cada PDF era una
categoría. Por eso aquí `history` se indexa por edición y dentro por categoría,
y no al revés.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Orden de las 14 columnas de posición, tal y como las publica la fuente.
CATEGORIES = (
    "NAC", "SEN", "S21", "JUV", "CAD", "INF", "ALE", "BEN",
    "V40", "V50", "V60", "V65", "V70", "V75",
)

#: La fuente escribe esto en la columna de una categoría en la que el jugador
#: no compite. **No es una posición** y no se guarda.
NOT_RANKED = 99999


@dataclass
class RfetmEntry:
    """Un jugador en una edición del ranking."""

    lic: str
    name: str
    club: str = ""
    fa: str = ""                  # federación autonómica: -AND-, -CAT-…
    cat: str = ""                 # categoría de edad declarada: SEN, V+40…
    birth_year: int = 0
    sex: str = ""                 # M | F
    pts: float = 0.0
    pts_prev: float = 0.0
    pj: int = 0
    pg: int = 0
    pp: int = 0
    #: Puntos ganados o perdidos por tipo de competición.
    by_event: dict[str, float] = field(default_factory=dict)
    #: Posición por categoría, ya sin los `99999`.
    positions: dict[str, int] = field(default_factory=dict)

    def to_history(self) -> dict:
        """Lo que se guarda de esta edición dentro del documento del jugador."""
        entrada: dict = {
            "pts": self.pts,
            "pts_prev": self.pts_prev,
            "pj": self.pj,
            "pg": self.pg,
            "pp": self.pp,
            "pos": dict(self.positions),
        }
        if self.by_event:
            entrada["by_event"] = dict(self.by_event)
        return entrada

    def to_top(self, category: str) -> dict:
        """Lo que se guarda en la lista `top` de una categoría."""
        return {
            "pos": self.positions[category],
            "lic": self.lic,
            "name": self.name,
            "club": self.club,
            "fa": self.fa,
            "pts": self.pts,
        }


@dataclass
class RfetmEdition:
    """Un mes de ranking: las dos tablas, masculina y femenina."""

    id: str                       # "2026-06"
    year: int = 0
    month: int = 0
    label: str = ""               # "juny 2026"
    entries: list[RfetmEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "year": self.year,
            "month": self.month,
            "label": self.label,
            "player_count": len(self.entries),
            "categories": sorted(
                {
                    f"{c}_{e.sex}"
                    for e in self.entries
                    for c in e.positions
                }
            ),
        }
