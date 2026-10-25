"""Cliente de almacenamiento para Cloudflare D1.

Misma interfaz que `FirestoreClient` (el CLI y los scrapers no cambian), pero en
vez de escribir en Firestore **emite un fichero `.sql` idempotente** que luego se
vuelca con `wrangler d1 execute <db> --file`. Ver design/plan_migracion_d1.md.

Diferencias de fondo con Firestore, que son la razón de la migración:

- El modelo es **relacional**: los documentos anidados de Firestore (`current`,
  `history`, `top`, `matches`) se **descomponen en filas** de las tablas planas
  (`player`, `ranking_snapshot`, `ranking_position`, `player_match`, …).
- Dos métodos de Firestore son **denormalizaciones derivables en SQL** y aquí no
  escriben nada: `save_ranking_root` (player_count = COUNT, last_edition = MAX) y
  `save_player_records` (el `record` sale de un SELECT sobre `player_match`).
- Idempotencia: `INSERT … ON CONFLICT(pk) DO UPDATE`, por la PK natural de cada
  tabla. Reejecutar produce otro `.sql` que al cargar deja el mismo estado.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sources import parse_ranking_label
from utils.logger import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────── metadatos de tablas
# tabla -> (columnas PK, columnas no-PK). El upsert actualiza solo las no-PK.
_TABLES: dict[str, tuple[list[str], list[str]]] = {
    "competition": (
        ["id"],
        ["organization", "federation", "season", "name", "source_url"],
    ),
    "comp_group": (
        ["competition_id", "category_id", "id"],
        ["name", "phase", "source_url"],
    ),
    "team": (
        ["competition_id", "group_id", "id"],
        ["name", "club", "position", "points", "played", "won", "drawn", "lost",
         "games_for", "games_against"],
    ),
    "matchday": (
        ["competition_id", "group_id", "number"],
        ["date", "status", "pdf_url", "pdf_hash"],
    ),
    "encounter": (
        ["competition_id", "id"],
        ["group_id", "matchday_number", "date", "status", "acta_url",
         "team_local_id", "team_local_name", "score_local",
         "team_visitor_id", "team_visitor_name", "score_visitor",
         "venue", "referee"],
    ),
    "individual_match": (
        ["competition_id", "encounter_id", "ord"],
        ["player_local_lic", "player_local_name",
         "player_visitor_lic", "player_visitor_name", "winner", "sets",
         "is_doubles", "player_local_lic_2", "player_local_name_2",
         "player_visitor_lic_2", "player_visitor_name_2"],
    ),
    "player": (
        ["federation", "lic"],
        ["name", "club", "fa", "sex", "birth_year", "cat", "updated_at"],
    ),
    "ranking_edition": (
        ["federation", "season", "edition_id"],
        ["label", "year", "month"],
    ),
    "ranking_snapshot": (
        ["federation", "season", "edition_id", "lic"],
        ["pts", "pts_prev", "d_pts", "rd", "pj", "pg", "pp",
         "p_any", "classified", "by_event"],
    ),
    "ranking_position": (
        ["federation", "season", "edition_id", "lic", "category"],
        ["sex", "pos"],
    ),
    "player_match": (
        ["federation", "season", "edition_id", "lic", "seq"],
        ["date", "event", "event_type", "category", "opp_lic", "opp_name",
         "opp_external", "sets", "opp_sets", "won", "rk", "opp_rk", "d_rk", "rd"],
    ),
}


def _fmt(value: Any) -> str:
    """Un valor Python → literal SQL. Escapa comillas; JSON para dict/list."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    # Cada statement se emite en UNA sola línea (load_chunks los lee línea a
    # línea). Un salto de línea embebido en un valor —p.ej. una dirección de
    # "Terreny de joc"— partiría la sentencia y el troceador la truncaría →
    # syntax error. Se normalizan a espacio.
    text = (
        str(value)
        .replace("\r\n", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("'", "''")
    )
    return f"'{text}'"


def _g(obj: Any, name: str, default: Any = None) -> Any:
    """Lee `name` de un objeto o de un dict indistintamente.

    Los scrapers pasan objetos modelo (Match, IndividualMatch…), pero algún
    camino los serializa a dict antes; esto tolera las dos formas.
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _clean(value: Any) -> Any:
    """Cadena vacía → NULL, para no guardar '' en columnas opcionales."""
    return None if value == "" else value


def _federation_for(competition_id: str) -> str:
    """Federación contra la que resuelven las licencias (no quién organiza).

    La territorial de Barcelona (rtbtt_*) se juega con licencia catalana → FCTT.
    Verificado 40/40 por competición; re-medir al cargar otra temporada.
    """
    return "RFETM" if competition_id.startswith("rfetm") else "FCTT"


def _season_for(competition_id: str) -> str:
    """'fctt_2526' → '2025-2026'. La temporada forma parte de las claves."""
    suffix = competition_id.rsplit("_", 1)[-1]
    if len(suffix) == 4 and suffix.isdigit():
        return f"20{suffix[:2]}-20{suffix[2:]}"
    return suffix


class D1Client:
    """Emite un `.sql` idempotente con la misma interfaz que FirestoreClient."""

    def __init__(self, out_dir: str, run_label: str = "") -> None:
        self._out_dir = out_dir
        self._run_label = run_label
        self._fh = None
        self._path: str | None = None
        self._counts: dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------ fichero
    def _ensure_open(self):
        if self._fh is None:
            os.makedirs(self._out_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            stem = f"load_{self._run_label + '_' if self._run_label else ''}{ts}.sql"
            self._path = os.path.join(self._out_dir, stem)
            self._fh = open(self._path, "w", encoding="utf-8")
            self._fh.write(
                "-- Generado por D1Client "
                f"({datetime.now(timezone.utc).isoformat()})\n"
                "-- Cargar con: wrangler d1 execute <db> --remote --file <este fichero>\n"
                "PRAGMA foreign_keys=OFF;\n\n"
            )
        return self._fh

    def _emit(self, table: str, row: dict) -> None:
        """Escribe un INSERT … ON CONFLICT DO UPDATE para `row` en `table`."""
        pk_cols, other_cols = _TABLES[table]
        cols = [c for c in pk_cols + other_cols if c in row]
        values = ", ".join(_fmt(row[c]) for c in cols)
        stmt = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({values})"
        updatable = [c for c in other_cols if c in row]
        if updatable:
            sets = ", ".join(f"{c}=excluded.{c}" for c in updatable)
            stmt += (
                f" ON CONFLICT({', '.join(pk_cols)}) DO UPDATE SET {sets};"
            )
        else:
            stmt += f" ON CONFLICT({', '.join(pk_cols)}) DO NOTHING;"
        self._ensure_open().write(stmt + "\n")
        self._counts[table] += 1

    @property
    def path(self) -> str | None:
        return self._path

    def validate(self) -> bool:
        try:
            os.makedirs(self._out_dir, exist_ok=True)
            logger.info(f"D1Client: emitiendo SQL en {self._out_dir}")
            return True
        except OSError as exc:
            logger.error(f"D1Client: no se puede escribir en {self._out_dir}: {exc}")
            return False

    # ------------------------------------------------------------- competición
    def save_competition(self, competition, dry_run: bool = False) -> None:
        cid = competition.competition_id
        if dry_run:
            logger.info(f"[DRY-RUN] competition {cid}")
            return
        self._emit("competition", {
            "id": cid,
            "organization": competition.organization,
            "federation": _federation_for(cid),
            "season": competition.season,
            "name": competition.name,
            "source_url": competition.source_url,
        })

    def save_group(self, competition_id, category_id, group, dry_run: bool = False) -> None:
        if dry_run:
            logger.info(f"[DRY-RUN] group {group.id}")
            return
        self._emit("comp_group", {
            "competition_id": competition_id,
            "category_id": category_id,
            "id": group.id,
            "name": group.name,
            "phase": group.phase,
            "source_url": group.source_url,
        })

    def save_teams(self, competition_id, category_id, group_id, teams, dry_run: bool = False) -> None:
        if dry_run:
            logger.info(f"[DRY-RUN] {len(teams)} team(s) en {group_id}")
            return
        for team in teams:
            self._emit("team", {
                "competition_id": competition_id,
                "group_id": group_id,
                "id": team.id,
                "name": team.name,
                "club": _clean(team.club),
                "position": team.position,
                "points": team.points,
                "played": team.matches_played,
                "won": team.matches_won,
                "drawn": team.matches_drawn,
                "lost": team.matches_lost,
                "games_for": team.games_for,
                "games_against": team.games_against,
            })

    def save_matchday_with_matches(
        self, competition_id, category_id, group_id, matchday, matches,
        dry_run: bool = False,
    ) -> None:
        if dry_run:
            logger.info(
                f"[DRY-RUN] matchday {matchday.number} + {len(matches)} encuentro(s)"
            )
            return
        self._emit("matchday", {
            "competition_id": competition_id,
            "group_id": group_id,
            "number": matchday.number,
            "date": _iso(matchday.date),
            "status": matchday.status,
            "pdf_url": _clean(matchday.pdf_url),
            "pdf_hash": _clean(matchday.pdf_hash),
        })
        for match in matches:
            enc = {
                "id": _g(match, "id"),
                "competition_id": competition_id,
                "group_id": group_id,
                "matchday_number": _g(match, "matchday_number"),
                "date": _iso(_g(match, "date")),
                "status": _g(match, "status"),
                "acta_url": _clean(_g(match, "acta_url")),
                "team_local_id": _g(match, "team_local_id"),
                "team_local_name": _g(match, "team_local_name"),
                "score_local": _g(match, "score_local"),
                "team_visitor_id": _g(match, "team_visitor_id"),
                "team_visitor_name": _g(match, "team_visitor_name"),
                "score_visitor": _g(match, "score_visitor"),
            }
            # Terreny de joc / àrbitre: solo se escriben cuando vienen con valor,
            # para que una re-pasada desde otra ruta (p.ej. sin esta info) no
            # pise con NULL un dato ya guardado (el upsert omite la columna
            # ausente y conserva el valor existente).
            venue = _clean(_g(match, "venue", ""))
            referee = _clean(_g(match, "referee", ""))
            if venue:
                enc["venue"] = venue
            if referee:
                enc["referee"] = referee
            self._emit("encounter", enc)
            for im in _g(match, "individual_matches", []) or []:
                self._emit("individual_match", {
                    "competition_id": competition_id,
                    "encounter_id": _g(match, "id"),
                    "ord": _g(im, "order"),
                    "player_local_lic": _clean(_g(im, "player_local_license", "")),
                    "player_local_name": _g(im, "player_local_name"),
                    "player_visitor_lic": _clean(_g(im, "player_visitor_license", "")),
                    "player_visitor_name": _g(im, "player_visitor_name"),
                    "winner": _g(im, "winner"),
                    "sets": _sets_json(_g(im, "sets", [])),
                    "is_doubles": 1 if _g(im, "is_doubles", False) else 0,
                    "player_local_lic_2": _clean(_g(im, "player_local_license_2", "")),
                    "player_local_name_2": _clean(_g(im, "player_local_name_2", "")),
                    "player_visitor_lic_2": _clean(_g(im, "player_visitor_license_2", "")),
                    "player_visitor_name_2": _clean(_g(im, "player_visitor_name_2", "")),
                })

    # ---------------------------------------------------------------- rànquings
    def save_ranking_root(self, competition_id, data, dry_run: bool = False) -> None:
        # Derivable en SQL (player_count = COUNT, last_edition = MAX). No-op.
        logger.debug(f"D1Client: save_ranking_root ignorado para {competition_id} (derivable)")

    def save_ranking_edition(
        self, competition_id, edition_id, edition, categories, dry_run: bool = False,
    ) -> None:
        if dry_run:
            logger.info(f"[DRY-RUN] ranking_edition {competition_id}/{edition_id}")
            return
        month = edition.get("month")
        year = edition.get("year")
        # FCTT publica el mes como nombre ("Setembre") y no imprime el año, así que
        # `to_dict()` deja month=str/"" y no trae year → ambos NULL. Se derivan del
        # label ("Setembre 2026/2027"). La RFETM ya los trae como int y no se toca.
        if not isinstance(month, int) or not isinstance(year, int):
            y, m = parse_ranking_label(edition.get("label") or "")
            if not isinstance(month, int):
                month = m
            if not isinstance(year, int):
                year = y
        self._emit("ranking_edition", {
            "federation": _federation_for(competition_id),
            "season": _season_for(competition_id),
            "edition_id": edition_id,
            "label": edition.get("label"),
            "year": year if isinstance(year, int) else None,
            "month": month if isinstance(month, int) else None,
        })
        # Los `top` por categoría NO se guardan: en SQL se derivan ordenando
        # ranking_position (idx_pos_top). Es la simplificación que el handover
        # señala frente al documento `top` precalculado de Firestore.

    def save_ranking_players(
        self, competition_id, players, dry_run: bool = False, merge: bool = True,
    ) -> None:
        if dry_run:
            logger.info(f"[DRY-RUN] {len(players)} jugador(es) de ranking → {competition_id}")
            return
        fed = _federation_for(competition_id)
        season = _season_for(competition_id)
        for code, doc in players:
            if fed == "RFETM":
                self._emit_rfetm_player(fed, season, code, doc)
            else:
                self._emit_fctt_player(fed, season, code, doc)

    def _emit_fctt_player(self, fed, season, code, doc) -> None:
        # history FCTT: {categoría: {edición: {pos, rk, d_rk, rd, pj, pg, pp, p_any, classified}}}
        # rk/pj/pg/pp son compartidos entre categorías → snapshot; pos por categoría → position.
        sex = _clean(doc.get("gender"))
        self._emit("player", {
            "federation": fed, "lic": code,
            "name": doc.get("name"), "club": _clean(doc.get("club")),
            "fa": None, "sex": sex, "birth_year": None,
            "cat": _clean(doc.get("edat_fctt")), "updated_at": doc.get("updated_at"),
        })
        snap_seen: set[str] = set()
        for category, by_edition in (doc.get("history") or {}).items():
            for edition_id, h in by_edition.items():
                self._emit("ranking_position", {
                    "federation": fed, "season": season, "edition_id": edition_id,
                    "lic": code, "category": category, "sex": sex, "pos": h.get("pos"),
                })
                if edition_id not in snap_seen:
                    snap_seen.add(edition_id)
                    rk, d_rk = h.get("rk"), h.get("d_rk")
                    self._emit("ranking_snapshot", {
                        "federation": fed, "season": season, "edition_id": edition_id,
                        "lic": code, "pts": rk,
                        "pts_prev": (rk - d_rk) if rk is not None and d_rk is not None else None,
                        "d_pts": d_rk, "rd": h.get("rd"),
                        "pj": h.get("pj"), "pg": h.get("pg"), "pp": h.get("pp"),
                        "p_any": h.get("p_any"),
                        "classified": 1 if h.get("classified") else 0,
                        "by_event": None,
                    })

    def _emit_rfetm_player(self, fed, season, code, doc) -> None:
        # history RFETM: {edición: {pts, pts_prev, pj, pg, pp, pos:{cat:n}, by_event?}}
        sex = _clean(doc.get("sex"))
        self._emit("player", {
            "federation": fed, "lic": code,
            "name": doc.get("name"), "club": _clean(doc.get("club")),
            "fa": _clean(doc.get("fa")), "sex": sex,
            "birth_year": doc.get("birth_year") or None,
            "cat": _clean(doc.get("cat")), "updated_at": doc.get("updated_at"),
        })
        for edition_id, h in (doc.get("history") or {}).items():
            pts, pts_prev = h.get("pts"), h.get("pts_prev")
            self._emit("ranking_snapshot", {
                "federation": fed, "season": season, "edition_id": edition_id,
                "lic": code, "pts": pts, "pts_prev": pts_prev,
                "d_pts": (pts - pts_prev) if pts is not None and pts_prev is not None else None,
                "rd": None, "pj": h.get("pj"), "pg": h.get("pg"), "pp": h.get("pp"),
                "p_any": None, "classified": None,
                "by_event": h.get("by_event") or None,
            })
            for category, pos in (h.get("pos") or {}).items():
                self._emit("ranking_position", {
                    "federation": fed, "season": season, "edition_id": edition_id,
                    "lic": code, "category": category, "sex": sex, "pos": pos,
                })

    def save_player_match_logs(self, competition_id, logs, dry_run: bool = False) -> None:
        if dry_run:
            logger.info(f"[DRY-RUN] {len(logs)} match_log(s) → {competition_id}")
            return
        fed = _federation_for(competition_id)
        season = _season_for(competition_id)
        for code, edition_id, payload in logs:
            for seq, m in enumerate(payload.get("matches", [])):
                opp = m.get("opponent", {}) or {}
                self._emit("player_match", {
                    "federation": fed, "season": season, "edition_id": edition_id,
                    "lic": code, "seq": seq,
                    "date": m.get("date"), "event": m.get("event"),
                    "event_type": m.get("event_type"), "category": _clean(m.get("category")),
                    "opp_lic": _clean(opp.get("code")), "opp_name": opp.get("name"),
                    "opp_external": 1 if opp.get("external") else 0,
                    "sets": m.get("sets"), "opp_sets": m.get("opp_sets"),
                    "won": 1 if m.get("won") else 0,
                    "rk": m.get("rk"), "opp_rk": m.get("opp_rk"),
                    "d_rk": m.get("d_rk"), "rd": m.get("rd"),
                })

    def save_player_records(self, competition_id, records, dry_run: bool = False) -> None:
        # Derivable en SQL (SELECT COUNT/SUM sobre player_match). No-op.
        logger.debug(f"D1Client: save_player_records ignorado para {competition_id} (derivable)")


def _iso(value) -> str | None:
    """datetime → ISO; deja pasar str/None."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _sets_json(sets) -> str:
    """list[SetScore|dict] → JSON '[{"local":11,"visitor":9}, …]'."""
    out = []
    for s in sets or []:
        out.append({"local": _g(s, "local"), "visitor": _g(s, "visitor")})
    return json.dumps(out, ensure_ascii=False)
