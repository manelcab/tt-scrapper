from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SetScore:
    local: int
    visitor: int


@dataclass
class IndividualMatch:
    id: str
    order: int
    player_local_name: str
    player_visitor_name: str
    sets: list[SetScore]
    winner: str
    player_local_license: str = ""
    player_visitor_license: str = ""
    is_doubles: bool = False
    player_local_name_2: str = ""
    player_local_license_2: str = ""
    player_visitor_name_2: str = ""
    player_visitor_license_2: str = ""

    @property
    def sets_won_local(self) -> int:
        return sum(1 for s in self.sets if s.local > s.visitor)

    @property
    def sets_won_visitor(self) -> int:
        return sum(1 for s in self.sets if s.visitor > s.local)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "order": self.order,
            "player_local_name": self.player_local_name,
            "player_visitor_name": self.player_visitor_name,
            "sets": [{"local": s.local, "visitor": s.visitor} for s in self.sets],
            "sets_won_local": self.sets_won_local,
            "sets_won_visitor": self.sets_won_visitor,
            "winner": self.winner,
            "player_local_license": self.player_local_license,
            "player_visitor_license": self.player_visitor_license,
            "is_doubles": self.is_doubles,
        }
        if self.is_doubles:
            d["player_local_name_2"] = self.player_local_name_2
            d["player_local_license_2"] = self.player_local_license_2
            d["player_visitor_name_2"] = self.player_visitor_name_2
            d["player_visitor_license_2"] = self.player_visitor_license_2
        return d


@dataclass
class Match:
    id: str
    matchday_number: int
    date: Optional[datetime]
    team_local_id: str
    team_local_name: str
    team_visitor_id: str
    team_visitor_name: str
    score_local: int
    score_visitor: int
    acta_url: str = ""
    status: str = "completed"
    # Terreny de joc / Lugar: dirección donde se juega (la pista del equipo
    # local). Útil para el equipo visitante que se desplaza. Vacío si no consta.
    venue: str = ""
    # Àrbitre / Árbitro: solo en categorías nacionales (y no siempre). Vacío si
    # no consta.
    referee: str = ""
    individual_matches: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "matchday_number": self.matchday_number,
            "date": self.date.isoformat() if self.date else None,
            "team_local_id": self.team_local_id,
            "team_local_name": self.team_local_name,
            "team_visitor_id": self.team_visitor_id,
            "team_visitor_name": self.team_visitor_name,
            "score_local": self.score_local,
            "score_visitor": self.score_visitor,
            "acta_url": self.acta_url,
            "status": self.status,
            "venue": self.venue,
            "referee": self.referee,
            "individual_matches": [
                m.to_dict() if hasattr(m, "to_dict") else m
                for m in self.individual_matches
            ],
        }


@dataclass
class Matchday:
    number: int
    date: Optional[datetime]
    pdf_url: str = ""
    pdf_hash: str = ""
    status: str = "completed"

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "date": self.date.isoformat() if self.date else None,
            "pdf_url": self.pdf_url,
            "pdf_hash": self.pdf_hash,
            "status": self.status,
        }
