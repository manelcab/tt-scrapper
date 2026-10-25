from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.ranking import RankingCategory, RankingEdition, RankingEntry
from parsers.fctt_ranking_pdf_parser import (
    _float,
    normalize_category,
    parse_index,
    parse_ranking,
)
from scrapers.fctt_rankings_scraper import (
    build_category_documents,
    build_player_documents,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _index_html() -> str:
    return (FIXTURES / "fctt_ranquings_index.html").read_text(encoding="utf-8")


def _entry(code: str, pos: int, rk: float, **kw) -> RankingEntry:
    return RankingEntry(
        player_code=code, name=f"JUGADOR {code}", position=pos, rk=rk, **kw
    )


# ── Índice ────────────────────────────────────────────────────────────────────


def test_parse_index_groups_pdfs_by_edition():
    editions = {e.number: e for e in parse_index(_index_html())}

    assert set(editions) == {"10", "06", "ini"}
    assert editions["10"].label == "Juliol 2025/2026"
    assert set(editions["10"].pdfs) == {"SEM", "SEF", "ADA", "V40M", "S21F"}


def test_categories_starting_with_a_digit_are_renamed():
    """Los ids acaban siendo claves de mapa en Firestore.

    Una clave que empieza por número obliga a escapar la ruta
    (`current.\\`40M\\`.pos`) y, si no se escapa, la consulta devuelve cero
    resultados **sin error**. Se renombran en origen para que no pase.
    """
    assert normalize_category("40M") == "V40M"
    assert normalize_category("65M") == "V65M"
    assert normalize_category("21F") == "S21F"
    # Las que ya empiezan por letra no se tocan
    assert normalize_category("SEM") == "SEM"
    assert normalize_category("ADA") == "ADA"
    assert normalize_category("BEF") == "BEF"


def test_renamed_categories_still_yield_the_right_gender():
    from scrapers.fctt_rankings_scraper import _gender_from_category

    assert _gender_from_category("V40M") == "M"
    assert _gender_from_category("S21F") == "F"


def test_parse_index_skips_partits_jugats():
    """No es un ranking: es la relación de partidos por deportista."""
    editions = {e.number: e for e in parse_index(_index_html())}

    assert not any("PARTITS" in c.upper() for c in editions["10"].pdfs)


def test_parse_index_deduplicates_wordpress_copies():
    """`RK2526_06_SEM-1.pdf` es la misma revisión resubida."""
    editions = {e.number: e for e in parse_index(_index_html())}

    assert set(editions["06"].pdfs) == {"SEM", "SEF"}
    assert editions["06"].pdfs["SEM"].endswith("RK2526_06_SEM.pdf")


def test_parse_index_reads_the_initial_ranking():
    editions = {e.number: e for e in parse_index(_index_html())}

    assert set(editions["ini"].pdfs) == {"GEN"}


def test_parse_index_makes_relative_urls_absolute():
    editions = {e.number: e for e in parse_index(_index_html())}

    assert editions["06"].pdfs["SEF"].startswith("https://fctt.cat/")


# ── Números ───────────────────────────────────────────────────────────────────


def test_float_reads_spanish_decimal_comma():
    assert _float("2920,8") == 2920.8
    assert _float("-19,2") == -19.2
    assert _float("") == 0.0


# ── Acumulación por jugador ───────────────────────────────────────────────────


def _two_editions() -> list[RankingEdition]:
    marzo = RankingEdition(
        number="06",
        categories=[
            RankingCategory(
                id="SEM",
                entries=[_entry("5747", pos=300, rk=1800.0, club="REUS", edat_fctt="SEN")],
            )
        ],
    )
    julio = RankingEdition(
        number="10",
        categories=[
            RankingCategory(
                id="SEM",
                entries=[_entry("5747", pos=245, rk=1850.5, club="TORTOSA", edat_fctt="SEN")],
            ),
            RankingCategory(
                id="40M",
                entries=[_entry("5747", pos=12, rk=1850.5, club="TORTOSA")],
            ),
        ],
    )
    return [marzo, julio]


def test_player_history_keeps_every_edition():
    docs = build_player_documents(_two_editions())

    historia = docs["5747"]["history"]["SEM"]
    assert set(historia) == {"06", "10"}
    assert historia["06"]["pos"] == 300
    assert historia["10"]["rk"] == 1850.5


def test_player_appears_in_every_category_he_ranks_in():
    docs = build_player_documents(_two_editions())

    assert set(docs["5747"]["history"]) == {"SEM", "40M"}
    assert docs["5747"]["current"]["40M"]["pos"] == 12


def test_current_reflects_the_latest_edition():
    """Las ediciones llegan de la más antigua a la más reciente."""
    docs = build_player_documents(_two_editions())

    assert docs["5747"]["current"]["SEM"] == {
        "pos": 245,
        "rk": 1850.5,
        "edition": "10",
    }
    # Y los datos de nivel jugador también son los del último (cambió de club)
    assert docs["5747"]["club"] == "TORTOSA"


def test_gender_is_derived_from_the_category():
    edition = RankingEdition(
        number="10",
        categories=[
            RankingCategory(id="SEF", entries=[_entry("1", 1, 2000.0)]),
            RankingCategory(id="SEM", entries=[_entry("2", 1, 2000.0)]),
            RankingCategory(id="ADA", entries=[_entry("3", 1, 2000.0)]),
        ],
    )
    docs = build_player_documents([edition])

    assert docs["1"]["gender"] == "F"
    assert docs["2"]["gender"] == "M"
    assert "gender" not in docs["3"]  # adaptado es mixto: no se inventa


def test_history_entry_drops_redundant_columns():
    """`RK anterior` no se guarda: es `rk - d_rk` y ya está el histórico."""
    docs = build_player_documents(_two_editions())

    campos = set(docs["5747"]["history"]["SEM"]["10"])
    assert campos == {
        "pos", "rk", "d_rk", "rd", "pj", "pg", "pp", "p_any", "classified",
    }


# ── Documentos de categoría ───────────────────────────────────────────────────


def test_category_document_keeps_an_ordered_top():
    edition = RankingEdition(
        number="10",
        categories=[
            RankingCategory(
                id="SEM",
                title="RÀNQUING SÈNIOR MASCULÍ",
                entries=[
                    _entry("c", pos=3, rk=1000.0),
                    _entry("a", pos=1, rk=1200.0),
                    _entry("b", pos=2, rk=1100.0),
                ],
            )
        ],
    )
    (category_id, doc), = build_category_documents(edition)

    assert category_id == "SEM"
    assert doc["player_count"] == 3
    assert [e["pos"] for e in doc["top"]] == [1, 2, 3]
    assert doc["top"][0]["code"] == "a"


# ── PDF real (opcional) ───────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (FIXTURES / "fctt_ranking_sample.pdf").exists(),
    reason=(
        "Falta tests/fixtures/fctt_ranking_sample.pdf. No se versiona porque los "
        "rànquings llevan nombres de jugadores, muchos menores de edad. Para "
        "activarlo, copia ahí un PDF de https://fctt.cat/ranquings/"
    ),
)
def test_parse_ranking_reads_the_seventeen_columns():
    entries = parse_ranking(str(FIXTURES / "fctt_ranking_sample.pdf"))

    assert entries
    first = entries[0]
    assert first.position == 1
    assert first.player_code.isdigit()
    assert first.rk > 0
    assert first.pj == first.pg + first.pp
