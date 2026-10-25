from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from utils.logger import get_logger

if TYPE_CHECKING:
    from models.competition import CompetitionIndex, Group
    from models.match import Match, Matchday
    from models.team import Team

logger = get_logger(__name__)

_MAX_BATCH_OPS = 499

#: Tope de tamaño por commit. El límite real de Firestore es 10 MiB; se deja
#: margen porque el tamaño se estima, no se mide sobre el protobuf.
_MAX_BATCH_BYTES = 4 * 1024 * 1024

#: Tope de campos hoja por commit. Es el límite que de verdad muerde con los
#: documentos de ranking: Firestore indexa **cada campo hoja**, incluidos los
#: anidados dentro de mapas, así que un documento de jugador con el histórico
#: (categorías × ediciones × 9 campos) genera cientos de entradas de índice.
#: Agrupar por número de documentos o por bytes no lo captura y el commit
#: revienta con "Transaction too big".
_MAX_BATCH_LEAVES = 5000


def _estimated_size(data: dict) -> int:
    """Tamaño aproximado del documento, para no pasarse del tope por commit."""
    try:
        return len(json.dumps(data, default=str))
    except (TypeError, ValueError):
        return 1024


def _leaf_count(value) -> int:
    """Campos hoja del documento — proxy del coste de indexación."""
    if isinstance(value, dict):
        return sum(_leaf_count(v) for v in value.values()) or 1
    if isinstance(value, (list, tuple)):
        return sum(_leaf_count(v) for v in value) or 1
    return 1


