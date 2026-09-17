"""A camera that lives in its own process, so a USB drop can be recovered from by starting a new one.

On macOS, AVFoundation never re-enumerates a UVC camera that fell off the bus and came back *inside the
process that had it open*: the index keeps pointing at the vanished device, which opens but never delivers a
frame, and the re-plugged one is invisible. A fresh process sees it immediately. The wrist camera rides the
moving arm and drops now and then, so it (and, for one code path, the overhead camera too) is read by a child
process that writes each frame into shared memory. When the stream dies the child is killed and a new one
spawned — that respawn *is* the recovery.

Duck-types the four things :mod:`hardware` asks of a lerobot camera: ``connect()``, ``disconnect()``,
``is_connected`` and ``read_latest(max_age_ms)``, which returns an RGB ``HxWx3`` uint8 array like lerobot's.
"""

import multiprocessing as mp
import time
from multiprocessing import shared_memory

import numpy as np

MAX_READ_FAILURES = 30      # consecutive read() misses before the child gives up and exits (~1 s at 30 fps)


def _run(index, width, height, fps, shm_name, stamp, lock, stop):
    """Child process: open the camera, copy every frame into shared memory, exit when the stream dies."""
    import cv2
    cap = cv2.VideoCapture(index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    shm = shared_memory.SharedMemory(name=shm_name)
    buf = np.ndarray((height, width, 3), dtype=np.uint8, buffer=shm.buf)
    misses = 0
    try:
        while not stop.is_set():
            ok, frame = cap.read()
            if not ok:
                misses += 1
                if misses > MAX_READ_FAILURES: return
                time.sleep(0.02); continue
            misses = 0
            if frame.shape[:2] != (height, width): frame = cv2.resize(frame, (width, height))
            with lock:
                cv2.cvtColor(frame, cv2.COLOR_BGR2RGB, dst=buf)
                stamp.value = time.time()
    finally:
        cap.release(); shm.close()


class WorkerCamera:
    def __init__(self, name, index, width, height, fps=30, warmup_s=4.0):
        self.name, self.index, self.width, self.height, self.fps = name, index, width, height, fps
        self.warmup_s = warmup_s
        self._proc = self._shm = None
        self._ctx = mp.get_context("spawn")           # never fork: AVFoundation does not survive a fork

    def __repr__(self): return f"WorkerCamera({self.name}@{self.index})"

    @property
    def is_connected(self):
        return self._proc is not None and self._proc.is_alive()

    def connect(self):
        self._shm = shared_memory.SharedMemory(create=True, size=self.width * self.height * 3)
        self._buf = np.ndarray((self.height, self.width, 3), dtype=np.uint8, buffer=self._shm.buf)
        self._stamp = self._ctx.Value("d", 0.0); self._lock = self._ctx.Lock(); self._stop = self._ctx.Event()
        self._proc = self._ctx.Process(target=_run, name=f"cam-{self.name}", daemon=True,
                                       args=(self.index, self.width, self.height, self.fps,
                                             self._shm.name, self._stamp, self._lock, self._stop))
        self._proc.start()
        deadline = time.time() + self.warmup_s
        while time.time() < deadline and self._proc.is_alive():
            if self._stamp.value > 0: return
            time.sleep(0.05)
        why = "exited" if not self._proc.is_alive() else f"no frame within {self.warmup_s:.0f} s"
        self.disconnect()
        raise ConnectionError(f"{self} {why}")

    def read_latest(self, max_age_ms=500):
        if not self.is_connected: raise RuntimeError(f"{self} process is not running.")
        t = self._stamp.value
        if t <= 0: raise RuntimeError(f"{self} has not captured any frames yet.")
        age_ms = (time.time() - t) * 1e3
        if age_ms > max_age_ms: raise TimeoutError(f"{self} latest frame is too old: {age_ms:.1f} ms (max allowed: {max_age_ms} ms).")
        with self._lock: return self._buf.copy()

    def disconnect(self):
        if self._proc is not None:
            self._stop.set(); self._proc.join(timeout=2.0)
            if self._proc.is_alive(): self._proc.kill(); self._proc.join(timeout=1.0)
            self._proc = None
        if self._shm is not None:
            try: self._shm.close(); self._shm.unlink()
            except Exception: pass
            self._shm = None
