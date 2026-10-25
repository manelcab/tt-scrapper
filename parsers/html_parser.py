from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from models.competition import Category, Group
from models.team import Team
from utils.logger import get_logger
from utils.text import slugify as _slugify

logger = get_logger(__name__)




def _fix_url(url: str) -> str:
    """Normalize backslashes to forward slashes in URL paths (rtbtt.com quirk)."""
    if "://" in url:
        scheme, rest = url.split("://", 1)
        return scheme + "://" + rest.replace("\\", "/")
    return url.replace("\\", "/")


class HtmlParser:
    # ----------------------------------------------------------------- public

    @staticmethod
    def parse_index(html: str, base_url: str) -> list[Category]:
        """Parse the competition index page and return a list of Category objects."""
        soup = BeautifulSoup(html, "lxml")
        categories: list[Category] = []

        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if not cells:
                    continue

                first_cell = cells[0]
                raw_name = first_cell.get_text(strip=True)
                # Skip header rows and navigation blobs (real category names are short)
                if not raw_name or raw_name.lower() in ("categoria", "categoría", ""):
                    continue
                if len(raw_name) > 30 or "\n" in raw_name or ":" in raw_name:
                    continue

                cat_id, level = HtmlParser._normalize_category_name(raw_name)
                if not cat_id:
                    continue

                groups: list[Group] = []
                for cell in cells[1:]:
                    link = cell.find("a")
                    if not link:
                        continue
                    href = link.get("href", "").strip()
                    group_text = link.get_text(strip=True)
                    if not href or not group_text:
                        continue

                    # rtbtt.com uses javascript:loadurl('real_url','frame') — extract inner URL
                    js_match = re.search(r"loadurl\(['\"]([^'\"]+)['\"]", href)
                    if js_match:
                        href = js_match.group(1)

                    # Build absolute URL and normalize backslashes
                    if href.startswith("http"):
                        source_url = _fix_url(href)
                    elif href.startswith("/") or href.startswith("\\"):
                        source_url = _fix_url(base_url.rstrip("/") + "/" + href.lstrip("\\/"))
                    else:
                        source_url = _fix_url(base_url.rstrip("/") + "/" + href)

                    group_code = group_text.upper()  # e.g. "G1", "G2"
                    group_id = f"{cat_id}_{group_code}"
                    group = Group(
                        id=group_id,
                        name=f"{raw_name} {group_code}",
                        category_id=cat_id,
                        phase="regular",
                        source_url=source_url,
                    )
                    groups.append(group)

                if groups:
                    cat = Category(id=cat_id, name=raw_name, level=level, groups=groups)
                    categories.append(cat)

        # Deduplicate: keep first occurrence of each (cat_id, group_id) pair
        seen: set[tuple[str, str]] = set()
        deduped: list[Category] = []
        for cat in categories:
            unique_groups = []
            for grp in cat.groups:
                key = (cat.id, grp.id)
                if key not in seen:
                    seen.add(key)
                    unique_groups.append(grp)
            if unique_groups:
                cat.groups = unique_groups
                deduped.append(cat)
        return deduped

    @staticmethod
    def parse_group_page(
        html: str,
        base_url: str,
        group_id: str,
    ) -> tuple[list[dict], list[Team]]:
        """Parse a group results page.

        Returns:
            (matchdays_info, teams)
            matchdays_info: list of dicts with keys number, team_local,
                            team_visitor, acta_url
            teams: list of Team objects from the classification table
        """
        soup = BeautifulSoup(html, "lxml")
        matchdays_info: list[dict] = []
        teams: list[Team] = []

        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            # Heuristic: look for rows containing acta links (PDF links, direct or via loadurl)
            for row in rows:
                link = row.find("a", href=re.compile(r"\.pdf|loadurl", re.IGNORECASE))
                if link:
                    cells = row.find_all(["td", "th"])
                    texts = [c.get_text(strip=True) for c in cells]

                    href = link.get("href", "").strip()
                    # Extract real URL from javascript:loadurl('url','frame')
                    js_match = re.search(r"loadurl\(['\"]([^'\"]+)['\"]", href)
                    if js_match:
                        href = js_match.group(1)

                    if href.startswith("http"):
                        acta_url = _fix_url(href)
                    elif href.startswith("/") or href.startswith("\\"):
                        acta_url = _fix_url(base_url.rstrip("/") + "/" + href.lstrip("\\/"))
                    else:
                        acta_url = _fix_url(base_url.rstrip("/") + "/" + href)

                    # Extract jornada number from PDF URL (most reliable source)
                    url_num = re.search(r"jornada(\d+)\.pdf", acta_url, re.IGNORECASE)
                    if url_num:
                        matchday_number = int(url_num.group(1))
                    else:
                        matchday_number = len(matchdays_info) + 1

                    team_local = ""
                    team_visitor = ""

                    for text in texts:
                        if not url_num:
                            m = re.search(r"[Jj]ornada\s*(\d+)", text)
                            if m:
                                matchday_number = int(m.group(1))
                        if " - " in text or " vs " in text.lower():
                            parts = re.split(r"\s*(?:-|vs)\s*", text, maxsplit=1)
                            if len(parts) == 2:
                                team_local, team_visitor = parts[0].strip(), parts[1].strip()

                    if not team_local or not team_visitor:
                        for i, text in enumerate(texts):
                            if i == 0:
                                continue
                            if text and not re.search(r"\.(pdf|html)", text, re.I):
                                if not team_local:
                                    team_local = text
                                elif not team_visitor:
                                    team_visitor = text
                                    break

                    matchdays_info.append(
                        {
                            "number": matchday_number,
                            "team_local": team_local,
                            "team_visitor": team_visitor,
                            "acta_url": acta_url,
                        }
                    )

        # Try to parse classification table (standings)
        teams = HtmlParser._parse_standings(soup, group_id)

        return matchdays_info, teams

    # ----------------------------------------------------------------- private

    @staticmethod
    def _normalize_category_name(name: str) -> tuple[str, int]:
        """Return (category_id, level) from a raw category name.

        Examples:
          "Preferent" → ("PREF", 1)
          "Primera"   → ("1A", 2)
          "Segona"    → ("2A", 3)
          "Tercera"   → ("3A", 4)
          "Primera A" → ("1A", 2)
        """
        upper = name.strip().upper()
        mapping = [
            (re.compile(r"PREFER"), "PREF", 1),
            (re.compile(r"1[AÀ]?|PRIMER[AO]"), "1A", 2),
            (re.compile(r"2[AÀ]?|SEGON[AO]"), "2A", 3),
            (re.compile(r"3[AÀ]?|TERCER[AO]"), "3A", 4),
            (re.compile(r"4[AÀ]?|QUART[AO]"), "4A", 5),
        ]
        for pattern, cat_id, level in mapping:
            if pattern.search(upper):
                return cat_id, level

        # Fallback: slugify
        slug = _slugify(name).upper()
        if slug:
            return slug, 99
        return "", 0

    @staticmethod
    def _parse_standings(soup: BeautifulSoup, group_id: str) -> list[Team]:
        """Try to find and parse a standings/classification table."""
        teams: list[Team] = []
        tables = soup.find_all("table")

        for table in tables:
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            header_cells = rows[0].find_all(["th", "td"])
            header_texts = [c.get_text(strip=True).lower() for c in header_cells]

            # Look for a standings table by checking for typical column headers
            score_headers = {"pts", "pj", "pg", "pp", "pe", "punts", "j", "g", "p"}
            if not any(h in score_headers for h in header_texts):
                continue

            for pos, row in enumerate(rows[1:], start=1):
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                texts = [c.get_text(strip=True) for c in cells]

                # Try to identify the team name cell (longest non-numeric string)
                team_name = ""
                for t in texts:
                    if t and not re.match(r"^\d+$", t) and len(t) > len(team_name):
                        team_name = t

                if not team_name:
                    continue

                team_id = f"{group_id}_{_slugify(team_name)}"

                def _int(val: str) -> int:
                    try:
                        return int(val)
                    except ValueError:
                        return 0

                points = 0
                matches_played = 0
                matches_won = 0
                matches_drawn = 0
                matches_lost = 0

                col_map: dict[str, int] = {
                    h: i for i, h in enumerate(header_texts)
                }
                points = _int(texts[col_map.get("pts", col_map.get("punts", -1))]) if col_map.get("pts") is not None or col_map.get("punts") is not None else 0
                matches_played = _int(texts[col_map["pj"]]) if "pj" in col_map else 0
                matches_won = _int(texts[col_map["pg"]]) if "pg" in col_map else 0
                matches_drawn = _int(texts[col_map["pe"]]) if "pe" in col_map else 0
                matches_lost = _int(texts[col_map["pp"]]) if "pp" in col_map else 0

                teams.append(
                    Team(
                        id=team_id,
                        name=team_name,
                        club=team_name,
                        position=pos,
                        points=points,
                        matches_played=matches_played,
                        matches_won=matches_won,
                        matches_drawn=matches_drawn,
                        matches_lost=matches_lost,
                    )
                )
            if teams:
                break  # use first matching table

        return teams
