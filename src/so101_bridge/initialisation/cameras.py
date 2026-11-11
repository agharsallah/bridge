"""Init mode cameras: the same WorkerCamera the daemon uses, pumped into ``ctrl.jpeg`` so the existing
``/top.mjpg`` and ``/wrist.mjpg`` routes stream them with no control loop running."""

import threading
import time

import cv2

from .. import vision
from ..camera_worker import WorkerCamera
from ..settings import CAM_FPS, CAM_H, CAM_SIZE, CAM_W, CAMS, JPEG_QUALITY
from ..util import log

RETRY_S = 5.0


class CameraPump:
    def __init__(self, ctrl):
        self.ctrl, self.cams, self.last, self.error = ctrl, {}, {}, {}
        self._run, self._next_try = True, 0.0
        threading.Thread(target=self._loop, daemon=True).start()

    def _connect_missing(self):
        for name, idx in CAMS.items():
            if name in self.cams: continue
            w, h = CAM_SIZE.get(name, (CAM_W, CAM_H))
            cam = WorkerCamera(name=name, index=idx, width=w, height=h, fps=CAM_FPS)
            try:
                cam.connect(); self.cams[name] = cam; self.error.pop(name, None); log(f"init: camera {name} streaming")
            except Exception as e:
                self.error[name] = f"index {idx}: {e} (macOS: Camera permission for the app that started the bridge)"
                try: cam.disconnect()
                except Exception: pass

    def _loop(self):
        while self._run:
            if len(self.cams) < len(CAMS) and time.time() >= self._next_try:
                self._connect_missing(); self._next_try = time.time() + RETRY_S
            for name, cam in list(self.cams.items()):
                try: rgb = cam.read_latest(max_age_ms=1000)
                except Exception as e:
                    self.error[name] = str(e); self.cams.pop(name, None)
                    try: cam.disconnect()
                    except Exception: pass
                    continue
                ok, buf = cv2.imencode(".jpg", vision.frame(rgb), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ok:
                    with self.ctrl.lock: self.ctrl.jpeg[name] = buf.tobytes()
                    self.last[name] = time.time()
            time.sleep(0.1)

    def retry_now(self):
        self._next_try = 0.0

    def status(self) -> dict:
        """{name: {ok, detail}} — ok when a frame arrived in the last 2 s."""
        now = time.time()
        return {n: {"ok": now - self.last.get(n, 0) < 2.0,
                    "detail": "streaming" if now - self.last.get(n, 0) < 2.0 else self.error.get(n, "starting...")}
                for n in CAMS}

    def close(self):
        self._run = False
        for cam in self.cams.values():
            try: cam.disconnect()
            except Exception: pass
