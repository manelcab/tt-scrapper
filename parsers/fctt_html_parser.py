"""Parser del HTML de la Federació Catalana de Tennis Taula (fctt.cat).

Forma de la fuente:

    Índice   /competicions-estatals/   h2 = categoría, h3 = grupo, y el primer
                                       enlace a /lligues/<slug>/ que sigue al h3
    Grupo    /lligues/<slug>/          table.br-apic-resultats con la
                                       clasificación + enlaces ?jornada=N
    Jornada  /lligues/<slug>/?jornada=N  la misma clasificación + un bloque por
                                       encuentro: div.match-header seguido de
                                       table.match-results-table

Un encuentro son hasta 7 partidos: individuales (A/B/C contra X/Y/Z) y el doble
(fila «Db»), que solo se juegan si el marcador lo pide. En el doble la celda de
jugador lleva **dos** enlaces, uno por jugador, cada uno con su `codi_jugador`.

Nota sobre `codi_jugador`: es el identificador interno de FCTT, no el número de
licencia de la RFETM. Se guarda igualmente en los campos `*_license` porque es
lo que identifica al jugador en esta fuente.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.parse as urlparse
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup, Tag

from models.competition import Category, Group
from models.match import IndividualMatch, Match, SetScore
from models.team import Team
from sources import FCTT, season_year_for_month
from utils.text import slugify

FCTT_BASE = FCTT.base_url
FCTT_INDEX = FCTT.index_url

# Categorías que scrapeamos, indexadas por el texto del h2 normalizado.
#   valor = (id de categoría, nombre, nivel)
#
# La sección «Lligues estatals (grups catalans)» queda deliberadamente fuera:
# son los grupos catalanes de la liga estatal, enlazan a rfetm.es y ya están en
# competitions/rfetm_*. Incluirlos duplicaría los mismos encuentros.
_SCRAPED_CATEGORIES: dict[str, tuple[str, str, int]] = {
    "tdm": ("TDM", "Tercera Divisió Masculina", 3),
    "copa catalana femenina": ("COPA_F", "Copa Catalana Femenina", 4),
}

_DOUBLES_LABEL = "db"
_SET_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")
#: Marcador del encuentro: espacios obligatorios alrededor del guion y máximo
#: dos cifras, para no capturar el año de nombres como "C.T.T. ATENEU 1882".
_SCORE_RE = re.compile(r"(?<=\s)(\d{1,2})\s+-\s+(\d{1,2})(?=\s|$)")
_DATETIME_RE = re.compile(r"(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}))?")
_SEASON_RE = re.compile(r"Temporada\s+(\d{4}\s*/\s*\d{4})", re.IGNORECASE)
_ORDINAL_RE = re.compile(r"^(\d+)\s*[aª]", re.IGNORECASE)
_JORNADA_RE = re.compile(r"[?&]jornada=(\d+)")
_JOR_HDR_RE = re.compile(r"Jornada\s+(\d+)", re.IGNORECASE)
_TEAM_ID_RE = re.compile(r"team_name_id=(\d+)")


# ── Índice ────────────────────────────────────────────────────────────────────


def parse_index(html: str) -> list[Category]:
    """Categorías y grupos del índice, excluyendo la sección estatal."""
    soup = BeautifulSoup(html, "html.parser")
    categories: list[Category] = []

    for h2 in soup.find_all("h2"):
        meta = _SCRAPED_CATEGORIES.get(_norm(h2.get_text(strip=True)))
        if not meta:
            continue
        cat_id, cat_name, level = meta
        category = Category(id=cat_id, name=cat_name, level=level)

        # Los h3 de esta categoría son los que hay hasta el siguiente h2.
        for node in h2.find_all_next(["h2", "h3"]):
            if node.name == "h2":
                break
            label = node.get_text(strip=True)
            url = _first_group_link(node)
            if not label or not url:
                continue
            category.groups.append(
                Group(
                    id=_group_id(cat_id, label),
                    name=label,
                    category_id=cat_id,
                    phase="regular",
                    source_url=url,
                )
            )

        if category.groups:
            categories.append(category)

    return categories


def parse_season_label(html: str) -> Optional[str]:
    """Temporada tal y como la publica la web ("2025/2026"), o None.

    La URL de FCTT no lleva la temporada: la misma dirección sirve siempre la
    vigente. El scraper compara esto con la temporada configurada para no
    escribir la temporada nueva encima de la archivada.
    """
    match = _SEASON_RE.search(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(1))


def parse_jornada_numbers(html: str) -> list[int]:
    """Números de jornada enlazados en la página de un grupo."""
    soup = BeautifulSoup(html, "html.parser")
    numbers = {
        int(m.group(1))
        for a in soup.find_all("a", href=True)
        if (m := _JORNADA_RE.search(a["href"]))
    }
    return sorted(numbers)


# ── Clasificación ─────────────────────────────────────────────────────────────


def parse_standings(html: str, group_id: str) -> list[Team]:
    """Tabla de clasificación (aparece en la página de grupo y en la de jornada).

    Cabecera: EQUIP · EJ · EG · EP · PF · PC · PUNTS. Las filas de datos llevan
    una celda extra al principio con la posición, que no tiene cabecera, así que
    las columnas van desplazadas una posición.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="br-apic-resultats")
    if not table:
        return []

    rows = table.find_all("tr")
    if len(rows) < 2:
        return []

    headers = [_norm(c.get_text(strip=True)) for c in rows[0].find_all(["th", "td"])]
    teams: list[Team] = []

    for position, row in enumerate(rows[1:], start=1):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
        if len(cells) < 2:
            continue

        # Desplazamiento entre cabeceras y celdas (la posición no tiene cabecera)
        offset = len(cells) - len(headers)
        col = {h: i + offset for i, h in enumerate(headers)}

        name = cells[col["equip"]] if "equip" in col else cells[1]
        if not name:
            continue

        teams.append(
            Team(
                id=f"{group_id}_{slugify(name)}",
                name=name,
                club=name,
                position=_int(cells[0]) or position,
                points=_cell_int(cells, col, "punts"),
                matches_played=_cell_int(cells, col, "ej"),
                matches_won=_cell_int(cells, col, "eg"),
                matches_drawn=0,  # la fuente no contempla empates
                matches_lost=_cell_int(cells, col, "ep"),
                # PF/PC: partidos individuales a favor / en contra (desempate).
                games_for=_cell_int(cells, col, "pf"),
                games_against=_cell_int(cells, col, "pc"),
            )
        )

    return teams


