"""Keep the test suite out of the repository's runtime files."""

import pytest

from so101_bridge import util


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path, monkeypatch):
    """Redirect the daemon log so tests never append to var/bridge.log."""
    monkeypatch.setattr(util, "LOG_FILE", tmp_path / "bridge.log")
