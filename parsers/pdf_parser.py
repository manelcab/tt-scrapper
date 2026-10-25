from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

from models.match import IndividualMatch, Match, SetScore
from utils.logger import get_logger

logger = get_logger(__name__)


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    lower = ascii_text.lower()
    return re.sub(r"[\s\-]+", "_", lower).strip("_")


# Individual match line:  A 2452 ESPERT Nil Y 13136 DOMINGUEZ Eric 3 0
_IND_MATCH_RE = re.compile(
    r"^([ABC])\s+(\d+)\s+(.+?)\s+([XYZ])\s+(\d+)\s+(.+?)\s+([0-3])\s+([0-3])\s*$"
)

# Doubles lines (only in 4-3 / 3-4 matches):
#   D1 15266 SUBIRANA Biel D1 14174 RAMÍREZ Rafael 0 3
#   D2 200 LUCEA Javier D2 7713 FARELL Jordi
_DOUBLES_D1_RE = re.compile(
    r"^D1\s+(\d+)\s+(.+?)\s+D1\s+(\d+)\s+(.+?)\s+([0-3])\s+([0-3])\s*$"
)
_DOUBLES_D2_RE = re.compile(
    r"^D2\s+(\d+)\s+(.+?)\s+D2\s+(\d+)\s+(.+?)\s*$"
)