# ── Jornada ───────────────────────────────────────────────────────────────────


def parse_group_meta(html: str) -> tuple[str, str]:
    """`(category_id, nombre)` de una página de grupo, derivados del `h1`.

    Sirve para las ligas territoriales (RTB/RTG/RTT), cuyos slugs no codifican la
    categoría de forma fiable. El `h1` sí ("RTB SEGONA A G1", "RTG PREFERENT"):
    la categoría es el `h1` sin el sufijo de grupo "G<n>", y el group_id se toma
    aparte del slug (único por URL). Ej.: "RTB SEGONA A G1" → ("rtb_segona_a",
    "RTB SEGONA A G1").
    """
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    name = h1.get_text(" ", strip=True) if h1 else ""
    category = re.sub(r"\s*G\s*\d+\s*$", "", name, flags=re.IGNORECASE).strip() or name
    return slugify(category), name


def parse_calendar_fixtures(html: str, group_id: str) -> list[Match]:
    """Encuentros del CALENDARIO desde `div.match-container` (rediseño de fctt.cat).

    Cada encuentro es un `div.match-container` con fecha/hora (`match-datetime`),
    equipo local/visitante (`team-home`/`team-away`, con `team_name_id` numérico) y
    el marcador (`match-score`, '- - -' hasta que se juega). Devuelve `Match` con
    `status='scheduled'` (sin marcador) o `'completed'` (con marcador por equipo),
    **sin partidos individuales** —esos, cuando la FCTT los publique con el markup
    nuevo, irán aparte—. El id es compuesto y con `team_name_id`, estable entre el
    fixture y el mismo encuentro ya jugado, para que el upsert los funda.

    La jornada de cada encuentro se toma de la cabecera "Jornada N" que lo precede.
    """
    soup = BeautifulSoup(html, "html.parser")
    fixtures: list[Match] = []
    for mc in soup.find_all("div", class_="match-container"):
        jornada = _jornada_of(mc)
        home = mc.find("div", class_="team-home")
        away = mc.find("div", class_="team-away")
        if jornada == 0 or not home or not away:
            continue
        local_name = home.get_text(" ", strip=True)
        visitor_name = away.get_text(" ", strip=True)
        if not local_name or not visitor_name:
            continue
        if "descansa" in f"{local_name} {visitor_name}".lower():
            continue  # jornada de descanso, no es un encuentro
        local_id = _team_id(home) or slugify(local_name)
        visitor_id = _team_id(away) or slugify(visitor_name)
        score_local, score_visitor = _fixture_score(mc)
        venue, referee = _match_info(mc)
        fixtures.append(Match(
            id=f"{jornada}_{local_id}_{visitor_id}",
            matchday_number=jornada,
            date=_fixture_datetime(mc),
            team_local_id=local_id,
            team_local_name=local_name,
            team_visitor_id=visitor_id,
            team_visitor_name=visitor_name,
            score_local=score_local,
            score_visitor=score_visitor,
            acta_url="",
            status="completed" if score_local is not None else "scheduled",
            venue=venue,
            referee=referee,
            individual_matches=[],
        ))
    return fixtures


