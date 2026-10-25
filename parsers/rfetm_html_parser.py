from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup, Tag

from models.competition import Category, Group
from models.match import IndividualMatch, Match, Matchday, SetScore
from models.team import Team

from sources import RFETM, season_year_for_month
from utils.text import slugify as _slugify

RFETM_BASE = RFETM.base_url
RFETM_INDEX = RFETM.index_url

_LOCAL_LETTERS = frozenset({"A", "B", "C"})
_VISITOR_LETTERS = frozenset({"X", "Y", "Z"})
_SET_RE = re.compile(r"^(\d+)-(\d+)$")
_LIC_RE = re.compile(r"Lic:\s*(\d+)")
_EQUIPO_RE = re.compile(r"equipo=(\d+)")
_JORNADA_RE = re.compile(r"jornada=(\d+)")
_DATETIME_RE = re.compile(r"(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}))?")
_LUGAR_RE = re.compile(r"Lugar:\s*(.*)", re.IGNORECASE | re.DOTALL)
_ARBITRO_RE = re.compile(r"[ÁA]rbitro\s*:\s*(.*)$", re.IGNORECASE | re.DOTALL)


def _place_referee(table: Tag) -> tuple[str, str]:
    """(Lugar, Árbitro) de una tabla de encuentro RFETM.

    Ambos van juntos en una fila `<b>Lugar: </b>…<br><b>Árbitro: </b>…` dentro
    del encuentro. El árbitro solo aparece en categorías nacionales (y no
    siempre). Cualquiera de los dos puede faltar → "".
    """
    node = table.find(string=lambda x: isinstance(x, str) and "Lugar:" in x)
    if node is None:
        return "", ""
    holder = node.find_parent(["tr", "td", "font"]) or node.parent
    txt = holder.get_text(" ", strip=True)
    m = _LUGAR_RE.search(txt)
    if not m:
        return "", ""
    rest = m.group(1)
    am = _ARBITRO_RE.search(rest)
    referee = am.group(1).strip() if am else ""
    venue = (rest[: am.start()] if am else rest).strip().strip("-").strip()
    return venue, referee

_LIGA_META: dict[str, tuple[str, str, int]] = {
    # liga_param → (id_prefix, display_name, level)
    "MQ==": ("liga1", "Superdivisión", 1),
    "Mg==": ("liga2", "División de Honor", 2),
    "Mw==": ("liga3", "Primera División", 3),
    "NA==": ("liga4", "Segunda División", 4),
}
_SEXO_LABEL = {"M": "Masculina", "F": "Femenina"}


# ─────────────────────────────────────────────────────── index

