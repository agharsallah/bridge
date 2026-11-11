"""Camera transport for labs: the raw frames, encoded for a browser or a model. Nothing looks at the pixels.

The control loop already keeps the newest raw RGB frame of every camera in ``ctrl.frame`` (stamped in
``ctrl.frame_t``). This module only *ships* them: optional downscale + JPEG, cached per (camera, width,
quality) and frame timestamp, so any number of viewers — browser tabs, a model, an LLM tool — cost one
encode per new frame. Streams push a part only when the camera produced a new frame (never the same
picture twice), at most ``fps`` times a second. Detection, overlays and other vision belong to the
consumer, not here.
"""

import base64
import threading
import time

import cv2
import numpy as np

from ..settings import CAMS, FRAME_MAX_AGE_S

MAX_W = 1920


class FrameServer:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}         # (cam, width, quality) -> (frame_t, jpeg bytes)
        self.served = {}        # cam -> {"fps": float, "t": last push, "clients": int}

    @staticmethod
    def raw(ctrl, cam, max_age_s=FRAME_MAX_AGE_S):
        """(rgb ndarray, timestamp) of the newest frame, or (None, t) if it is missing or older than ``max_age_s``."""
        with ctrl.lock:
            frame, t = ctrl.frame.get(cam), ctrl.frame_t.get(cam)
        if frame is None or t is None or time.time() - t > max_age_s: return None, t
        return frame, t

    def jpeg(self, ctrl, cam, width=960, quality=70, max_age_s=FRAME_MAX_AGE_S):
        """(jpeg bytes, frame timestamp) — re-encoded only when the camera has delivered a new frame."""
        width, quality = int(max(160, min(MAX_W, width))), int(max(30, min(95, quality)))
        frame, t = self.raw(ctrl, cam, max_age_s)
        if frame is None: return None, t
        key = (cam, width, quality)
        with self.lock:
            hit = self.cache.get(key)
        if hit and hit[0] == t: return hit[1], t
        bgr = np.ascontiguousarray(frame[..., ::-1])
        h, w = bgr.shape[:2]
        if w > width: bgr = cv2.resize(bgr, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok: return None, t
        data = buf.tobytes()
        with self.lock: self.cache[key] = (t, data)
        return data, t

    def stream(self, ctrl, cam, width=960, quality=70, fps=10, alive=lambda: True):
        """Yield JPEG parts for a multipart MJPEG response; only new frames, at most ``fps`` per second."""
        period, last_t = 1.0 / max(1.0, min(30.0, float(fps))), None
        self._client(cam, +1)
        try:
            while alive():
                t0 = time.time()
                data, t = self.jpeg(ctrl, cam, width, quality)
                if data and t != last_t:
                    last_t = t; self._pushed(cam, t0)
                    yield data
                time.sleep(max(0.01, period - (time.time() - t0)))
        finally:
            self._client(cam, -1)

    def _client(self, cam, d):
        with self.lock:
            s = self.served.setdefault(cam, {"fps": 0.0, "t": None, "clients": 0}); s["clients"] = max(0, s["clients"] + d)

    def _pushed(self, cam, now):
        with self.lock:
            s = self.served.setdefault(cam, {"fps": 0.0, "t": None, "clients": 0})
            if s["t"]: s["fps"] = round(0.8 * s["fps"] + 0.2 / max(1e-3, now - s["t"]), 1)
            s["t"] = now

    def info(self, ctrl):
        """Per camera: is it delivering, how old is the newest frame, its size, what the streams push."""
        down = _camera_status()
        out = []
        for cam in CAMS:
            with ctrl.lock:
                frame, t = ctrl.frame.get(cam), ctrl.frame_t.get(cam)
            age = None if t is None else round(time.time() - t, 2)
            with self.lock: s = dict(self.served.get(cam) or {})
            live = age is not None and age <= FRAME_MAX_AGE_S
            out.append({"name": cam, "live": live, "age_s": age, "size": list(frame.shape[1::-1]) if frame is not None else None,
                        "down_s": down.get(cam), "stream_fps": s.get("fps", 0.0) if s.get("clients") else 0.0,
                        "clients": s.get("clients", 0)})
        return out

    def grab_b64(self, ctrl, cam, width=640, quality=80):
        """One frame for a model/LLM: ``{"cam", "t", "jpeg_b64"}`` or None if the camera is quiet."""
        data, t = self.jpeg(ctrl, cam, width, quality)
        return None if data is None else {"cam": cam, "t": t, "jpeg_b64": base64.b64encode(data).decode()}


def _camera_status():
    try:
        from ..hardware import camera_status  # lazy: the daemon's camera bookkeeping, absent in tests
        return camera_status()
    except Exception:
        return {}


FRAMES = FrameServer()
