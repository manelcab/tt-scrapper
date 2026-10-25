from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.rfetm_ranking import RfetmEdition, RfetmEntry
from parsers.rfetm_ranking_parser import (
    RfetmFormatError,
    parse_index,
    parse_ranking,
)
from scrapers.rfetm_rankings_scraper import (
    build_category_documents,
    build_player_documents,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _html(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


def _entries() -> list[RfetmEntry]:
    return parse_ranking(_html("rfetm_ranking.html"), "M")


# ── Índice ────────────────────────────────────────────────────────────────────


def test_index_keeps_only_the_requested_season():
    """El índice mezcla las temporadas publicadas."""
    refs = parse_index(_html("rfetm_ranking_index.html"), temporada=11)

    assert [r.id for r in refs] == ["2025-09", "2026-05", "2026-06"]


def test_index_is_sorted_oldest_first():
    """`current` se construye sobreescribiendo, así que el orden importa."""
    refs = parse_index(_html("rfetm_ranking_index.html"), temporada=11)

    assert refs[0].id == "2025-09" and refs[-1].id == "2026-06"


def test_index_deduplicates_the_two_sexes():
    """Cada mes se anuncia dos veces, una por sexo, y es una sola edición."""
    refs = parse_index(_html("rfetm_ranking_index.html"), temporada=11)

    assert len(refs) == len({r.id for r in refs})


def test_index_reads_the_initial_ranking_of_a_season():
    """`mes=0` es el ranking inicial; la temporada 10 sí lo tiene."""
    refs = parse_index(_html("rfetm_ranking_index.html"), temporada=10)

    assert "2024-00" in [r.id for r in refs]


# ── Tabla ─────────────────────────────────────────────────────────────────────


def test_parse_reads_identity_and_points():
    primero = _entries()[0]

    assert primero.lic == "17121"
    assert primero.name == "FERREIRA Diogo Miguel"
    assert primero.fa == "AND"          # la fuente lo escribe `-AND-`
    assert primero.birth_year == 1992
    assert (primero.pts_prev, primero.pts) == (2792.1, 2795.3)
    assert (primero.pj, primero.pg, primero.pp) == (2, 2, 0)


def test_not_ranked_is_not_a_position():
    """`99999` significa "no compite en esa categoría"."""
    primero = _entries()[0]

    assert primero.positions == {"SEN": 1}
    assert 99999 not in primero.positions.values()


def test_a_player_can_rank_in_several_categories():
    lewandowski = _entries()[1]

    assert lewandowski.positions == {"SEN": 3, "V40": 1}


def test_national_ranking_is_not_the_general_one():
    """El nº1 global no está en NAC —es extranjero— y el nº1 de NAC es otro."""
    ferreira, _, lillo = _entries()

    assert "NAC" not in ferreira.positions      # 2795,3 puntos y sin ranking NAC
    assert lillo.positions["NAC"] == 1          # 2619,2 puntos y nº1 nacional
    assert lillo.pts < ferreira.pts


def test_points_by_competition_only_keep_what_scored():
    primero = _entries()[0]

    assert primero.by_event == {"llnn": 3.2}


def test_a_table_with_another_shape_is_rejected():
    """Si la fuente cambia de formato, mejor abortar que descolocar los campos.

    La cabecera viene rota —un solo `<th>` con todos los nombres pegados—, así
    que no hay forma de detectarlo por nombre de columna.
    """
    roto = _html("rfetm_ranking.html").replace("<td>2792.1</td>", "")

    with pytest.raises(RfetmFormatError):
        parse_ranking(roto, "M")


# ── Documentos ────────────────────────────────────────────────────────────────


def _dos_ediciones() -> list[RfetmEdition]:
    mayo = RfetmEdition(id="2026-05", entries=[
        RfetmEntry(lic="17948", name="LILLO Alberto", club="ALICANTE TM", sex="M",
                   pts=2606.8, positions={"NAC": 2, "SEN": 3}),
    ])
    junio = RfetmEdition(id="2026-06", entries=[
        RfetmEntry(lic="17948", name="LILLO Alberto", club="OLOT", sex="M",
                   pts=2619.2, pj=5, pg=4, pp=1, positions={"NAC": 1, "SEN": 2}),
    ])
    return [mayo, junio]


def test_history_keeps_every_edition():
    docs = build_player_documents(_dos_ediciones())

    assert set(docs["17948"]["history"]) == {"2026-05", "2026-06"}
    assert docs["17948"]["history"]["2026-05"]["pos"] == {"NAC": 2, "SEN": 3}


def test_current_reflects_the_latest_edition():
    docs = build_player_documents(_dos_ediciones())

    assert docs["17948"]["current"]["NAC_M"] == {
        "pos": 1, "pts": 2619.2, "edition": "2026-06",
    }
    assert docs["17948"]["club"] == "OLOT"      # cambió de club en junio


def test_the_sex_is_part_of_the_key():
    """`NAC` es la misma categoría para ellos y para ellas.

    Sin el sexo en la clave, `orderBy('current.NAC.pos')` mezcla las dos listas
    y devuelve dos primeros, uno de cada.
    """
    edition = RfetmEdition(id="2026-06", entries=[
        RfetmEntry(lic="m", name="LILLO", sex="M", pts=2619.2, positions={"NAC": 1}),
        RfetmEntry(lic="f", name="XIAO", sex="F", pts=2500.0, positions={"NAC": 1}),
    ])

    docs = build_player_documents([edition])

    assert set(docs["m"]["current"]) == {"NAC_M"}
    assert set(docs["f"]["current"]) == {"NAC_F"}


def test_a_player_missing_from_the_last_edition_has_no_current():
    """Si no está en el ranking del mes, no tiene posición vigente.

    Arrastrar la última posición conocida haría que un jugador retirado
    apareciese como nº1 meses después de haber desaparecido.
    """
    ediciones = _dos_ediciones()
    ediciones[1].entries = []          # en junio ya no aparece

    docs = build_player_documents(ediciones)

    assert docs["17948"]["current"] == {}
    assert "2026-05" in docs["17948"]["history"]   # pero su histórico sigue


def test_leaving_a_category_removes_it_from_current():
    ediciones = _dos_ediciones()
    ediciones[1].entries[0].positions = {"NAC": 1}

    docs = build_player_documents(ediciones)

    assert set(docs["17948"]["current"]) == {"NAC_M"}


def test_category_documents_are_derived_not_downloaded():
    """Los rànquings por categoría salen de la tabla global, ordenando."""
    edition = RfetmEdition(id="2026-06", entries=[
        RfetmEntry(lic="a", name="A", sex="M", pts=2600.0, positions={"SEN": 2}),
        RfetmEntry(lic="b", name="B", sex="M", pts=2700.0, positions={"SEN": 1, "V40": 1}),
        RfetmEntry(lic="c", name="C", sex="F", pts=2400.0, positions={"SEN": 1}),
    ])

    docs = dict(build_category_documents(edition))

    assert set(docs) == {"SEN_M", "V40_M", "SEN_F"}
    assert [e["lic"] for e in docs["SEN_M"]["top"]] == ["b", "a"]
    assert docs["SEN_F"]["player_count"] == 1