def _jornada_of(mc: Tag) -> int:
    h = mc.find_previous(
        lambda t: t.name in ("h2", "h3") and _JOR_HDR_RE.match(t.get_text(strip=True))
    )
    m = _JOR_HDR_RE.match(h.get_text(strip=True)) if h else None
    return int(m.group(1)) if m else 0


def _team_id(cell: Tag) -> Optional[str]:
    a = cell.find("a", href=True)
    if a:
        m = _TEAM_ID_RE.search(a["href"])
        if m:
            return m.group(1)
    return None


def _fixture_datetime(mc: Tag) -> Optional[datetime]:
    div = mc.find("div", class_="match-datetime")
    if not div:
        return None
    txt = div.get_text(" ", strip=True)
    dm = re.search(r"(\d{2})/(\d{2})/\d{4}", txt)
    if not dm:
        return None
    day, month = int(dm.group(1)), int(dm.group(2))
    hh = mm = 0
    tm = re.search(r"(\d{2}):(\d{2})", txt)
    if tm:
        hh, mm = int(tm.group(1)), int(tm.group(2))
    try:
        # Año derivado del mes + temporada (ver sources.season_year_for_month).
        return datetime(season_year_for_month(month), month, day, hh, mm)
    except ValueError:
        return None


def _fixture_score(mc: Tag) -> tuple[Optional[int], Optional[int]]:
    div = mc.find("div", class_="match-score")
    if div:
        m = re.search(r"(\d+)\s*-\s*(\d+)", div.get_text(" ", strip=True))
        if m:
            return int(m.group(1)), int(m.group(2))
    return None, None


def _label_value(div: Optional[Tag]) -> str:
    """Texto de un `div.*-info` quitando su etiqueta en `<strong>`.

    `<div class="field-info"><strong>Terreny de joc:</strong> DIRECCIÓN</div>`
    → "DIRECCIÓN". Devuelve "" si no hay div.
    """
    if div is None:
        return ""
    strong = div.find("strong")
    if strong is not None:
        strong.extract()
    return div.get_text(" ", strip=True).strip(" :")


def _match_info(container: Tag) -> tuple[str, str]:
    """(terreny de joc, àrbitre) del `div.match-info` de un encuentro.

    - Terreny de joc (dirección donde se juega): `div.field-info`.
    - Àrbitre (solo categorías nacionales, no siempre): `div.referee-info`.
    Cualquiera de los dos puede faltar → "".
    """
    info = container.find("div", class_="match-info")
    if info is None:
        return "", ""
    return (
        _label_value(info.find("div", class_="field-info")),
        _label_value(info.find("div", class_="referee-info")),
    )


