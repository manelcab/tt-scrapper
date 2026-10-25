from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.local_cache import LocalCache


# ----------------------------------------------------------------- LocalCache


def test_group_hash_roundtrip(tmp_path):
    cache = LocalCache(str(tmp_path))
    assert cache.get_group_hash("PREF_G1") is None
    cache.set_group_hash("PREF_G1", "abc123")
    assert cache.get_group_hash("PREF_G1") == "abc123"


def test_matchday_hash_roundtrip(tmp_path):
    cache = LocalCache(str(tmp_path))
    key = "PREF_G1_jornada3"
    assert cache.get_matchday_hash(key) is None
    cache.set_matchday_hash(key, "def456")
    assert cache.get_matchday_hash(key) == "def456"


def test_cache_persists_to_disk(tmp_path):
    cache = LocalCache(str(tmp_path))
    cache.set_group_hash("PREF_G1", "xyz789")

    # Re-load from disk
    cache2 = LocalCache(str(tmp_path))
    assert cache2.get_group_hash("PREF_G1") == "xyz789"


def test_hashes_json_structure(tmp_path):
    cache = LocalCache(str(tmp_path))
    cache.set_group_hash("A", "h1")
    cache.set_matchday_hash("A_j1", "h2")

    data = json.loads((tmp_path / "hashes.json").read_text())
    assert data["groups"]["A"] == "h1"
    assert data["matchdays"]["A_j1"] == "h2"


def test_save_and_detect_pdf(tmp_path):
    pdfs_dir = tmp_path / "pdfs"
    pdfs_dir.mkdir()
    cache = LocalCache(str(tmp_path / "cache"))

    assert not cache.is_pdf_cached("test.pdf")

    cache.save_pdf("test.pdf", b"binary content", str(pdfs_dir))
    assert (pdfs_dir / "test.pdf").read_bytes() == b"binary content"


def test_corrupted_cache_file_recovers(tmp_path):
    (tmp_path / "hashes.json").write_text("not valid json")
    cache = LocalCache(str(tmp_path))  # should not raise
    assert cache.get_group_hash("X") is None


# ----------------------------------------------------------------- FirestoreClient dry-run


def test_firestore_dry_run_no_writes():
    from storage.firestore_client import FirestoreClient
    from models.competition import CompetitionIndex, Category, Group
    from models.match import Match, Matchday
    from models.team import Team

    client = FirestoreClient("test-project", "./credentials/firebase-service-account.json")

    competition = CompetitionIndex(
        competition_id="test",
        name="Test Competition",
        organization="TEST",
        season="2025-2026",
        source_url="http://example.com",
    )

    # In dry_run=True mode, _get_db() should never be called
    with patch.object(client, "_get_db") as mock_db:
        client.save_competition(competition, dry_run=True)
        mock_db.assert_not_called()

    grp = Group(id="PREF_G1", name="Preferent G1", category_id="PREF",
                phase="regular", source_url="http://example.com")
    with patch.object(client, "_get_db") as mock_db:
        client.save_group("test", "PREF", grp, dry_run=True)
        mock_db.assert_not_called()

    matchday = Matchday(number=1, date=None, pdf_url="", pdf_hash="abc")
    with patch.object(client, "_get_db") as mock_db:
        client.save_matchday_with_matches("test", "PREF", "PREF_G1", matchday, [], dry_run=True)
        mock_db.assert_not_called()

    team = Team(id="t1", name="CTT Test", club="CTT Test")
    with patch.object(client, "_get_db") as mock_db:
        client.save_teams("test", "PREF", "PREF_G1", [team], dry_run=True)
        mock_db.assert_not_called()