def parse_index(html: str) -> list[Category]:
    """Return categories + groups from index.php."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    categories: dict[str, Category] = {}

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "jornada=0" not in href or "liga=" not in href:
            continue
        params = _qs(href)
        liga = params.get("liga", "")
        if liga not in _LIGA_META:
            continue
        grupo = params.get("grupo", "0")
        subgrupo = params.get("subgrupo", "S")
        sexo = params.get("sexo", "M")

        id_prefix, display, level = _LIGA_META[liga]
        cat_id = f"{id_prefix}_{sexo}"
        grp_id = f"{id_prefix}_g{grupo}_{sexo}"

        if grp_id in seen:
            continue
        seen.add(grp_id)

        if cat_id not in categories:
            categories[cat_id] = Category(
                id=cat_id,
                name=f"{display} {_SEXO_LABEL.get(sexo, sexo)}",
                level=level,
            )

        grupo_n = int(grupo) if grupo.isdigit() else 0
        grp_name = f"{display} {_SEXO_LABEL.get(sexo, sexo)}"
        if grupo_n > 0:
            grp_name += f" - Grupo {grupo_n}"

        source_url = (
            f"{RFETM_BASE}/view.php"
            f"?liga={liga}&grupo={grupo}&subgrupo={subgrupo}"
            f"&jornada=0&sexo={sexo}"
        )
        categories[cat_id].groups.append(
            Group(
                id=grp_id,
                name=grp_name,
                category_id=cat_id,
                phase=subgrupo,
                source_url=source_url,
            )
        )

    return sorted(categories.values(), key=lambda c: c.level)


# ─────────────────────────────────────────────────────── jornada nav

def parse_max_jornada(html: str) -> int:
    """Return highest jornada number from navigation links."""
    soup = BeautifulSoup(html, "html.parser")
    max_j = 0
    for a in soup.find_all("a", href=True):
        m = _JORNADA_RE.search(a["href"])
        if m:
            n = int(m.group(1))
            if n > max_j:
                max_j = n
    return max_j


# ─────────────────────────────────────────────────────── standings

def parse_standings(html: str) -> list[Team]:
    """Parse the standings table from any jornada page.

    Guiado por la cabecera: la Superdivisión (liga1) trae la columna EE
    (empates), pero 2ª/3ª/4ª la omiten, así que la tabla tiene 9 u 8 columnas
    según el grupo. No se puede asumir una posición fija (un posicional ingenuo
    deja PJ a 0 y desplaza PG/PE); se mapea cada dato por el nombre de su
    columna (EJ/EG/EE/EP/PF/PC/PTOS). Sin cabecera reconocible, se cae a un
    posicional anclado por la derecha (PTOS/PC/PF siempre son las tres últimas).
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find(
        "table",
        class_=lambda c: c and "table-striped" in c and "table-hover" in c,
    )
    if not table:
        return []

    rows = table.find_all("tr")

    # Mapa columna→índice a partir de la fila de cabecera (la que lleva 'EQUIPO').
    colmap: dict[str, int] = {}
    for row in rows:
        labels = [c.get_text(strip=True).upper().rstrip(".")
                  for c in row.find_all(["th", "td"])]
        if "EQUIPO" in labels:
            for i, lab in enumerate(labels):
                if lab in ("EJ", "EG", "EE", "EP", "PF", "PC", "PTOS"):
                    colmap[lab] = i
            break

    teams: list[Team] = []
    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 8:
            continue
        texts = [td.get_text(strip=True) for td in tds]

        try:
            pos = int(texts[0])
        except ValueError:
            continue

        team_a = tds[1].find("a")
        team_name = team_a.get_text(strip=True) if team_a else tds[1].get_text(strip=True)
        team_href = team_a.get("href", "") if team_a else ""
        team_id_m = re.search(r"eqdat=(\d+)", team_href)
        team_id = team_id_m.group(1) if team_id_m else _slugify(team_name)

        if colmap:
            ej = _col_val(texts, colmap, "EJ")
            eg = _col_val(texts, colmap, "EG")
            ee = _col_val(texts, colmap, "EE")
            ep = _col_val(texts, colmap, "EP")
            pf = _col_val(texts, colmap, "PF")
            pc = _col_val(texts, colmap, "PC")
            pts = _col_val(texts, colmap, "PTOS")
        else:
            # Fallback sin cabecera: PTOS/PC/PF van anclados por la derecha; entre
            # el equipo y PF van EJ, EG, [EE], EP. Con 4 columnas intermedias hay
            # EE (9 col); con 3, se omite (8 col, ligas sin empates).
            pts, pc, pf = _int(texts[-1]), _int(texts[-2]), _int(texts[-3])
            ej, eg = _int(texts[2]), _int(texts[3])
            if len(tds) >= 9:
                ee, ep = _int(texts[4]), _int(texts[5])
            else:
                ee, ep = 0, _int(texts[4])

        teams.append(Team(
            id=team_id,
            name=team_name,
            club="",
            position=pos,
            points=pts,
            matches_played=ej,
            matches_won=eg,
            matches_drawn=ee,
            matches_lost=ep,
            # PF/PC: partidos individuales a favor / en contra (desempate).
            games_for=pf,
            games_against=pc,
        ))
    return teams


