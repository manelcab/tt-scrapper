from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

_LEGACY_HASHES_FILE = "hashes.json"


class LocalCache:
    """Caché local de hashes y PDFs.

    Con `readonly=True` (lo que usa `--dry-run`) los hashes se siguen leyendo y
    actualizando **en memoria**, para que la simulación se comporte igual que
    una ejecución real, pero nunca se vuelcan a disco. Sin esto, un dry-run deja
    la caché como si la carga se hubiera hecho y la siguiente ejecución de
    verdad se salta todo sin escribir nada en el destino.

    Los PDFs sí se guardan en disco aunque sea dry-run: son una copia del dato
    de origen, no estado que decida qué se escribe.

    **Caché por destino** (`backend`): el hash dice "esto ya está cargado", pero
    lo que está cargado en Firestore no lo está en D1. Si se compartiera el
    fichero, la primera carga a una D1 vacía se saltaría casi todo sin un solo
    error. Por eso cada destino tiene su propio `hashes.<backend>.json`, y una
    carga a un destino nuevo arranca con caché vacía por construcción. La caché
    antigua `hashes.json` se adopta como la de Firestore por compatibilidad.
    """

    def __init__(
        self, cache_dir: str, readonly: bool = False, backend: str = "firestore"
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._backend = backend
        self._hashes_path = self._cache_dir / f"hashes.{backend}.json"
        self._legacy_path = self._cache_dir / _LEGACY_HASHES_FILE
        self._readonly = readonly
        self._data = self._load()

    # ------------------------------------------------------------------ groups

    def get_group_hash(self, group_id: str) -> Optional[str]:
        return self._data.get("groups", {}).get(group_id)

    def set_group_hash(self, group_id: str, hash: str) -> None:
        self._data.setdefault("groups", {})[group_id] = hash
        self._save()

    # --------------------------------------------------------------- matchdays

    def get_matchday_hash(self, key: str) -> Optional[str]:
        return self._data.get("matchdays", {}).get(key)

    def set_matchday_hash(self, key: str, pdf_hash: str) -> None:
        self._data.setdefault("matchdays", {})[key] = pdf_hash
        self._save()

    # ------------------------------------------------------------ invalidación

    def invalidate_group(self, group_id: str) -> None:
        """Olvida el hash del grupo y el de todas sus jornadas.

        Es lo que necesita `--force` para repoblar: invalidar solo el hash del
        grupo hace que se vuelvan a descargar los PDFs, pero el hash por jornada
        seguiría coincidiendo y nada se escribiría.

        Las claves de jornada llevan prefijo según la fuente (`PREF_G1_jornada3`,
        `rfetm_liga1_g0_M_jornada3`), así que el grupo se ancla a inicio de
        cadena o a un `_` para no confundir `1A_G1` con `11A_G1`.
        """
        self._data.setdefault("groups", {}).pop(group_id, None)
        matchdays = self._data.setdefault("matchdays", {})
        pattern = re.compile(rf"(^|_){re.escape(group_id)}_jornada\d+$")
        for key in [k for k in matchdays if pattern.search(k)]:
            del matchdays[key]
        self._save()

    # -------------------------------------------------------------------- PDFs

    def is_pdf_cached(self, filename: str) -> bool:
        # filename is relative to pdfs_dir; caller must pass the right name
        return (self._cache_dir.parent / "pdfs" / filename).exists()

    def save_pdf(self, filename: str, data: bytes, pdfs_dir: str) -> None:
        pdf_path = Path(pdfs_dir) / filename
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(data)
        logger.debug(f"Saved PDF: {pdf_path}")

    # ----------------------------------------------------------------- private

    def _load(self) -> dict:
        path = self._hashes_path
        # Compat: si aún no hay caché para este destino y existe la antigua
        # `hashes.json`, se adopta solo para Firestore (era su estado). Un
        # destino nuevo como D1 arranca vacío —justo lo que evita el gotcha.
        if (
            not path.exists()
            and self._backend == "firestore"
            and self._legacy_path.exists()
        ):
            path = self._legacy_path
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(f"Could not load cache file, starting fresh: {exc}")
        return {}

    def _save(self) -> None:
        if self._readonly:
            return
        self._hashes_path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
