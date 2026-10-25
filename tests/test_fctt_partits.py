from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.player_match import MatchLog, PlayerMatch, build_record
from parsers.fctt_partits_parser import (
    PartitsRef,
    RawRow,
    _row_from,
    classify_event,
    parse_index,
    resolve_opponent_codes,
)
from scrapers.fctt_partits_scraper import build_match_logs, build_records

FIXTURES = Path(__file__).parent / "fixtures"

REF = PartitsRef(number="10", label="Juliol 2025/2026", url="https://fctt.cat/x.pdf")


def _row(licence, name, opponent, sets=3, opp_sets=1, rk=1700.0, opp_rk=1500.0,
         date="2026-06-02", event="Lliga RTB J4", d_rk=2.4) -> RawRow:
    return RawRow(
        date=date, event=event, category='Veterans 3a "A"',
        licence=licence, name=name, opponent_name=opponent,
        sets=sets, opp_sets=opp_sets, rk=rk, opp_rk=opp_rk,
        d_rk=d_rk, opp_d_rk=-d_rk, rd=57.3, opp_rd=59.2,
    )


def _mirror(row: RawRow, licence: str) -> RawRow:
    """La misma fila vista desde el otro jugador, como la publica el PDF."""
    return RawRow(
        date=row.date, event=row.event, category=row.category,
        licence=licence, name=row.opponent_name, opponent_name=row.name,
        sets=row.opp_sets, opp_sets=row.sets, rk=row.opp_rk, opp_rk=row.rk,
        d_rk=row.opp_d_rk, opp_d_rk=row.d_rk, rd=row.opp_rd, opp_rd=row.rd,
    )


# ── Índice ────────────────────────────────────────────────────────────────────


def test_parse_index_finds_one_pdf_per_edition():
    html = (FIXTURES / "fctt_ranquings_index.html").read_text(encoding="utf-8")

    refs = {r.number: r for r in parse_index(html)}

    assert set(refs) == {"10", "06"}
    assert refs["10"].label == "Juliol 2025/2026"
    assert refs["10"].url.endswith("RK2526_10_Partits_Jugats.pdf")


def test_parse_index_accepts_the_wordpress_duplicate():
    """En la edición 06 el único fichero publicado es el `-1`."""
    html = (FIXTURES / "fctt_ranquings_index.html").read_text(encoding="utf-8")

    refs = {r.number: r for r in parse_index(html)}

    assert refs["06"].url.endswith("RK2526_06_Partits_Jugats-1.pdf")
    assert refs["06"].url.startswith("https://fctt.cat/")


# ── Emparejado del espejo ─────────────────────────────────────────────────────


def test_the_mirror_row_supplies_the_opponent_licence():
    """El PDF solo trae la licencia del jugador 1; la del rival es su espejo."""
    a = _row("68", "MARTINEZ Felipe", "GIMENEZ Antonio")
    rows = [a, _mirror(a, "3312")]

    assert resolve_opponent_codes(rows) == ["3312", "68"]


def test_an_opponent_without_a_mirror_has_no_code():
    """Partido estatal: el rival es de otra federación y no tiene ficha FCTT."""
    rows = [_row("149", "NOLIS Josep María", "ADAN Miguel Angel", event="ESTATAL")]

    assert resolve_opponent_codes(rows) == [None]


def test_a_mirror_that_does_not_match_the_result_is_rejected():
    """Sin validar el marcador, dos partidos distintos podrían emparejarse."""
    a = _row("68", "MARTINEZ Felipe", "GIMENEZ Antonio", sets=3, opp_sets=0)
    # Mismos jugadores, mismo día, mismo evento… pero ganan los dos: no puede
    # ser el mismo partido.
    falso = _row("3312", "GIMENEZ Antonio", "MARTINEZ Felipe", sets=3, opp_sets=0,
                 rk=1500.0, opp_rk=1700.0)

    assert resolve_opponent_codes([a, falso]) == [None, None]