# ─────────────────────────────────────────────────────── matches

def parse_matches(html: str, jornada_n: int) -> list[Match]:
    """Parse all team matches (with individual results) from a jornada page."""
    soup = BeautifulSoup(html, "html.parser")
    matches: list[Match] = []
    for table in soup.find_all("table", class_="table-sm"):
        if "border: #F1F1F0" not in table.get("style", ""):
            continue
        match = _parse_match_table(table, jornada_n)
        if match:
            matches.append(match)
    return matches


def parse_fixtures(html: str, jornada_n: int) -> list[Match]:
    """Partidos de CALENDARIO aún no disputados de una jornada.

    La RFETM los publica en tablas `table-borderless` (fecha+hora, equipo local,
    "-", equipo visitante, y el lugar) — distintas de las tablas con borde
    `#F1F1F0` de los encuentros ya jugados que lee `parse_matches`. Sirven para
    que el agente sepa el PRÓXIMO partido de un equipo/jugador. Se devuelven como
    `Match` con `status="scheduled"`, sin marcador ni partidos individuales, y con
    el MISMO id compuesto (jornada+equipos) que tendrán al jugarse, para que el
    upsert los actualice en sitio (scheduled → completed) sin duplicar.
    """
    soup = BeautifulSoup(html, "html.parser")
    fixtures: list[Match] = []
    for table in soup.find_all("table", class_="table-borderless"):
        first_tr = table.find("tr")
        if not first_tr:
            continue
        team_links = [
            a for a in first_tr.find_all("a", href=True) if "equipo=" in a["href"]
        ]
        if len(team_links) < 2:
            continue  # no es una fila de calendario

        match_date = _parse_datetime(first_tr.find("td").get_text(" ", strip=True))

        local_a, visitor_a = team_links[0], team_links[1]
        local_name = local_a.get_text(strip=True)
        visitor_name = visitor_a.get_text(strip=True)
        local_m = _EQUIPO_RE.search(local_a.get("href", ""))
        visitor_m = _EQUIPO_RE.search(visitor_a.get("href", ""))
        local_id = local_m.group(1) if local_m else _slugify(local_name)
        visitor_id = visitor_m.group(1) if visitor_m else _slugify(visitor_name)

        venue, referee = _place_referee(table)
        fixtures.append(Match(
            id=f"{jornada_n}_{local_id}_{visitor_id}",
            matchday_number=jornada_n,
            date=match_date,
            team_local_id=local_id,
            team_local_name=local_name,
            team_visitor_id=visitor_id,
            team_visitor_name=visitor_name,
            score_local=None,
            score_visitor=None,
            acta_url="",
            status="scheduled",
            venue=venue,
            referee=referee,
            individual_matches=[],
        ))
    return fixtures