def parse_jornada(html: str, jornada_n: int, group_id: str) -> list[Match]:
    """Encuentros de una jornada, con sus partidos individuales y el doble.

    Se recorre `div.match-header`, no `table.match-results-table`: hay
    encuentros con resultado y **sin** tabla de partidos —las incomparecencias,
    que se publican como 5-0 o 0-5— y cuentan para la clasificación igual que
    los demás. Recorriendo tablas se perderían.
    """
    soup = BeautifulSoup(html, "html.parser")
    matches: list[Match] = []

    for header in soup.find_all("div", class_="match-header"):
        date, header_score, names = _parse_match_header(header)
        table = _table_of(header)
        # Terreny de joc / àrbitre del contenedor del encuentro (si existe).
        venue, referee = _match_info(
            header.find_parent("div", class_="match-container") or header.parent
        )

        # Nombres y marcador salen SIEMPRE de la cabecera, que es donde van
        # juntos y en el mismo orden. El acta no sirve para esto: hay encuentros
        # en los que lista los equipos al revés (partidos aplazados o jugados en
        # la otra pista), y cruzar nombres del acta con el marcador de la
        # cabecera invertiría el resultado.
        local_name, visitor_name = names
        individual_matches: list[IndividualMatch] = []

        if table is not None:
            rows = table.find_all("tr")
            header_cells = rows[0].find_all(["th", "td"])
            table_local = header_cells[1].get_text(" ", strip=True)
            table_visitor = header_cells[3].get_text(" ", strip=True)

            if not local_name or not visitor_name:
                # La cabecera no traía marcador y no se pudo separar: nos
                # quedamos con los nombres del acta.
                local_name, visitor_name = table_local, table_visitor

            individual_matches = [
                individual
                for order, row in enumerate(rows[1:], start=1)
                if (individual := _parse_individual(row, order))
            ]

            if _is_swapped(table_local, table_visitor, local_name, visitor_name):
                individual_matches = [_flip(m) for m in individual_matches]

        if not local_name or not visitor_name:
            continue

        if header_score is not None:
            score_local, score_visitor = header_score
        else:
            score_local = sum(1 for m in individual_matches if m.winner == "local")
            score_visitor = sum(1 for m in individual_matches if m.winner == "visitor")

        local_id = slugify(local_name)
        visitor_id = slugify(visitor_name)
        matches.append(
            Match(
                id=f"{jornada_n}_{local_id}_{visitor_id}",
                matchday_number=jornada_n,
                date=date,
                team_local_id=f"{group_id}_{local_id}",
                team_local_name=local_name,
                team_visitor_id=f"{group_id}_{visitor_id}",
                team_visitor_name=visitor_name,
                score_local=score_local,
                score_visitor=score_visitor,
                # Una incomparecencia no tiene partidos pero sí resultado.
                status="completed"
                if (individual_matches or header_score is not None)
                else "scheduled",
                venue=venue,
                referee=referee,
                individual_matches=individual_matches,
            )
        )

    return matches


def parse_team_actas(html: str, viewed_team_id: str) -> list[Match]:
    """Actas (partidos individuales) de la página de un equipo (`?team_name_id=`).

    Cada encuentro es un `div.match-container` con `h2.jornada-results-title`,
    `div.match-teams-row` (equipo LOCAL primero, con su `team_name_id`, marcador) y,
    si se jugó, una `table.match-results-table` con los individuales.

    Solo se devuelven los encuentros en los que el equipo de la página es el
    **LOCAL** (`viewed_team_id == home_id`): cada encuentro se lee así una única vez
    (evita duplicar, ya que aparece en las dos páginas), y como la página orienta el
    acta al equipo visto (columna A/B/C), al ser el local queda local=A/B/C, que es
    justo lo que espera `_parse_individual`. El id de encuentro coincide con el del
    calendario (`{jornada}_{idLocal}_{idVisitante}`), así que las actas se anexan a
    los `encounter` ya cargados.
    """
    soup = BeautifulSoup(html, "html.parser")
    viewed = str(viewed_team_id)
    matches: list[Match] = []

    for mc in soup.find_all("div", class_="match-container"):
        table = mc.find("table", class_=lambda c: c and "match-results-table" in c)
        if table is None:
            continue  # encuentro sin acta (no jugado o incomparecencia)

        title = mc.find(class_="jornada-results-title")
        jm = re.search(r"(\d+)", title.get_text(" ", strip=True)) if title else None
        row = mc.find(class_="match-teams-row")
        links = (
            [a for a in row.find_all("a", href=True) if "team_name_id=" in a["href"]]
            if row else []
        )
        if not jm or len(links) < 2:
            continue
        jornada = int(jm.group(1))
        home_id = _TEAM_ID_RE.search(links[0]["href"]).group(1)
        away_id = _TEAM_ID_RE.search(links[1]["href"]).group(1)
        if home_id != viewed:
            continue  # se leerá desde la página del local

        home_name = links[0].get_text(strip=True)
        away_name = links[1].get_text(strip=True)
        trs = table.find_all("tr")
        individual = [
            im for order, r in enumerate(trs[1:], start=1)
            if (im := _parse_individual(r, order))
        ]
        if not individual:
            continue

        # El acta orienta sus columnas por su cuenta (no por el local de la
        # cabecera): la cabecera de la tabla nombra los dos equipos y, si van al
        # revés que el LOCAL/visitante reales, se giran los individuales.
        header_cells = trs[0].find_all(["th", "td"])
        if len(header_cells) >= 4 and _is_swapped(
            header_cells[1].get_text(" ", strip=True),
            header_cells[3].get_text(" ", strip=True),
            home_name, away_name,
        ):
            individual = [_flip(im) for im in individual]

        sc = re.search(r"(\d+)\s*-\s*(\d+)", row.get_text(" ", strip=True))
        venue, referee = _match_info(mc)
        matches.append(Match(
            id=f"{jornada}_{home_id}_{away_id}",
            matchday_number=jornada,
            date=_fixture_datetime(mc),
            team_local_id=home_id,
            team_local_name=home_name,
            team_visitor_id=away_id,
            team_visitor_name=away_name,
            score_local=int(sc.group(1)) if sc else None,
            score_visitor=int(sc.group(2)) if sc else None,
            acta_url="",
            status="completed",
            venue=venue,
            referee=referee,
            individual_matches=individual,
        ))
    return matches


