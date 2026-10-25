from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Group:
    id: str
    name: str
    category_id: str
    phase: str
    source_url: str


@dataclass
class Category:
    id: str
    name: str
    level: int
    groups: list[Group] = field(default_factory=list)


@dataclass
class CompetitionIndex:
    competition_id: str
    name: str
    organization: str
    season: str
    source_url: str
    categories: list[Category] = field(default_factory=list)
