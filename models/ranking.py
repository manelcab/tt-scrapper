from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RankingEntry:
    """Una fila de ranking: un jugador en una categoría y una edición.

    Los campos que la fuente no publica en el ranking inicial (que tiene otro
    formato, más corto) quedan a su valor por defecto.
    """

    player_code: str
    name: str
    position: int
    rk: float
    club: str = ""
    edat_fctt: str = ""
    gender: str = ""
    d_rk: float = 0.0
    rd: float = 0.0
    pj: int = 0
    pg: int = 0
    pp: int = 0
    p_any: int = 0
    classified: bool = True

    def to_history(self) -> dict:
        """Lo que se guarda por (categoría, edición) en el documento del jugador.

        No se guarda `RK anterior`: es `rk - d_rk` y además ya tenemos el
        histórico completo. Tampoco el nombre o el club, que van al nivel del
        jugador para no repetirlos en cada edición.
        """
        return {
            "pos": self.position,
            "rk": self.rk,
            "d_rk": self.d_rk,
            "rd": self.rd,
            "pj": self.pj,
            "pg": self.pg,
            "pp": self.pp,
            "p_any": self.p_any,
            "classified": self.classified,
        }

    def to_top(self) -> dict:
        """Lo que se guarda en la lista `top` de una categoría."""
        return {
            "pos": self.position,
            "code": self.player_code,
            "name": self.name,
            "club": self.club,
            "rk": self.rk,
        }


@dataclass
class RankingCategory:
    """Un PDF: una categoría dentro de una edición."""

    id: str                       # SEM, 40F, ADA, GEN (inicial)…
    title: str = ""               # "RÀNQUING SÈNIOR MASCULÍ"
    source_url: str = ""
    entries: list[RankingEntry] = field(default_factory=list)


@dataclass
class RankingEdition:
    """Una revisión mensual completa del ranking."""

    number: str                   # "ini", "02".."10"
    month: str = ""               # "JULIOL"
    season: str = ""              # "2025-2026"
    version: str = ""             # "v1"
    label: str = ""               # "Juliol 2025/2026" (como lo titula la web)
    categories: list[RankingCategory] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "month": self.month,
            "season": self.season,
            "version": self.version,
            "label": self.label,
            "categories": [c.id for c in self.categories],
            "player_count": len(
                {e.player_code for c in self.categories for e in c.entries}
            ),
        }
