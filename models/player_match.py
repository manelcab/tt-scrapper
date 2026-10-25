"""Partidos de un jugador, tal y como se guardan en Firestore.

La unidad de almacenamiento es **un documento por jugador y edición**, con los
partidos dentro de un array. Un documento por partido serían ~80.000 escrituras
por temporada —cuatro días de cuota del plan gratuito— y guardarlo todo dentro
del documento del jugador lo haría crecer sin techo y obligaría a reescribirlo
entero cada mes.

El array no se descontrola: la media es de 5 a 8 partidos por jugador y edición,
y el máximo medido en la temporada 2025-2026 es 36.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlayerMatch:
    """Un partido visto desde uno de los dos jugadores."""

    date: str                     # ISO: "2026-06-02"
    event: str                    # "Lliga RTB J4", "Top Català"
    event_type: str               # "lliga" | "torneig"
    category: str                 # 'Veterans 3a "A"', "V60M"
    opponent_name: str
    opponent_code: str = ""       # vacío si el rival no tiene licencia FCTT
    sets: int = 0
    opp_sets: int = 0
    rk: float = 0.0               # rating propio **antes** del partido
    opp_rk: float = 0.0
    d_rk: float = 0.0             # lo que movió este partido
    rd: float = 0.0

    @property
    def won(self) -> bool:
        return self.sets > self.opp_sets

    def to_dict(self) -> dict:
        opponent: dict = {"name": self.opponent_name}
        if self.opponent_code:
            opponent["code"] = self.opponent_code
        else:
            # Rival de otra federación: la FCTT no le calcula ranking, así que
            # no hay ficha que abrir. Se marca para que la app no lo intente.
            opponent["external"] = True

        return {
            "date": self.date,
            "event": self.event,
            "event_type": self.event_type,
            "category": self.category,
            "opponent": opponent,
            "sets": self.sets,
            "opp_sets": self.opp_sets,
            "won": self.won,
            "rk": self.rk,
            "opp_rk": self.opp_rk,
            "d_rk": self.d_rk,
            "rd": self.rd,
        }


@dataclass
class MatchLog:
    """Los partidos de un jugador en una edición: un documento de Firestore."""

    player_code: str
    edition: str                  # "02".."10"
    label: str = ""               # "Juliol 2025/2026"
    matches: list[PlayerMatch] = field(default_factory=list)

    @property
    def won(self) -> int:
        return sum(1 for m in self.matches if m.won)

    def to_dict(self) -> dict:
        ordered = sorted(self.matches, key=lambda m: (m.date, m.event))
        won = self.won
        return {
            "edition": self.edition,
            "label": self.label,
            "from": ordered[0].date if ordered else "",
            "to": ordered[-1].date if ordered else "",
            "played": len(ordered),
            "won": won,
            "lost": len(ordered) - won,
            # Suma de lo que movió el ranking en el periodo. Tiene que coincidir
            # con el ΔRK de la fila del jugador en el ranking de esa edición: es
            # el invariante que valida el emparejado del espejo.
            "d_rk_total": round(sum(m.d_rk for m in ordered), 1),
            "matches": [m.to_dict() for m in ordered],
        }


def build_record(logs: list[MatchLog]) -> dict:
    """Resumen de temporada de un jugador, para el documento de jugador.

    Se guarda junto a `current` para que abrir la ficha de un rival —el caso
    más frecuente— sea **una sola lectura**, sin tocar `match_log`.
    """
    matches = [m for log in logs for m in log.matches]
    if not matches:
        return {}

    ordered = sorted(matches, key=lambda m: m.date)
    won = [m for m in ordered if m.won]
    # La mejor victoria es contra el rival con más rating, que es como lo
    # cuenta un jugador ("le gané al 1.900").
    best = max(won, key=lambda m: m.opp_rk, default=None)

    record = {
        "played": len(ordered),
        "won": len(won),
        "lost": len(ordered) - len(won),
        "tournaments": len({m.event for m in ordered if m.event_type == "torneig"}),
        "last_played": ordered[-1].date,
    }
    if best is not None:
        record["best_win"] = {
            "name": best.opponent_name,
            "rk": best.opp_rk,
            "date": best.date,
            **({"code": best.opponent_code} if best.opponent_code else {}),
        }
    return record