def _parse_match_table(table: Tag, jornada_n: int) -> Optional[Match]:
    rows = table.find_all("tr", recursive=False)
    if not rows:
        return None

    tds = rows[0].find_all("td", recursive=False)
    if len(tds) < 5:
        return None

    # tds[0]: date (colspan=2)
    match_date = _parse_date(tds[0].get_text(separator=" ", strip=True))

    # tds[1]: local team
    local_a = tds[1].find("a")
    local_name = local_a.get_text(strip=True) if local_a else tds[1].get_text(strip=True)
    local_href = local_a.get("href", "") if local_a else ""
    local_id_m = _EQUIPO_RE.search(local_href)
    local_id = local_id_m.group(1) if local_id_m else _slugify(local_name)

    # tds[2]/tds[3]: scores
    try:
        score_local = int(tds[2].get_text(strip=True))
        score_visitor = int(tds[3].get_text(strip=True))
    except (ValueError, IndexError):
        return None

    # tds[4]: visitor team
    visitor_a = tds[4].find("a")
    visitor_name = visitor_a.get_text(strip=True) if visitor_a else tds[4].get_text(strip=True)
    visitor_href = visitor_a.get("href", "") if visitor_a else ""
    visitor_id_m = _EQUIPO_RE.search(visitor_href)
    visitor_id = visitor_id_m.group(1) if visitor_id_m else _slugify(visitor_name)

    # tds[5+]: acta link (número de partido de la RFETM). Se guarda como acta_url,
    # pero el ID del encuentro es SIEMPRE el compuesto jornada+equipos: ese número
    # de partido solo existe una vez jugado, así que un partido de calendario
    # (parse_fixtures) y el mismo ya disputado (parse_matches) comparten id y el
    # upsert lo actualiza en sitio (scheduled → completed) en vez de duplicarlo.
    acta_url = ""
    match_id = f"{jornada_n}_{local_id}_{visitor_id}"
    for td in tds[5:]:
        a = td.find("a")
        if a and "/partido/" in (a.get("href", "") or ""):
            acta_url = a["href"]
            break

    # Individual matches from nested borderer table
    individual_matches: list[IndividualMatch] = []
    for td in table.find_all("td"):
        borderer = td.find("table", class_="table-borderer")
        if borderer:
            individual_matches = _parse_individual_matches(
                borderer, local_name, visitor_name
            )
            break

    status = "completed" if (score_local + score_visitor > 0 or individual_matches) else "pending"
    venue, referee = _place_referee(table)

    return Match(
        id=match_id,
        matchday_number=jornada_n,
        date=match_date,
        team_local_id=local_id,
        team_local_name=local_name,
        team_visitor_id=visitor_id,
        team_visitor_name=visitor_name,
        score_local=score_local,
        score_visitor=score_visitor,
        acta_url=acta_url,
        status=status,
        venue=venue,
        referee=referee,
        individual_matches=individual_matches,
    )


def _parse_individual_matches(
    borderer: Tag,
    outer_local: str = "",
    outer_visitor: str = "",
) -> list[IndividualMatch]:
    rows = borderer.find_all("tr")
    if not rows:
        return []

    individual_matches: list[IndividualMatch] = []
    order = 0

    # Determine which column is the local team by comparing the inner header's
    # first team name against the outer match's local team name.
    # The inner table sometimes lists visitor first, making letters A/B/C unreliable.
    first_col_is_local = _infer_first_col_is_local(rows[0], outer_local, outer_visitor)

    for row in rows[1:]:  # skip header row
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        letter1 = cells[0].get_text(strip=True).upper()
        if letter1 not in _LOCAL_LETTERS and letter1 not in _VISITOR_LETTERS:
            continue

        lic1, name1 = _parse_player_cell(cells[1])
        lic2, name2 = _parse_player_cell(cells[3])

        if first_col_is_local:
            local_lic, local_name = lic1, name1
            visitor_lic, visitor_name = lic2, name2
        else:
            local_lic, local_name = lic2, name2
            visitor_lic, visitor_name = lic1, name1

        # Set scores from cells[4..8] — perspective of left (col-1) player
        # Stop at hidden-xs cells (game-score / running-score columns)
        sets: list[SetScore] = []
        for cell in cells[4:]:
            cell_cls = cell.get("class") or []
            if "hidden-xs" in cell_cls or "hidden-sm" in cell_cls:
                break
            txt = cell.get_text(strip=True)
            m = _SET_RE.match(txt)
            if not m:
                break
            a, b = int(m.group(1)), int(m.group(2))
            if first_col_is_local:
                sets.append(SetScore(local=a, visitor=b))
            else:
                sets.append(SetScore(local=b, visitor=a))

        if not sets:
            continue

        sets_local = sum(1 for s in sets if s.local > s.visitor)
        sets_visitor = sum(1 for s in sets if s.visitor > s.local)
        winner = "local" if sets_local > sets_visitor else "visitor"

        order += 1
        individual_matches.append(IndividualMatch(
            id=str(order),
            order=order,
            player_local_name=local_name,
            player_visitor_name=visitor_name,
            sets=sets,
            winner=winner,
            player_local_license=local_lic,
            player_visitor_license=visitor_lic,
        ))

    return individual_matches


