"""Keep the test suite out of the repository's runtime files."""

import pytest

from so101_bridge import util
from so101_bridge.chess import history as chess_history
from so101_bridge.chess import log as chess_log
from so101_bridge.chess import workspace as chess_ws
from so101_bridge.paint import workspace as paint_ws
from so101_bridge.shared import teach as teach_mod


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path, monkeypatch):
    """Redirect the daemon log so tests never append to var/bridge.log."""
    monkeypatch.setattr(util, "LOG_FILE", tmp_path / "bridge.log")


@pytest.fixture(autouse=True)
def _paint_config_to_tmp(tmp_path, monkeypatch):
    """Point the painting workspace at a scratch file so tests never read or write config/painting.json."""
    monkeypatch.setattr(paint_ws, "PAINT_FILE", tmp_path / "painting.json")


@pytest.fixture(autouse=True)
def _chess_files_to_tmp(tmp_path, monkeypatch):
    """Same for everything chess writes: the taught workspace, the games, the teaching sessions."""
    monkeypatch.setattr(chess_ws, "CHESS_FILE", tmp_path / "chess.json")
    monkeypatch.setattr(chess_log, "GAMES_DIR", tmp_path / "games")
    monkeypatch.setattr(chess_history, "HISTORY_FILE", tmp_path / "chess_history.jsonl")
    monkeypatch.setattr(teach_mod, "TEACH_DIR", tmp_path / "teach")
