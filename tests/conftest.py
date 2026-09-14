"""Keep the test suite out of the repository's runtime files."""

import pytest

from so101_bridge import util
from so101_bridge.paint import workspace as paint_ws


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path, monkeypatch):
    """Redirect the daemon log so tests never append to var/bridge.log."""
    monkeypatch.setattr(util, "LOG_FILE", tmp_path / "bridge.log")


@pytest.fixture(autouse=True)
def _paint_config_to_tmp(tmp_path, monkeypatch):
    """Point the painting workspace at a scratch file so tests never read or write config/painting.json."""
    monkeypatch.setattr(paint_ws, "PAINT_FILE", tmp_path / "painting.json")