def _table_of(header: Tag) -> Optional[Tag]:
    """Tabla de partidos de este encuentro, o None si no publicó acta."""
    table = header.find_next("table", class_="match-results-table")
    if table is None:
        return None
    # Si entre la tabla y su cabecera hay otro match-header, la tabla es del
    # encuentro siguiente y este se quedó sin acta.
    if table.find_previous("div", class_="match-header") is not header:
        return None
    return table if len(table.find_all("tr")) >= 2 else None


def _parse_match_header(
    header: Tag,
) -> tuple[Optional[datetime], Optional[tuple[int, int]], tuple[str, str]]:
    """Fecha, marcador y equipos de un `div.match-header`.

    Texto tipo: "27/09/2025 17:00 MIRÓ GANXETS REUS 3 - 4 ATT PREMIA DE MAR".

    El marcador se localiza exigiendo espacios alrededor del guion y como mucho
    dos cifras por lado; así no se confunde con los años que llevan algunos
    clubes en el nombre ("C.T.T. ATENEU 1882").
    """
    text = header.get_text(" ", strip=True)

    date: Optional[datetime] = None
    if dt_match := _DATETIME_RE.search(text):
        day, time = dt_match.group(1), dt_match.group(2)
        try:
            date = datetime.strptime(f"{day} {time or '00:00'}", "%d/%m/%Y %H:%M")
        except ValueError:
            date = None
        text = text[dt_match.end():].strip()

    score_match = _SCORE_RE.search(text)
    if not score_match:
        return date, None, (text.strip(), "")

    score = (int(score_match.group(1)), int(score_match.group(2)))
    local_name = text[: score_match.start()].strip()
    visitor_name = text[score_match.end():].strip()
    return date, score, (local_name, visitor_name)


def _parse_individual(row: Tag, order: int) -> Optional[IndividualMatch]:
    """Una fila de `table.match-results-table` → un partido individual o doble."""
    cells = row.find_all(["th", "td"])
    if len(cells) < 10:
        return None

    local_players = _players(cells[1])
    visitor_players = _players(cells[3])
    if not local_players or not visitor_players:
        return None

    label = _norm(cells[0].get_text(strip=True))
    is_doubles = label == _DOUBLES_LABEL or len(local_players) > 1

    sets = [s for cell in cells[4:9] if (s := _parse_set(cell.get_text(strip=True)))]
    if not sets:
        return None

    sets_local = sum(1 for s in sets if s.local > s.visitor)
    sets_visitor = len(sets) - sets_local

    individual = IndividualMatch(
        id=str(order),
        order=order,
        player_local_name=local_players[0][0],
        player_visitor_name=visitor_players[0][0],
        sets=sets,
        winner="local" if sets_local > sets_visitor else "visitor",
        player_local_license=local_players[0][1],
        player_visitor_license=visitor_players[0][1],
        is_doubles=is_doubles,
    )

    if is_doubles and len(local_players) > 1 and len(visitor_players) > 1:
        individual.player_local_name_2 = local_players[1][0]
        individual.player_local_license_2 = local_players[1][1]
        individual.player_visitor_name_2 = visitor_players[1][0]
        individual.player_visitor_license_2 = visitor_players[1][1]

    return individual