def test_homonyms_are_told_apart_by_the_ratings():
    """`GIMENEZ Angel` son dos jugadores: licencias 947 y 8328.

    Casar por nombre le atribuiría a uno los partidos del otro, en silencio. El
    rating cruzado es lo que los distingue.
    """
    a = _row("68", "MARTINEZ Felipe", "GIMENEZ Angel", opp_rk=1500.0)
    otro = _row("70", "PUIG Jaume", "GIMENEZ Angel", opp_rk=1200.0)
    rows = [a, otro, _mirror(a, "947"), _mirror(otro, "8328")]

    assert resolve_opponent_codes(rows) == ["947", "8328", "68", "70"]


def test_two_matches_between_the_same_pair_on_the_same_day():
    """Grupo y eliminatoria en el mismo torneo: cuatro filas, no dos."""
    g = _row("68", "MARTINEZ Felipe", "GIMENEZ Antonio", event="Top Català",
             sets=3, opp_sets=0, rk=1700.0)
    ko = _row("68", "MARTINEZ Felipe", "GIMENEZ Antonio", event="Top Català",
              sets=1, opp_sets=3, rk=1702.4)
    rows = [g, ko, _mirror(g, "3312"), _mirror(ko, "3312")]

    assert resolve_opponent_codes(rows) == ["3312", "3312", "68", "68"]


# ── Marcador al revés ─────────────────────────────────────────────────────────


def _cells(sets: str, opp_sets: str, d_rk: str) -> list[str]:
    return ["17/05/2026", "Piramidal de Veterans RTL", "VET", "42",
            "FONTANET BERGA, FRANCESC", "ZHYLYAKOV, SERGII",
            sets, opp_sets, "1500,0", "1400,0", d_rk, "-2,0", "50,0", "50,0"]


def test_a_score_that_contradicts_the_rating_change_is_flipped():
    """La fuente escribe algunas filas desde el otro jugador sin girar el
    marcador: dice que ganó quien perdió. El ΔRK sí es el del jugador."""
    row, girada = _row_from(_cells("2", "0", "-14,4"))

    assert girada is True
    assert (row.sets, row.opp_sets) == (0, 2)   # perdió, como dice el ΔRK
    assert row.d_rk == -14.4                     # el ΔRK no se toca


def test_a_coherent_score_is_left_alone():
    row, girada = _row_from(_cells("3", "0", "37,2"))

    assert girada is False
    assert (row.sets, row.opp_sets) == (3, 0)


def test_a_match_worth_zero_points_is_not_second_guessed():
    """Sin ΔRK no hay forma de saber la orientación: se respeta la fuente."""
    row, girada = _row_from(_cells("0", "3", "0,0"))

    assert girada is False
    assert (row.sets, row.opp_sets) == (0, 3)


# ── Documentos ────────────────────────────────────────────────────────────────


def test_every_row_becomes_a_match_for_its_own_player():
    """Las dos caras del partido se guardan: una en el log de cada jugador."""
    a = _row("68", "MARTINEZ Felipe", "GIMENEZ Antonio")
    logs = {log.player_code: log for log in build_match_logs([a, _mirror(a, "3312")], REF)}

    assert set(logs) == {"68", "3312"}
    assert logs["68"].matches[0].won is True
    assert logs["3312"].matches[0].won is False
    assert logs["3312"].matches[0].opponent_code == "68"


def test_an_external_opponent_is_flagged_instead_of_faking_a_code():
    rows = [_row("149", "NOLIS Josep María", "ADAN Miguel Angel", event="ESTATAL")]
    (log,) = build_match_logs(rows, REF)

    opponent = log.to_dict()["matches"][0]["opponent"]
    assert opponent == {"name": "ADAN Miguel Angel", "external": True}


