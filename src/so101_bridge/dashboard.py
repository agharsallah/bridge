"""Dashboard: a dependency-free HTTP server for the operator.

Routes: ``/`` the page, ``/state`` the full state as JSON, ``/top.mjpg`` and ``/wrist.mjpg`` the
camera streams, ``/waypoints`` the taught poses, ``/cmd?a=...`` every button. It is bound to
127.0.0.1 — the arm is never exposed to the network.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import routines
from .controller import Controller
from .floor import FLOOR_MARGIN, GRASP_Z
from .paths import ESTOP, LOG_FILE
from .settings import HTTP_PORT, JOINTS
from .util import log

PAGE = (Path(__file__).parent / "web" / "dashboard.html").read_bytes()


def serve(ctrl: Controller, port: int = HTTP_PORT):
    """Start the dashboard in a daemon thread and return the server."""
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ctrl))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"dashboard: http://localhost:{port}")
    return server


def make_handler(ctrl: Controller):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                body = PAGE; self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            elif u.path == "/state":
                st, blob = ctrl.snapshot(); st["blob"] = blob; st["auto"] = ctrl.routine_status
                st["rec"] = ctrl.rec_name if ctrl.rec_file else None
                z = ctrl.floor.z(st["present"]) if st.get("present") else None
                st["floor"] = {"points": ctrl.floor.n, "fitted": ctrl.floor.params is not None,
                               "rms_mm": round(ctrl.floor.rms * 1000, 1) if ctrl.floor.rms else None,
                               "tip_z_cm": round(z * 100, 1) if z is not None else None,
                               "arm": ctrl.floor.arm_points(st["present"]) if st.get("present") else None,
                               "margin_cm": FLOOR_MARGIN * 100, "grasp_cm": GRASP_Z * 100}
                with ctrl.lock:
                    st["progress"] = json.loads(json.dumps(ctrl.progress, default=str))
                    st["events"] = list(ctrl.events)
                st["preflight"] = ctrl.preflight(st, blob)
                st["routines"] = routines.catalog()
                body = json.dumps(st).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            elif u.path in ("/top.mjpg", "/wrist.mjpg"):
                name = u.path[1:-5]
                self.send_response(200); self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f"); self.end_headers()
                try:
                    while True:
                        with ctrl.lock:
                            frame = ctrl.jpeg.get(name)
                        if frame:
                            self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame))
                            self.wfile.write(frame); self.wfile.write(b"\r\n")
                        time.sleep(0.1)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            elif u.path == "/log":
                n = min(400, max(1, int((parse_qs(u.query).get("n", ["120"])[0]) or 120)))
                try: lines = LOG_FILE.read_text(errors="replace").splitlines()[-n:]
                except OSError: lines = []
                body = json.dumps(lines).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            elif u.path == "/waypoints":
                body = json.dumps(ctrl.wp_load()).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            elif u.path == "/cmd":
                q = parse_qs(u.query); a = q.get("a", [""])[0]; g = lambda k, d=None: q.get(k, [d])[0]
                if a == "goal":
                    j, v = g("j"), g("v")
                    if j in JOINTS and v is not None:
                        ctrl.request({"action": "goal", "goal": {j: float(v)}, "relative": g("rel") == "1",
                                      "speed": float(g("speed", 4)), "src": "web"})
                elif a == "engage": ctrl.request({"action": "engage", "src": "web"})
                elif a == "rest": ctrl.go_rest()
                elif a == "rest_save": ctrl.rest_save()
                elif a == "floor_mark": ctrl.floor_mark(float(g("h", 0) or 0))
                elif a == "rec_start": ctrl.rec_start()
                elif a == "rec_stop": ctrl.rec_stop()
                elif a == "wp_save": ctrl.wp_save(g("name", "").strip()[:40])
                elif a == "wp_goto": ctrl.wp_goto(g("name", ""))
                elif a == "wp_delete": ctrl.wp_delete(g("name", ""))
                elif a == "stop": ESTOP.touch(); ctrl.abort_auto("STOP button")
                elif a == "resume": ESTOP.unlink(missing_ok=True)
                elif a == "hold": ctrl.abort_auto("HOLD button"); ctrl.request({"action": "hold", "src": "web"})
                elif a == "release": ctrl.abort_auto("RELEASE button"); ctrl.request({"action": "release", "src": "web"})
                elif a == "auto": ctrl.start_auto()
                elif a == "routine": ctrl.start_routine(g("name", "pick_place"))
                elif a == "abort": ctrl.abort_auto("ABORT button"); ctrl.request({"action": "hold", "src": "web"})
                self.send_response(204); self.end_headers()
            else:
                self.send_response(404); self.end_headers()
    return H

