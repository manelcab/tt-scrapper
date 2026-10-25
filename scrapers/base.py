from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.competition import Category, Group
    from storage.firestore_client import FirestoreClient
    from storage.local_cache import LocalCache


class CompetitionScraper(ABC):
    @abstractmethod
    def get_source_name(self) -> str: ...

    @abstractmethod
    def get_index(self) -> list["Category"]: ...

    @abstractmethod
    def scrape_group(
        self,
        group: "Group",
        cache: "LocalCache",
        firestore: "FirestoreClient",
        dry_run: bool,
        only_jornada: int = 0,
    ) -> dict: ...