def test_the_log_totals_match_the_matches():
    a = _row("68", "A", "B", sets=3, opp_sets=1, d_rk=2.4)
    b = _row("68", "A", "C", sets=0, opp_sets=3, d_rk=-7.0, date="2026-06-05")
    (log,) = build_match_logs([a, b], REF)

    doc = log.to_dict()
    assert (doc["played"], doc["won"], doc["lost"]) == (2, 1, 1)
    assert doc["d_rk_total"] == -4.6
    assert (doc["from"], doc["to"]) == ("2026-06-02", "2026-06-05")


def test_matches_are_stored_in_chronological_order():
    tarde = _row("68", "A", "B", date="2026-06-09")
    pronto = _row("68", "A", "C", date="2026-06-01")
    (log,) = build_match_logs([tarde, pronto], REF)

    assert [m["date"] for m in log.to_dict()["matches"]] == [
        "2026-06-01", "2026-06-09",
    ]


# ── Resumen de temporada ──────────────────────────────────────────────────────


def _match(**kw) -> PlayerMatch:
    base = dict(
        date="2026-06-02", event="Top Català", event_type="torneig",
        category="V60M", opponent_name="RIVAL", opponent_code="99",
        sets=3, opp_sets=0, rk=1700.0, opp_rk=1500.0, d_rk=2.0, rd=50.0,
    )
    return PlayerMatch(**{**base, **kw})


def test_record_counts_the_whole_season():
    logs = [
        MatchLog("68", "06", matches=[_match(date="2026-01-10")]),
        MatchLog("68", "10", matches=[_match(date="2026-06-02", sets=1, opp_sets=3)]),
    ]

    record = build_record(logs)

    assert (record["played"], record["won"], record["lost"]) == (2, 1, 1)
    assert record["last_played"] == "2026-06-02"


def test_best_win_is_the_highest_rated_opponent_beaten():
    logs = [
        MatchLog("68", "10", matches=[
            _match(opponent_name="FLOJO", opp_rk=1200.0),
            _match(opponent_name="FUERTE", opp_rk=2100.0),
            # Perdida contra uno todavía mejor: no cuenta como mejor victoria
            _match(opponent_name="CAMPEÓN", opp_rk=2400.0, sets=0, opp_sets=3),
        ])
    ]

    assert build_record(logs)["best_win"]["name"] == "FUERTE"


def test_record_counts_distinct_tournaments_not_league_days():
    logs = [
        MatchLog("68", "10", matches=[
            _match(event="Lliga RTB J4", event_type="lliga"),
            _match(event="Top Català"),
            _match(event="Top Català"),
            _match(event="TOP OPEN BCN"),
        ])
    ]

    assert build_record(logs)["tournaments"] == 2


def test_records_are_computed_from_every_edition_but_written_selectively():
    """Solo se reescribe a quien ha jugado en la edición nueva…"""
    logs = [
        MatchLog("68", "06", matches=[_match(date="2026-01-10")]),
        MatchLog("68", "10", matches=[_match(date="2026-06-02")]),
        MatchLog("70", "06", matches=[_match(date="2026-01-10")]),
    ]

    records = build_records(logs, only={"68"})

    assert set(records) == {"68"}
    # …pero con su temporada entera, no solo la edición que cambió.
    assert records["68"]["played"] == 2


# ── Clasificación de eventos ──────────────────────────────────────────────────


def test_leagues_and_tournaments_are_told_apart():
    """Nombres reales del vocabulario de la temporada (64 eventos distintos)."""
    for evento in ("Lliga RTB J4", "Lliga RTB Play-Off", "LLIGA J1", "LLIGA FA",
                   "LLNN J16", "LLNN FA", "ESTATAL"):
        assert classify_event(evento) == "lliga", evento

    for evento in ("Top Català", "TOP OPEN BCN", "Open Kids-Girona", "CE ABS",
                   "COPAS", "CC Màsters Ind&Db", "Circuit Promeses ZonaTT Top 8",
                   'CAMPIONAT DE CATALUNYA PER COMARQUES "B"', "Trials FCTT"):
        assert classify_event(evento) == "torneig", evento