def _infer_first_col_is_local(header_row: Tag, outer_local: str, outer_visitor: str) -> bool:
    """Return True if the first player column in the inner table belongs to the local team.

    The inner table header names the two teams (left side first). We compare that
    first name against the outer match's local/visitor names to resolve the order.
    """
    if not outer_local and not outer_visitor:
        return True

    cells = header_row.find_all(["th", "td"])

    # Prefer cells that carry a tdacta class (used by RFETM for team-name headers)
    tdacta = [c for c in cells if "tdacta" in (c.get("class") or [])]
    first_team = tdacta[0].get_text(strip=True) if tdacta else (
        cells[1].get_text(strip=True) if len(cells) >= 2 else ""
    )

    if not first_team:
        return True
    if _name_fuzzy_match(first_team, outer_local):
        return True
    if _name_fuzzy_match(first_team, outer_visitor):
        return False
    return True  # fallback: assume normal order


# ─────────────────────────────────────────────────────── helpers

def _name_fuzzy_match(a: str, b: str) -> bool:
    """True if one team name is a substring of the other (case-insensitive)."""
    if not a or not b:
        return False
    al, bl = a.lower().strip(), b.lower().strip()
    return al in bl or bl in al


def _parse_player_cell(td: Tag) -> tuple[str, str]:
    text = td.get_text(separator=" ", strip=True)
    lic_m = _LIC_RE.search(text)
    license_ = lic_m.group(1) if lic_m else ""
    a_tag = td.find("a")
    name = a_tag.get_text(strip=True) if a_tag else ""
    return license_, name


def _build_date(dmy: str, hm: Optional[str]) -> Optional[datetime]:
    """dd/mm/yyyy (+ HH:MM opcional) → datetime, pero DERIVANDO el año del mes +
    la temporada en vez del que imprime la RFETM: hay encuentros publicados con el
    año mal (p. ej. '2031', o octubre de '2027'). Ver sources.season_year_for_month.
    """
    dm = re.match(r"^(\d{2})/(\d{2})/\d{4}$", dmy)
    if not dm:
        return None
    day, month = int(dm.group(1)), int(dm.group(2))
    hh, mm = 0, 0
    if hm:
        tm = re.match(r"^(\d{2}):(\d{2})$", hm)
        if tm:
            hh, mm = int(tm.group(1)), int(tm.group(2))
    try:
        return datetime(season_year_for_month(month), month, day, hh, mm)
    except ValueError:
        return None


def _parse_date(text: str) -> Optional[datetime]:
    m = re.search(r"(\d{2}/\d{2}/\d{4})", text)
    return _build_date(m.group(1), None) if m else None


def _parse_datetime(text: str) -> Optional[datetime]:
    """Fecha con hora de saque opcional ("02/10/2026 17:30"). La hora importa
    para los próximos partidos, así que se conserva cuando la publica la fuente."""
    m = _DATETIME_RE.search(text)
    return _build_date(m.group(1), m.group(2)) if m else None


def _qs(href: str) -> dict[str, str]:
    if "?" not in href:
        return {}
    result: dict[str, str] = {}
    for part in href.split("?", 1)[1].split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k] = v
    return result


def _int(text: str) -> int:
    try:
        return int(re.sub(r"[^0-9\-]", "", text) or "0")
    except ValueError:
        return 0


def _col_val(texts: list[str], colmap: dict[str, int], name: str) -> int:
    """Valor entero de la columna `name` según el mapa de cabecera (0 si falta)."""
    i = colmap.get(name)
    return _int(texts[i]) if i is not None and i < len(texts) else 0


