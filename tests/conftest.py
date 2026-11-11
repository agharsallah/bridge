"""Keep the test suite out of the repository's runtime files."""

import pytest

from so101_bridge import tool as tool_mod
from so101_bridge import util
from so101_bridge.chess import history as chess_history
from so101_bridge.chess import log as chess_log
from so101_bridge.chess import workspace as chess_ws
from so101_bridge.lab import LABS, discover
from so101_bridge.lab.store import LabStore
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


@pytest.fixture(autouse=True)
def _lab_files_to_tmp(tmp_path, monkeypatch):
    """Every lab (template + so101_bridge/labs/*) gets scratch config, pending and journal files."""
    discover()
    for lab in LABS.values():
        monkeypatch.setattr(lab, "store", LabStore(lab.name, lab.store.defaults, tmp_path / f"{lab.name}.json",
                                                   tmp_path / f"{lab.name}.pending.json"))


@pytest.fixture(autouse=True)
def _tool_file_to_tmp(tmp_path, monkeypatch):
    """The mounted-tool file (var/tool.json) is runtime state: never read or write the real one."""
    monkeypatch.setattr(tool_mod, "TOOL_FILE", tmp_path / "tool.json")
