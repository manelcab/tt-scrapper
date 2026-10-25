from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from sources import RTBTT
from utils.logger import set_level


@dataclass
class Config:
    competition_id: str
    base_url: str
    season_path: str
    index_url: str
    output_dir: str
    cache_dir: str
    pdfs_dir: str
    dry_run: bool
    log_level: str
    only_categories: list[str]
    only_groups: list[str]
    google_credentials: str
    firestore_project_id: str
    storage_backend: str
    sql_out_dir: str

    @classmethod
    def from_env(cls, env_file: str = ".env") -> "Config":
        load_dotenv(env_file, override=False)

        def _split_list(val: str) -> list[str]:
            if not val:
                return []
            return [v.strip() for v in val.split(",") if v.strip()]

        # LOG_LEVEL only takes effect once it reaches the loggers, which were
        # already created (at NOTSET → root's WARNING) when the modules imported.
        log_level = os.getenv("LOG_LEVEL", "INFO")
        set_level(log_level)

        return cls(
            # URLs e id de competición: siempre desde sources.py (ver SEASON).
            competition_id=RTBTT.id,
            base_url=RTBTT.base_url,
            season_path=RTBTT.acta_path,
            index_url=RTBTT.index_url,
            output_dir=os.getenv("OUTPUT_DIR", "./output"),
            cache_dir=os.getenv("CACHE_DIR", "./output/cache"),
            pdfs_dir=os.getenv("PDFS_DIR", "./output/pdfs"),
            dry_run=os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes"),
            log_level=log_level,
            only_categories=_split_list(os.getenv("ONLY_CATEGORIES", "")),
            only_groups=_split_list(os.getenv("ONLY_GROUPS", "")),
            google_credentials=os.getenv(
                "GOOGLE_APPLICATION_CREDENTIALS",
                "./credentials/firebase-service-account.json",
            ),
            firestore_project_id=os.getenv("FIRESTORE_PROJECT_ID", ""),
            # Backend de almacenamiento: 'firestore' (por defecto) o 'd1' (emite
            # .sql para cargar con wrangler). Ver design/plan_migracion_d1.md.
            storage_backend=os.getenv("STORAGE_BACKEND", "firestore").lower(),
            sql_out_dir=os.getenv("SQL_OUT_DIR", "./output/sql"),
        )