class FirestoreClient:
    def __init__(self, project_id: str, credentials_path: str) -> None:
        self._project_id = project_id
        self._credentials_path = credentials_path
        self._db = None

    def _get_db(self):
        if self._db is None:
            os.environ.setdefault(
                "GOOGLE_APPLICATION_CREDENTIALS", self._credentials_path
            )
            from google.cloud import firestore  # type: ignore

            self._db = firestore.Client(project=self._project_id)
        return self._db

    def validate(self) -> bool:
        try:
            db = self._get_db()
            # A lightweight round-trip: list at most 1 collection
            next(iter(db.collections()), None)
            logger.info("Firestore connection validated successfully.")
            return True
        except Exception as exc:
            logger.error(f"Firestore validation failed: {exc}")
            return False

    # ------------------------------------------------------------------ writes

    def save_competition(
        self,
        competition: "CompetitionIndex",
        dry_run: bool = False,
    ) -> None:
        doc_ref = f"competitions/{competition.competition_id}"
        data = {
            "competition_id": competition.competition_id,
            "name": competition.name,
            "organization": competition.organization,
            "season": competition.season,
            "source_url": competition.source_url,
        }
        if dry_run:
            logger.info(f"[DRY-RUN] Would write competition → {doc_ref}")
            return
        self._get_db().document(doc_ref).set(data)
        logger.debug(f"Saved competition {competition.competition_id}")

    def save_group(
        self,
        competition_id: str,
        category_id: str,
        group: "Group",
        dry_run: bool = False,
    ) -> None:
        doc_ref = (
            f"competitions/{competition_id}"
            f"/categories/{category_id}"
            f"/groups/{group.id}"
        )
        data = {
            "id": group.id,
            "name": group.name,
            "category_id": group.category_id,
            "phase": group.phase,
            "source_url": group.source_url,
        }
        if dry_run:
            logger.info(f"[DRY-RUN] Would write group → {doc_ref}")
            return
        self._get_db().document(doc_ref).set(data)
        logger.debug(f"Saved group {group.id}")

    def save_matchday_with_matches(
        self,
        competition_id: str,
        category_id: str,
        group_id: str,
        matchday: "Matchday",
        matches: "list[Match]",
        dry_run: bool = False,
    ) -> None:
        base = (
            f"competitions/{competition_id}"
            f"/categories/{category_id}"
            f"/groups/{group_id}"
        )
        matchday_ref = f"{base}/matchdays/{matchday.number}"

        if dry_run:
            logger.info(
                f"[DRY-RUN] Would write matchday → {matchday_ref} "
                f"+ {len(matches)} match(es)"
            )
            return

        db = self._get_db()
        ops: list[tuple] = []  # (ref, data)

        ops.append((db.document(matchday_ref), matchday.to_dict()))
        for match in matches:
            match_ref = f"{matchday_ref}/matches/{match.id}"
            ops.append((db.document(match_ref), match.to_dict()))

        self._commit_in_batches(db, ops)
        logger.debug(
            f"Saved matchday {matchday.number} with {len(matches)} matches for {group_id}"
        )

    def save_teams(
        self,
        competition_id: str,
        category_id: str,
        group_id: str,
        teams: "list[Team]",
        dry_run: bool = False,
    ) -> None:
        base = (
            f"competitions/{competition_id}"
            f"/categories/{category_id}"
            f"/groups/{group_id}"
        )
        if dry_run:
            logger.info(
                f"[DRY-RUN] Would write {len(teams)} team(s) under {base}/teams/"
            )
            return

        db = self._get_db()
        ops = []
        for team in teams:
            team_ref = f"{base}/teams/{team.id}"
            ops.append((db.document(team_ref), team.to_dict()))

        self._commit_in_batches(db, ops)
        logger.debug(f"Saved {len(teams)} teams for {group_id}")

    # ------------------------------------------------------------- rankings

    def save_ranking_root(
        self,
        competition_id: str,
        data: dict,
        dry_run: bool = False,
    ) -> None:
        doc_ref = f"rankings/{competition_id}"
        if dry_run:
            logger.info(f"[DRY-RUN] Would write ranking root → {doc_ref}")
            return
        self._get_db().document(doc_ref).set(data, merge=True)

    def save_ranking_edition(
        self,
        competition_id: str,
        edition_id: str,
        edition: dict,
        categories: "list[tuple[str, dict]]",
        dry_run: bool = False,
    ) -> None:
        """Escribe la edición y un documento por categoría con su `top`.

        El id va aparte porque cada federación numera las ediciones a su
        manera: la FCTT usa el número de revisión (`02`…`10`) y la RFETM el mes
        (`2026-06`).
        """
        base = f"rankings/{competition_id}/editions/{edition_id}"
        if dry_run:
            logger.info(
                f"[DRY-RUN] Would write edition → {base} + {len(categories)} categoría(s)"
            )
            return

        db = self._get_db()
        ops = [(db.document(base), edition)]
        for category_id, payload in categories:
            ops.append((db.document(f"{base}/categories/{category_id}"), payload))
        self._commit_in_batches(db, ops)

    def save_ranking_players(
        self,
        competition_id: str,
        players: "list[tuple[str, dict]]",
        dry_run: bool = False,
        merge: bool = True,
    ) -> None:
        """Escribe los documentos de jugador.

        `merge=True` (por defecto) es lo que permite ejecutar mes a mes sin
        releer nada: la edición nueva se añade dentro de `history` sin pisar las
        anteriores.

        `merge=False` se usa en una carga completa, donde el payload ya trae
        todo el histórico. Además es la única forma de que desaparezcan claves
        que ya no existen —una categoría renombrada, por ejemplo—: fusionando,
        la clave vieja se quedaría ahí para siempre.
        """
        if dry_run:
            logger.info(
                f"[DRY-RUN] Would write {len(players)} jugador(es) "
                f"({'merge' if merge else 'overwrite'}) → "
                f"rankings/{competition_id}/players/"
            )
            return

        db = self._get_db()
        ops = [
            (db.document(f"rankings/{competition_id}/players/{code}"), payload)
            for code, payload in players
        ]
        self._commit_in_batches(db, ops, merge=merge)

    def save_player_match_logs(
        self,
        competition_id: str,
        logs: "list[tuple[str, str, dict]]",
        dry_run: bool = False,
    ) -> None:
        """Escribe un documento por (jugador, edición) con sus partidos.

        `logs` son tuplas `(codi_jugador, edición, payload)`. Se sobrescribe sin
        fusionar: el payload trae la edición entera, así que una revisión
        republicada corrige lo que hubiera.
        """
        if dry_run:
            logger.info(
                f"[DRY-RUN] Would write {len(logs)} match_log(s) → "
                f"rankings/{competition_id}/players/*/match_log/*"
            )
            return

        db = self._get_db()
        ops = [
            (
                db.document(
                    f"rankings/{competition_id}/players/{code}/match_log/{edition}"
                ),
                payload,
            )
            for code, edition, payload in logs
        ]
        self._commit_in_batches(db, ops)

    def save_player_records(
        self,
        competition_id: str,
        records: "list[tuple[str, dict]]",
        dry_run: bool = False,
    ) -> None:
        """Funde el resumen de temporada dentro del documento de jugador."""
        if dry_run:
            logger.info(
                f"[DRY-RUN] Would write {len(records)} record(s) → "
                f"rankings/{competition_id}/players/"
            )
            return

        db = self._get_db()
        ops = [
            (
                db.document(f"rankings/{competition_id}/players/{code}"),
                {"record": record},
            )
            for code, record in records
        ]
        self._commit_in_batches(db, ops, merge=True)

    # ----------------------------------------------------------------- helpers

    def _commit_in_batches(self, db, ops: list[tuple], merge: bool = False) -> None:
        """Commit a list of (doc_ref, data) tuples.

        Se corta por **dos** límites, no solo por número de operaciones: además
        del máximo de escrituras por lote, cada commit tiene un tope de tamaño
        (10 MiB). Los documentos de jugador del ranking llevan el histórico
        entero y 499 de golpe se pasan: Firestore responde "Transaction too big".
        """
        batch = db.batch()
        count = 0
        size = 0
        leaves = 0

        for ref, data in ops:
            payload = _estimated_size(data)
            doc_leaves = _leaf_count(data)
            if count and (
                count >= _MAX_BATCH_OPS
                or size + payload > _MAX_BATCH_BYTES
                or leaves + doc_leaves > _MAX_BATCH_LEAVES
            ):
                batch.commit()
                logger.debug(
                    f"Committed batch of {count} ops ({size // 1024}KB, {leaves} campos)"
                )
                batch = db.batch()
                count = size = leaves = 0

            batch.set(ref, data, merge=merge)
            count += 1
            size += payload
            leaves += doc_leaves

        if count:
            batch.commit()
            logger.debug(
                f"Committed batch of {count} ops ({size // 1024}KB, {leaves} campos)"
            )
