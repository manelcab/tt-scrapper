from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers.rfetm_html_parser import parse_matches


def _match_table(place_row: str) -> str:
    """Tabla de encuentro RFETM (table-sm + borde #F1F1F0) con fila Lugar/Árbitro.

    tr0: fecha · equipo local · marcador · equipo visitante.
    tr1: un td con la línea Lugar/Árbitro y la tabla anidada de individuales.
    """
    return (
        "<table class='table table-sm' style='border: #F1F1F0 1px solid;'>"
        "<tr>"
        "<td colspan='2'>02/10/2026 17:30</td>"
        "<td><a href='?equipo=1'>LOCAL TM</a></td>"
        "<td>4</td><td>2</td>"
        "<td><a href='?equipo=2'>VISITANTE TM</a></td>"
        "</tr>"
        "<tr><td colspan='7'>"
        "<table class='table-borderer'><tr><td>A</td><td>Lic: 1 JUGADOR</td>"
        "<td>X</td><td>Lic: 2 RIVAL</td></tr></table>"
        f"{place_row}"
        "</td></tr>"
        "</table>"
    )


def test_parse_matches_extracts_lugar_and_arbitro():
    place = (
        "<tr><td><font><b>Lugar: </b>SALA MUNICIPAL - Alacant (Alicante)"
        "<br><b>&Aacute;rbitro: </b>RAQUEL MATEU</font></td></tr>"
    )
    match = parse_matches(_match_table(place), 1)[0]
    assert match.venue == "SALA MUNICIPAL - Alacant (Alicante)"
    assert match.referee == "RAQUEL MATEU"


def test_parse_matches_lugar_without_arbitro():
    place = "<tr><td><font><b>Lugar: </b>POLIDEPORTIVO SUR</font></td></tr>"
    match = parse_matches(_match_table(place), 1)[0]
    assert match.venue == "POLIDEPORTIVO SUR"
    assert match.referee == ""


def test_parse_matches_without_place_row():
    match = parse_matches(_match_table(""), 1)[0]
    assert match.venue == ""
    assert match.referee == ""


# ── Clasificación (PF/PC de desempate) ────────────────────────────────────────

from parsers.rfetm_html_parser import parse_standings  # noqa: E402


def test_parse_standings_reads_games_for_against():
    """pos · equipo · EJ · EG · EE · EP · PF · PC · PTOS (layout de 9 columnas)."""
    html = (
        "<table class='table table-striped table-hover'>"
        "<tr><td>1</td><td><a href='?eqdat=9'>EQUIPO UNO</a></td>"
        "<td>5</td><td>5</td><td>0</td><td>0</td><td>18</td><td>7</td><td>10</td></tr>"
        "<tr><td>2</td><td><a href='?eqdat=8'>EQUIPO DOS</a></td>"
        "<td>5</td><td>4</td><td>0</td><td>1</td><td>16</td><td>9</td><td>8</td></tr>"
        "</table>"
    )
    teams = parse_standings(html)
    assert len(teams) == 2
    assert (teams[0].games_for, teams[0].games_against) == (18, 7)
    assert (teams[0].points, teams[0].matches_won) == (10, 5)
    assert (teams[1].games_for, teams[1].games_against) == (16, 9)


def test_parse_standings_eight_columns_no_draws():
    """2ª/3ª/4ª omiten la columna EE (empates): pos·equipo·EJ·EG·EP·PF·PC·PTOS.

    Con cabecera, el parser mapea por nombre y NO deja PJ a 0 ni desplaza PG/PE
    (el bug que detectó Kanto: todos los equipos con 0 jugados).
    """
    html = (
        "<table class='table table-striped table-hover'>"
        "<tr><th></th><th>EQUIPO</th><th>EJ</th><th>EG</th><th>EP</th>"
        "<th>PF</th><th>PC</th><th>PTOS.</th></tr>"
        "<tr><td>1</td><td><a href='?eqdat=9'>EQUIPO UNO</a></td>"
        "<td>3</td><td>3</td><td>0</td><td>16</td><td>3</td><td>6</td></tr>"
        "<tr><td>2</td><td><a href='?eqdat=8'>EQUIPO DOS</a></td>"
        "<td>3</td><td>2</td><td>1</td><td>12</td><td>8</td><td>5</td></tr>"
        "</table>"
    )
    teams = parse_standings(html)
    assert len(teams) == 2
    t0 = teams[0]
    assert t0.matches_played == 3          # PJ ya no es 0
    assert (t0.matches_won, t0.matches_drawn, t0.matches_lost) == (3, 0, 0)
    assert (t0.games_for, t0.games_against, t0.points) == (16, 3, 6)
    t1 = teams[1]
    assert (t1.matches_played, t1.matches_won, t1.matches_lost) == (3, 2, 1)
    assert (t1.games_for, t1.games_against, t1.points) == (12, 8, 5)