class PdfParser:
    def parse_acta(
        self,
        pdf_path: str,
        group_id: str,
        matchday_number: int,
        debug: bool = False,
    ) -> list[Match]:
        try:
            import pdfplumber  # type: ignore
        except ImportError:
            logger.error("pdfplumber no instalado. Ejecuta: pip install pdfplumber")
            return []

        path = Path(pdf_path)
        if not path.exists():
            logger.warning(f"PDF no encontrado: {pdf_path}")
            return []

        matches: list[Match] = []
        try:
            with pdfplumber.open(str(path)) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    try:
                        text = page.extract_text() or ""
                        tables = page.extract_tables() or []

                        if debug:
                            print(f"\n--- PAGE {page_num + 1} TEXT ---")
                            print(text)
                            print(f"\n--- PAGE {page_num + 1} TABLES ---")
                            for t in tables:
                                print(t)

                        # Use RTBTT-specific parser when format is detected
                        if re.search(r"\bActa\s+\d+\b", text):
                            match = self._parse_rtbtt_page(
                                text, group_id, matchday_number, page_num
                            )
                        else:
                            match = self._parse_generic_page(
                                text, tables, group_id, matchday_number, page_num
                            )

                        if match:
                            matches.append(match)
                    except Exception as exc:
                        logger.warning(
                            f"Error procesando página {page_num + 1} de {pdf_path}: {exc}"
                        )
        except Exception as exc:
            logger.warning(f"Error abriendo PDF {pdf_path}: {exc}")

        return matches

    # ----------------------------------------------------------------- RTBTT

    def _parse_rtbtt_page(
        self,
        text: str,
        group_id: str,
        matchday_number: int,
        page_num: int,
    ) -> Optional[Match]:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        # --- Acta header: "Acta N DD/MM/YY TEAM1 TEAM2" ---
        acta_num: Optional[int] = None
        match_date: Optional[datetime] = None
        for line in lines:
            m = re.match(r"Acta\s+(\d+)\s+(\d{2}/\d{2}/\d{2})", line)
            if m:
                acta_num = int(m.group(1))
                d, mo, y = m.group(2).split("/")
                year = 2000 + int(y) if int(y) < 100 else int(y)
                try:
                    match_date = datetime(year, int(mo), int(d))
                except ValueError:
                    pass
                break

        # --- ABC/XYZ line: "ABC TEAM_LOCAL XYZ TEAM_VISITOR" ---
        team_abc: Optional[str] = None
        team_xyz: Optional[str] = None
        for line in lines:
            m = re.match(r"^ABC\s+(.+?)\s+XYZ\s+(.+)$", line)
            if m:
                team_abc = m.group(1).strip()
                team_xyz = m.group(2).strip()
                break

        if not team_abc or not team_xyz:
            logger.debug(
                f"Página {page_num + 1}: no se encontró línea ABC/XYZ, omitiendo"
            )
            return None

        # --- Individual match rows ---
        individual: list[IndividualMatch] = []
        order = 0
        local_slug = _slugify(team_abc)[:25]
        visitor_slug = _slugify(team_xyz)[:25]
        base_match_id = f"{matchday_number}_{local_slug}_vs_{visitor_slug}"
        if acta_num:
            base_match_id = f"{matchday_number}_a{acta_num}_{local_slug}_vs_{visitor_slug}"

        pending_d1: Optional[dict] = None

        for line in lines:
            # Regular individual match (ABC vs XYZ)
            m = _IND_MATCH_RE.match(line)
            if m:
                order += 1
                sets_local = int(m.group(7))
                sets_visitor = int(m.group(8))
                sets = [SetScore(1, 0)] * sets_local + [SetScore(0, 1)] * sets_visitor
                individual.append(IndividualMatch(
                    id=f"{base_match_id}_p{order}",
                    order=order,
                    player_local_name=m.group(3).strip(),
                    player_local_license=m.group(2),
                    player_visitor_name=m.group(6).strip(),
                    player_visitor_license=m.group(5),
                    sets=sets,
                    winner="local" if sets_local > sets_visitor else "visitor",
                ))
                continue

            # Doubles D1 line — store first player pair + score, wait for D2
            m = _DOUBLES_D1_RE.match(line)
            if m:
                pending_d1 = {
                    "local_license": m.group(1),
                    "local_name": m.group(2).strip(),
                    "visitor_license": m.group(3),
                    "visitor_name": m.group(4).strip(),
                    "sets_local": int(m.group(5)),
                    "sets_visitor": int(m.group(6)),
                }
                continue

            # Doubles D2 line — completes the pending D1 into a single doubles match
            m = _DOUBLES_D2_RE.match(line)
            if m and pending_d1:
                order += 1
                d1 = pending_d1
                pending_d1 = None
                sets = [SetScore(1, 0)] * d1["sets_local"] + [SetScore(0, 1)] * d1["sets_visitor"]
                individual.append(IndividualMatch(
                    id=f"{base_match_id}_p{order}",
                    order=order,
                    player_local_name=d1["local_name"],
                    player_local_license=d1["local_license"],
                    player_visitor_name=d1["visitor_name"],
                    player_visitor_license=d1["visitor_license"],
                    sets=sets,
                    winner="local" if d1["sets_local"] > d1["sets_visitor"] else "visitor",
                    is_doubles=True,
                    player_local_name_2=m.group(2).strip(),
                    player_local_license_2=m.group(1),
                    player_visitor_name_2=m.group(4).strip(),
                    player_visitor_license_2=m.group(3),
                ))

        if not individual:
            logger.debug(f"Página {page_num + 1}: no se encontraron partidos individuales")
            return None

        score_local = sum(1 for im in individual if im.winner == "local")
        score_visitor = sum(1 for im in individual if im.winner == "visitor")

        return Match(
            id=base_match_id,
            matchday_number=matchday_number,
            date=match_date,
            team_local_id=f"{group_id}_{local_slug}",
            team_local_name=team_abc,
            team_visitor_id=f"{group_id}_{visitor_slug}",
            team_visitor_name=team_xyz,
            score_local=score_local,
            score_visitor=score_visitor,
            individual_matches=individual,
        )

    # --------------------------------------------------------------- generic fallback

    def _parse_generic_page(
        self,
        text: str,
        tables: list,
        group_id: str,
        matchday_number: int,
        page_num: int,
    ) -> Optional[Match]:
        team_local, team_visitor = self._extract_teams(text)
        if not team_local or not team_visitor:
            return None

        match_date = self._extract_date(text)
        score_local, score_visitor = self._extract_score(text)

        local_slug = _slugify(team_local)[:25]
        visitor_slug = _slugify(team_visitor)[:25]
        match_id = f"{matchday_number}_{local_slug}_vs_{visitor_slug}"

        individual = self._parse_tables(tables, match_id)
        if not individual:
            individual = self._parse_text_matches(text, match_id)

        return Match(
            id=match_id,
            matchday_number=matchday_number,
            date=match_date,
            team_local_id=f"{group_id}_{local_slug}",
            team_local_name=team_local,
            team_visitor_id=f"{group_id}_{visitor_slug}",
            team_visitor_name=team_visitor,
            score_local=score_local,
            score_visitor=score_visitor,
            individual_matches=individual,
        )

    def _extract_teams(self, text: str) -> tuple[str, str]:
        patterns = [
            r"LOCAL[:\s]+([^\n]+)\n.*?VISITANT[:\s]+([^\n]+)",
            r"([A-Za-zÀ-ÖØ-öø-ÿ\s\.]+?)\s+[-–]\s+([A-Za-zÀ-ÖØ-öø-ÿ\s\.]+?)(?:\n|$)",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if m:
                local = m.group(1).strip()
                visitor = m.group(2).strip()
                if len(local) > 2 and len(visitor) > 2:
                    return local, visitor
        return "", ""

    def _extract_date(self, text: str) -> Optional[datetime]:
        m = re.search(r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})", text)
        if m:
            try:
                day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if year < 100:
                    year += 2000
                return datetime(year, month, day)
            except (ValueError, OverflowError):
                pass
        return None

    def _extract_score(self, text: str) -> tuple[int, int]:
        candidates = re.findall(r"\b([0-9]{1,2})\s*[-–]\s*([0-9]{1,2})\b", text)
        for ls, vs in candidates:
            lo, vi = int(ls), int(vs)
            if 0 <= lo <= 18 and 0 <= vi <= 18 and lo + vi <= 18:
                return lo, vi
        return 0, 0

    def _parse_tables(self, tables: list, match_id: str) -> list[IndividualMatch]:
        individual: list[IndividualMatch] = []
        order = 0
        for table in tables:
            for row in table:
                if not row:
                    continue
                cells = [str(c).strip() if c is not None else "" for c in row]
                set_cells = [c for c in cells if re.match(r"\d+[/\-]\d+", c)]
                if not set_cells:
                    continue
                sets = []
                for sc in set_cells:
                    m = re.match(r"(\d+)[/\-](\d+)", sc.strip())
                    if m:
                        sets.append(SetScore(int(m.group(1)), int(m.group(2))))
                if not sets:
                    continue
                non_score = [c for c in cells if c and not re.match(r"^\d+[/\-]\d+$", c)]
                player_local = non_score[0] if non_score else ""
                player_visitor = non_score[1] if len(non_score) > 1 else ""
                sl = sum(1 for s in sets if s.local > s.visitor)
                sv = sum(1 for s in sets if s.visitor > s.local)
                order += 1
                individual.append(IndividualMatch(
                    id=f"{match_id}_p{order}",
                    order=order,
                    player_local_name=player_local,
                    player_visitor_name=player_visitor,
                    sets=sets,
                    winner="local" if sl > sv else "visitor",
                ))
        return individual

    def _parse_text_matches(self, text: str, match_id: str) -> list[IndividualMatch]:
        individual: list[IndividualMatch] = []
        order = 0
        set_re = re.compile(r"\d+[/\-]\d+")
        for line in text.splitlines():
            tokens = set_re.findall(line)
            if len(tokens) < 2:
                continue
            sets = []
            for t in tokens:
                m = re.match(r"(\d+)[/\-](\d+)", t)
                if m:
                    sets.append(SetScore(int(m.group(1)), int(m.group(2))))
            if not sets:
                continue
            remaining = set_re.sub("", line).strip()
            parts = [p.strip() for p in re.split(r"\s{2,}|\t", remaining) if p.strip()]
            sl = sum(1 for s in sets if s.local > s.visitor)
            sv = sum(1 for s in sets if s.visitor > s.local)
            order += 1
            individual.append(IndividualMatch(
                id=f"{match_id}_p{order}",
                order=order,
                player_local_name=parts[0] if parts else "",
                player_visitor_name=parts[1] if len(parts) > 1 else "",
                sets=sets,
                winner="local" if sl > sv else "visitor",
            ))
        return individual
