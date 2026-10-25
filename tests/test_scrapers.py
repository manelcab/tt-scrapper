from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Config
from parsers.html_parser import HtmlParser
from scrapers.rtbtt_scraper import RtbttScraper

FIXTURES = Path(__file__).parent / "fixtures"


def _make_config(**overrides) -> Config:
    defaults = dict(
        competition_id="rtbtt_2526",
        base_url="https://www.rtbtt.com",
        season_path="actes2526",
        index_url="https://www.rtbtt.com/actes_2526.html",
        output_dir="./output",
        cache_dir="./output/cache",
        pdfs_dir="./output/pdfs",
        dry_run=True,
        log_level="INFO",
        only_categories=[],
        only_groups=[],
        google_credentials="./credentials/firebase-service-account.json",
        firestore_project_id="test-project",
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_get_index_parses_categories():
    html = (FIXTURES / "sample_index.html").read_text()
    mock_http = MagicMock()
    mock_http.fetch_text.return_value = html

    cfg = _make_config()
    scraper = RtbttScraper(cfg, mock_http)
    categories = scraper.get_index()

    assert len(categories) >= 2
    mock_http.fetch_text.assert_called_once_with(cfg.index_url)


def test_get_index_handles_http_error():
    mock_http = MagicMock()
    mock_http.fetch_text.side_effect = RuntimeError("Connection refused")

    cfg = _make_config()
    scraper = RtbttScraper(cfg, mock_http)
    categories = scraper.get_index()

    assert categories == []


def test_scrape_group_skips_when_hash_matches():
    mock_http = MagicMock()
    mock_http.fetch_text.return_value = "<html>unchanged</html>"

    mock_cache = MagicMock()
    mock_cache.get_group_hash.return_value = "some_fixed_hash"

    mock_firestore = MagicMock()

    from utils.hashing import md5_str
    html_content = "<html>unchanged</html>"
    expected_hash = md5_str(html_content)
    mock_cache.get_group_hash.return_value = expected_hash

    from models.competition import Group
    grp = Group(
        id="PREF_G1",
        name="Preferent G1",
        category_id="PREF",
        phase="regular",
        source_url="https://www.rtbtt.com/actes2526/PREF_G1/PREF_G1.html",
    )

    cfg = _make_config()
    scraper = RtbttScraper(cfg, mock_http)
    stats = scraper.scrape_group(grp, mock_cache, mock_firestore, dry_run=True)

    assert stats["skipped"] is True
    assert stats["matches"] == 0
    mock_firestore.save_group.assert_not_called()


def test_scrape_group_processes_when_hash_differs():
    html = (FIXTURES / "sample_group.html").read_text()
    mock_http = MagicMock()
    mock_http.fetch_text.return_value = html
    mock_http.fetch.return_value = b"%PDF-1.4 fake pdf content"

    mock_cache = MagicMock()
    mock_cache.get_group_hash.return_value = "old_hash_differs"
    mock_cache.get_matchday_hash.return_value = None
    mock_cache.is_pdf_cached.return_value = False

    mock_firestore = MagicMock()

    from models.competition import Group
    grp = Group(
        id="PREF_G1",
        name="Preferent G1",
        category_id="PREF",
        phase="regular",
        source_url="https://www.rtbtt.com/actes2526/PREF_G1/PREF_G1.html",
    )

    # Patch PdfParser to avoid needing a real PDF
    with patch("scrapers.rtbtt_scraper.PdfParser") as MockParser:
        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_acta.return_value = []
        MockParser.return_value = mock_parser_instance

        cfg = _make_config()
        scraper = RtbttScraper(cfg, mock_http)
        stats = scraper.scrape_group(grp, mock_cache, mock_firestore, dry_run=True)

    assert stats["skipped"] is False
    mock_cache.set_group_hash.assert_called_once()
