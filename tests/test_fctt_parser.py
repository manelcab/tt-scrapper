from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers.fctt_html_parser import (
    parse_calendar_fixtures,
    parse_index,
    parse_jornada,
    parse_jornada_numbers,
    parse_season_label,
    parse_standings,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _index_html() -> str:
    return (FIXTURES / "fctt_index.html").read_text(encoding="utf-8")


def _jornada_html() -> str:
    return (FIXTURES / "fctt_jornada.html").read_text(encoding="utf-8")


# ── Índice ────────────────────────────────────────────────────────────────────


def test_parse_index_returns_scraped_categories():
    categories = parse_index(_index_html())

    assert [c.id for c in categories] == ["TDM", "COPA_F"]
    assert [g.id for g in categories[0].groups] == ["TDM_G1", "TDM_G2"]
    assert [g.id for g in categories[1].groups] == ["COPA_F_1A", "COPA_F_2A"]


def test_parse_index_excludes_state_leagues():
    """Los grupos catalanes de la liga estatal ya están en rfetm_*."""
    categories = parse_index(_index_html())

    all_groups = [g.id for c in categories for g in c.groups]
    assert "SUM" not in all_groups
    assert not any("SDM" in g for g in all_groups)


def test_parse_index_makes_relative_urls_absolute():
    tdm = parse_index(_index_html())[0]

    assert tdm.groups[0].source_url == "https://fctt.cat/lligues/grup-1/"
    assert tdm.groups[1].source_url == "https://fctt.cat/lligues/grup-2/"


# ── Metadatos de grupo ────────────────────────────────────────────────────────


def test_parse_season_label():
    assert parse_season_label(_jornada_html()) == "2025/2026"


def test_parse_jornada_numbers():
    assert parse_jornada_numbers(_jornada_html()) == [1, 2, 3]


def test_parse_standings():
    teams = parse_standings(_jornada_html(), "COPA_F_1A")

    assert len(teams) == 3
    first = teams[0]
    assert first.name == "CTT VILASSAR"
    assert first.id == "COPA_F_1A_ctt_vilassar"
    assert (first.position, first.matches_played, first.matches_won) == (1, 12, 12)
    assert (first.matches_lost, first.points) == (0, 24)
    # PF/PC (partidos individuales a favor/en contra) para desempate.
    assert (first.games_for, first.games_against) == (55, 5)


# ── Jornada ───────────────────────────────────────────────────────────────────


def test_parse_jornada_reads_every_encounter():
    matches = parse_jornada(_jornada_html(), 2, "COPA_F_1A")

    assert len(matches) == 3
    assert matches[0].date is not None
    assert matches[0].date.strftime("%d/%m/%Y %H:%M") == "22/11/2025 10:00"


def test_walkover_has_result_but_no_acta():
    """Las incomparecencias se publican como 5-0 sin tabla de partidos."""
    walkover = parse_jornada(_jornada_html(), 2, "COPA_F_1A")[1]

    assert (walkover.team_local_name, walkover.team_visitor_name) == (
        "CTT VILASSAR",
        "C.T.T.VILANOVA",
    )
    assert (walkover.score_local, walkover.score_visitor) == (5, 0)
    assert walkover.individual_matches == []
    assert walkover.status == "completed"


def test_team_name_with_year_is_not_split_by_the_score_regex():
    """"C.T.T. ATENEU 1882" no debe partirse por el 1882."""
    match = parse_jornada(_jornada_html(), 2, "COPA_F_1A")[0]

    assert match.team_visitor_name == "C.T.T. ATENEU 1882"
    assert (match.score_local, match.score_visitor) == (3, 2)


def test_doubles_carries_both_players_and_codes():
    match = parse_jornada(_jornada_html(), 2, "COPA_F_1A")[0]
    doubles = [m for m in match.individual_matches if m.is_doubles]

    assert len(doubles) == 1
    double = doubles[0]
    assert double.player_local_name == "CUMPLIDO CHACON, GUADALUPE"
    assert double.player_local_name_2 == "CALAVIA DE ORBE, NURIA"
    assert double.player_local_license == "101"
    assert double.player_local_license_2 == "102"
    assert double.player_visitor_name == "OSUNA CRUZ, LOLA"
    assert double.player_visitor_name_2 == "CARREÑO PASTOR, ARIADNA"
    assert double.player_visitor_license == "201"
    assert double.player_visitor_license_2 == "202"


def test_swapped_acta_is_flipped_to_match_the_header():
    """El acta a veces lista los equipos al revés que la cabecera.

    Si no se voltea, el resultado del encuentro sale invertido.
    """
    match = parse_jornada(_jornada_html(), 2, "COPA_F_1A")[2]

    assert (match.team_local_name, match.team_visitor_name) == (
        "C.T.T.VILANOVA",
        "C.T.T. ATENEU 1882",
    )
    assert (match.score_local, match.score_visitor) == (0, 2)
    # Los partidos, ya reexpresados en términos del local del encuentro
    assert all(m.winner == "visitor" for m in match.individual_matches)
    assert match.individual_matches[0].player_local_name == "SOLÉ MARTÍ, ROSA"
    assert match.individual_matches[0].player_visitor_name == "OSUNA CRUZ, LOLA"
    assert [(s.local, s.visitor) for s in match.individual_matches[0].sets] == [
        (4, 11),
        (6, 11),
        (8, 11),
    ]


def test_score_always_matches_the_individual_results():
    """Invariante: el marcador del encuentro = partidos ganados por cada lado.

    Es lo que destapa una acta girada o unos lados cruzados.
    """
    for match in parse_jornada(_jornada_html(), 2, "COPA_F_1A"):
        if not match.individual_matches:
            continue  # incomparecencia: resultado sin partidos
        won_local = sum(1 for m in match.individual_matches if m.winner == "local")
        won_visitor = sum(1 for m in match.individual_matches if m.winner == "visitor")
        assert (won_local, won_visitor) == (match.score_local, match.score_visitor), (
            f"{match.team_local_name} vs {match.team_visitor_name}"
        )


# ── Terreny de joc / àrbitre (div.match-info) ─────────────────────────────────


def _container(field: str, referee_div: str = "") -> str:
    """Un `div.match-container` de calendario con su bloque `match-info`."""
    return (
        "<h2>Jornada 1</h2>"
        '<div class="match-container">'
        '  <div class="match-datetime">04/10/2026 17:00</div>'
        '  <div class="team-home"><a href="?team_name_id=10">LOCAL CTT</a></div>'
        '  <div class="team-away"><a href="?team_name_id=20">VISITANT CTT</a></div>'
        '  <div class="match-score">4 - 2</div>'
        f'  <div class="match-info">{field}{referee_div}'
        '    <div class="acta-info"><a href="x/imprimir/acta">Acta</a></div>'
        "  </div>"
        "</div>"
    )


def test_calendar_extracts_venue_and_referee():
    field = '<div class="field-info"><strong>Terreny de joc:</strong> PAV. X. C/ Y, 1, 08001 BCN</div>'
    ref = '<div class="referee-info"><strong>Àrbitre:</strong> JOAN ARBITRE</div>'
    match = parse_calendar_fixtures(_container(field, ref), "grup-1")[0]
    assert match.venue == "PAV. X. C/ Y, 1, 08001 BCN"
    assert match.referee == "JOAN ARBITRE"


def test_calendar_venue_without_referee():
    """Divisiones inferiores / provinciales: hay terreno pero no árbitro."""
    field = '<div class="field-info"><strong>Terreny de joc:</strong> GIMNÀS Z, 08339 VILASSAR</div>'
    match = parse_calendar_fixtures(_container(field), "1a-divisio")[0]
    assert match.venue == "GIMNÀS Z, 08339 VILASSAR"
    assert match.referee == ""


def test_calendar_without_match_info_leaves_venue_empty():
    html = (
        "<h2>Jornada 1</h2>"
        '<div class="match-container">'
        '  <div class="team-home"><a href="?team_name_id=10">LOCAL</a></div>'
        '  <div class="team-away"><a href="?team_name_id=20">VISITANT</a></div>'
        '  <div class="match-score">- - -</div>'
        "</div>"
    )
    match = parse_calendar_fixtures(html, "x")[0]
    assert match.venue == ""
    assert match.referee == ""
