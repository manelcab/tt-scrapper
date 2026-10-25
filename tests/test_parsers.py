from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers.html_parser import HtmlParser
from utils.hashing import md5_bytes, md5_str

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------ HtmlParser


def test_parse_index_from_fixture():
    html = (FIXTURES / "sample_index.html").read_text()
    categories = HtmlParser.parse_index(html, "https://www.rtbtt.com")

    assert len(categories) == 3

    pref = next((c for c in categories if c.id == "PREF"), None)
    assert pref is not None
    assert len(pref.groups) == 2
    assert pref.groups[0].id == "PREF_G1"
    assert "PREF_G1.html" in pref.groups[0].source_url

    primera = next((c for c in categories if c.id == "1A"), None)
    assert primera is not None
    assert len(primera.groups) == 3
    assert primera.level == 2


def test_parse_index_absolute_urls():
    html = (FIXTURES / "sample_index.html").read_text()
    categories = HtmlParser.parse_index(html, "https://www.rtbtt.com")
    for cat in categories:
        for grp in cat.groups:
            assert grp.source_url.startswith("https://www.rtbtt.com"), (
                f"URL should be absolute: {grp.source_url}"
            )


def test_parse_group_page_matchdays():
    html = (FIXTURES / "sample_group.html").read_text()
    matchdays_info, teams = HtmlParser.parse_group_page(
        html, "https://www.rtbtt.com", "PREF_G1"
    )

    assert len(matchdays_info) == 3
    for info in matchdays_info:
        assert info["acta_url"].endswith(".pdf")
        assert info["number"] >= 1


def test_parse_group_page_teams():
    html = (FIXTURES / "sample_group.html").read_text()
    _, teams = HtmlParser.parse_group_page(html, "https://www.rtbtt.com", "PREF_G1")

    assert len(teams) == 3
    assert teams[0].position == 1
    assert "Barcelona" in teams[0].name
    assert teams[0].matches_played == 2
    assert teams[0].points == 4


def test_parse_index_javascript_loadurl():
    """URLs wrapped in javascript:loadurl() must be extracted correctly."""
    html = """<table>
      <tr><th>Categoria</th><th>G1</th></tr>
      <tr><td>Preferent</td>
          <td><a href="javascript:loadurl('https://www.rtbtt.com/actes2526/PREF_G1/PREF_G1.html','main')">G1</a></td>
      </tr>
    </table>"""
    categories = HtmlParser.parse_index(html, "https://www.rtbtt.com")
    assert len(categories) == 1
    assert categories[0].groups[0].source_url == "https://www.rtbtt.com/actes2526/PREF_G1/PREF_G1.html"


def test_normalize_category_name():
    cases = [
        ("Preferent", "PREF", 1),
        ("Primera", "1A", 2),
        ("Segona", "2A", 3),
        ("Tercera", "3A", 4),
    ]
    for raw, expected_id, expected_level in cases:
        cat_id, level = HtmlParser._normalize_category_name(raw)
        assert cat_id == expected_id, f"'{raw}' → expected {expected_id}, got {cat_id}"
        assert level == expected_level


# ------------------------------------------------------------------ hashing


def test_md5_bytes_deterministic():
    data = b"hello world"
    assert md5_bytes(data) == md5_bytes(data)
    assert len(md5_bytes(data)) == 32


def test_md5_str_matches_bytes():
    text = "hola mundo"
    assert md5_str(text) == md5_bytes(text.encode("utf-8"))


def test_md5_different_inputs():
    assert md5_str("abc") != md5_str("xyz")


# ------------------------------------------------------------------ PdfParser


@pytest.mark.skipif(
    not (FIXTURES / "sample_acta.pdf").exists(),
    reason="No sample PDF fixture available",
)
def test_parse_acta_returns_matches():
    from parsers.pdf_parser import PdfParser

    parser = PdfParser()
    matches = parser.parse_acta(
        str(FIXTURES / "sample_acta.pdf"), "PREF_G1", 1
    )
    assert isinstance(matches, list)