def _is_swapped(
    table_local: str, table_visitor: str, local_name: str, visitor_name: str
) -> bool:
    """¿El acta lista los equipos al revés que la cabecera?"""
    return (
        _norm(table_local) == _norm(visitor_name)
        and _norm(table_visitor) == _norm(local_name)
        and _norm(local_name) != _norm(visitor_name)
    )


def _flip(match: IndividualMatch) -> IndividualMatch:
    """Reexpresa un partido con los lados intercambiados.

    Se usa cuando el acta viene girada respecto a la cabecera: así todo —
    jugadores, sets y ganador — queda en términos del local del encuentro.
    """
    match.player_local_name, match.player_visitor_name = (
        match.player_visitor_name,
        match.player_local_name,
    )
    match.player_local_license, match.player_visitor_license = (
        match.player_visitor_license,
        match.player_local_license,
    )
    match.player_local_name_2, match.player_visitor_name_2 = (
        match.player_visitor_name_2,
        match.player_local_name_2,
    )
    match.player_local_license_2, match.player_visitor_license_2 = (
        match.player_visitor_license_2,
        match.player_local_license_2,
    )
    match.sets = [SetScore(local=s.visitor, visitor=s.local) for s in match.sets]
    match.winner = "visitor" if match.winner == "local" else "local"
    return match


def _players(cell: Tag) -> list[tuple[str, str]]:
    """(nombre, codi_jugador) de cada jugador de la celda.

    Dos enlaces = doble. Sin enlaces se cae al texto plano, para no perder la
    fila cuando la web publica un jugador sin ficha (incomparecencias, etc.).
    """
    players: list[tuple[str, str]] = []
    for link in cell.find_all("a", href=True):
        query = urlparse.parse_qs(urlparse.urlparse(link["href"]).query)
        code = query.get("codi_jugador", [""])[0]
        name = link.get_text(" ", strip=True)
        if name:
            players.append((name, code))

    if not players:
        text = cell.get_text(" ", strip=True)
        if text:
            players.append((text, ""))

    return players


# ── Helpers ───────────────────────────────────────────────────────────────────


def _first_group_link(node: Tag) -> Optional[str]:
    """Primer enlace a /lligues/ que sigue al nodo en orden de documento.

    La web está hecha con Elementor y el título del grupo y su botón viven en
    widgets hermanos, sin un contenedor común, así que la relación fiable es el
    orden de lectura.
    """
    for link in node.find_all_next("a", href=True):
        href = link["href"]
        if "/lligues/" in href:
            return href if href.startswith("http") else f"{FCTT_BASE}{href}"
    return None


def _group_id(category_id: str, label: str) -> str:
    """"TDM G1" → TDM_G1 · "1a Divisió" → COPA_F_1A."""
    normalized = re.sub(r"[\s\-]+", "_", label.strip()).upper()
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")

    if normalized.startswith(f"{category_id}_") or normalized == category_id:
        return normalized
    if ordinal := _ORDINAL_RE.match(label.strip()):
        return f"{category_id}_{ordinal.group(1)}A"
    return f"{category_id}_{slugify(label).upper()}"


def _parse_set(text: str) -> Optional[SetScore]:
    match = _SET_RE.match(text.strip())
    if not match:
        return None
    return SetScore(local=int(match.group(1)), visitor=int(match.group(2)))


def _norm(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).strip().lower()


def _int(text: str) -> int:
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return 0


def _cell_int(cells: list[str], col: dict[str, int], key: str) -> int:
    index = col.get(key)
    if index is None or index < 0 or index >= len(cells):
        return 0
    return _int(cells[index])
