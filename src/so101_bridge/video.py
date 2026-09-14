"""Video recording of the camera feeds (with their overlays) into var/videos/.

The control loop hands the recorder the processed frames (~10 fps, 960x540 each); they are tiled side by
side (top | wrist) into one MP4. Recording starts from a dashboard button or automatically when a routine
starts (``settings.AUTO_RECORD_ROUTINES``) and stops when it finishes.
"""

import re
import threading
import time

import cv2
import numpy as np

from .paths import VAR_DIR
from .util import log

VIDEO_DIR = VAR_DIR / "videos"
FPS = 10.0
CODECS = (("mp4v", ".mp4"), ("avc1", ".mp4"), ("MJPG", ".avi"))


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "").strip())[:40] or "clip"


class VideoRecorder:
    def __init__(self, cams=("top", "wrist")):
        self.cams = tuple(cams)
        self.lock = threading.Lock()
        self.writer = None; self.path = None; self.t0 = 0.0; self.frames = 0; self.size = None; self.auto = False

    def start(self, name="clip", auto=False):
        with self.lock:
            if self.writer is not None:
                return self.path.name
            VIDEO_DIR.mkdir(parents=True, exist_ok=True)
            stem = f"{time.strftime('%Y%m%d_%H%M%S')}_{_safe(name)}"
            self.pending = stem; self.writer = "pending"      # opened on the first frame, when the size is known
            self.path = None; self.t0 = time.time(); self.frames = 0; self.auto = auto
        log(f"VIDEO start -> {stem}")
        return stem

    def _open(self, w, h):
        for fourcc, ext in CODECS:
            path = VIDEO_DIR / f"{self.pending}{ext}"
            wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), FPS, (w, h))
            if wr.isOpened():
                self.writer, self.path, self.size = wr, path, (w, h); return
            wr.release()
        log("VIDEO: no working codec, recording disabled"); self.writer = None

    def write(self, frames):
        """frames: {cam: BGR ndarray}. Missing cameras are filled black; sizes are normalised to the first frame."""
        with self.lock:
            if self.writer is None: return
            tiles = []
            ref = next((f for f in frames.values() if f is not None), None)
            if ref is None: return
            h, w = ref.shape[:2]
            for cam in self.cams:
                f = frames.get(cam)
                if f is None: f = np.zeros((h, w, 3), np.uint8)
                elif f.shape[:2] != (h, w): f = cv2.resize(f, (w, h))
                tiles.append(f)
            frame = np.hstack(tiles)
            if self.writer == "pending":
                self._open(frame.shape[1], frame.shape[0])
                if self.writer is None: return
            cv2.putText(frame, time.strftime("%H:%M:%S"), (frame.shape[1] - 110, frame.shape[0] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            self.writer.write(frame); self.frames += 1

    def stop(self):
        with self.lock:
            wr, path, n = self.writer, self.path, self.frames
            self.writer = None; self.path = None; self.auto = False
        if wr is not None and wr != "pending":
            wr.release(); log(f"VIDEO stop -> {path.name} ({n} frames, {n / FPS:.0f} s)")
            return path.name
        return None

    def status(self):
        with self.lock:
            if self.writer is None: return None
            return {"file": (self.path.name if self.path else self.pending), "seconds": round(time.time() - self.t0, 1),
                    "frames": self.frames, "auto": self.auto}

    @staticmethod
    def catalog():
        out = []
        if VIDEO_DIR.is_dir():
            for p in sorted(VIDEO_DIR.iterdir(), reverse=True):
                if p.suffix in (".mp4", ".avi"):
                    out.append({"file": p.name, "bytes": p.stat().st_size, "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))})
        return out[:50]
