"""Video recorder: tiles the camera frames into one clip file."""

import numpy as np

from so101_bridge import video


def test_recorder_writes_a_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(video, "VIDEO_DIR", tmp_path)
    rec = video.VideoRecorder()
    assert rec.status() is None
    rec.start("test run")
    for i in range(12):
        rec.write({"top": np.full((54, 96, 3), i * 10, np.uint8), "wrist": None})
    st = rec.status()
    assert st and st["frames"] == 12 and "test_run" in st["file"]
    name = rec.stop()
    assert name and (tmp_path / name).stat().st_size > 0
    assert video.VideoRecorder.catalog()[0]["file"] == name
    assert rec.status() is None and rec.stop() is None
