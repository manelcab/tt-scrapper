from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Team:
    id: str
    name: str
    club: str
    position: int = 0
    points: int = 0
    matches_played: int = 0
    matches_won: int = 0
    matches_drawn: int = 0
    matches_lost: int = 0
    # PF / PC de la tabla de clasificación: partidos individuales a favor y en
    # contra (suma de los individuales de todos los encuentros). Sirven de
    # desempate cuando dos equipos tienen los mismos encuentros ganados.
    games_for: int = 0
    games_against: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "club": self.club,
            "position": self.position,
            "points": self.points,
            "matches_played": self.matches_played,
            "matches_won": self.matches_won,
            "matches_drawn": self.matches_drawn,
            "matches_lost": self.matches_lost,
            "games_for": self.games_for,
            "games_against": self.games_against,
        }
