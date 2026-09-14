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
from .paint import kinematics as paint_kin
from .paint import planner as paint_planner
from .paint import program as paint_program
from .paint import workspace as paint_ws
from .paths import ESTOP, LOG_FILE
from .settings import HTTP_PORT, JOINTS
from .util import log
from .video import VIDEO_DIR, VideoRecorder

PAGE = (Path(__file__).parent / "web" / "dashboard.html").read_bytes()
PAINT_PAGE = (Path(__file__).parent / "web" / "paint.html").read_bytes()


def _json(handler, obj, status=200):
    body = json.dumps(obj, default=str).encode()
    handler.send_response(status); handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body))); handler.end_headers(); handler.wfile.write(body)


def paint_state(ctrl):
    """Everything the /paint page needs: workspace, fitted tool model, reachability, programs, progress."""
    st, _ = ctrl.snapshot()
    ws = paint_ws.load()
    tool = paint_kin.ToolModel(ctrl.floor, ws, st.get("limits") or {})
    out = {"workspace": ws, "status": paint_ws.status(ws), "tool": tool.summary(), "programs": paint_program.catalog(),
           "video": ctrl.video.status(), "videos": VideoRecorder.catalog(),
           "progress": None, "auto": ctrl.routine_status, "mode": st.get("mode"), "estop": st.get("estop"),
           "torque_on": st.get("torque_on"), "present": st.get("present"), "time": st.get("time")}
    if tool.ok:
        cols, rows, grid = tool.reachability(1.0); out["reach"] = {"cols": cols, "rows": rows, "grid": grid}
        if st.get("present"):
            try:
                u, v, z = tool.pose_to_uv(st["present"]); out["brush_uv"] = [round(u, 1), round(v, 1), round(z * 100, 1)]
            except Exception: pass
    with ctrl.lock:
        out["progress"] = json.loads(json.dumps(ctrl.progress.get("paint"), default=str)) if ctrl.progress.get("paint") else None
        out["phase"] = ctrl.progress.get("phase"); out["detail"] = ctrl.progress.get("detail")
    return out


def paint_cmd(ctrl, q):
    g = lambda k, d=None: q.get(k, [d])[0]
    a = g("a"); st, _ = ctrl.snapshot(); present = st.get("present") or {}
    if a == "paper_size": paint_ws.set_paper_size(float(g("w")), float(g("h")))
    elif a == "mark_corner": paint_ws.mark_corner(g("name"), present)
    elif a == "mark_extra": paint_ws.mark_extra(float(g("u")), float(g("v")), present)
    elif a == "clear_paper": paint_ws.clear_paper_marks()
    elif a == "station_set":
        rgb = [int(x) for x in (g("rgb") or "128,128,128").split(",")]
        paint_ws.set_station(g("name", ""), g("kind", "color"), rgb)
    elif a == "station_mark": paint_ws.mark_station(g("name"), g("which", "dip"), present)
    elif a == "station_delete": paint_ws.delete_station(g("name"))
    elif a == "brush": paint_ws.set_brush(**{k: v[0] for k, v in q.items() if k != "a"})
    elif a == "run":
        ctrl.paint_request = {"name": g("name")}; ctrl.start_routine("paint")
    elif a == "delete_program": paint_program.delete(g("name"))
    elif a == "trace_border":
        # dry-run program along the paper border at hover height: validates corners, reach and clearance
        ws = paint_ws.load(); tool = paint_kin.ToolModel(ctrl.floor, ws, st.get("limits") or {})
        if not tool.ok: log(f"paint: cannot trace border: {tool.error}"); return
        W, H = ws["paper"]["width_cm"], ws["paper"]["height_cm"]; m = 0.5
        color = next((n for n, s_ in ws["stations"].items() if s_.get("kind") == "color" and s_.get("dip")), None)
        pts = [[m, m], [W - m, m], [W - m, H - m], [m, H - m], [m, m]]
        plan = {"palette": [{"name": color or "border", "rgb": [80, 80, 80]}], "grid": {"cols": 1, "rows": 1, "cells": [[-1]],
                "origin_cm": [0, 0], "size_cm": [W, H]},
                "strokes": [{"color": color or "border", "points": [pts[i], pts[i + 1]]} for i in range(4)]}
        ws2 = json.loads(json.dumps(ws))
        if color is None: ws2["stations"]["border"] = {"kind": "color", "rgb": [80, 80, 80], "dip": None}
        prog = paint_program.compile_program(plan, ws2, tool, "_trace_border", "paper border", dry_run=True)
        paint_program.save(prog); ctrl.paint_request = {"name": "_trace_border"}; ctrl.start_routine("paint")
    else: log(f"paint: unknown command {a!r}")


def paint_plan(ctrl, q, body):
    """POST /paint/plan with the picture as the body: quantise, hatch, compile, save. Returns stats + preview."""
    import base64

    import cv2
    import numpy as np
    g = lambda k, d=None: q.get(k, [d])[0]
    img = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR) if body else None
    if img is None:
        if getattr(ctrl, "paint_image", None) is None: return {"error": "no picture uploaded"}
        img, image_name = ctrl.paint_image
    else:
        image_name = g("image", "upload"); ctrl.paint_image = (img, image_name)
    st, _ = ctrl.snapshot(); ws = paint_ws.load()
    tool = paint_kin.ToolModel(ctrl.floor, ws, st.get("limits") or {})
    if not tool.ok: return {"error": f"tool model: {tool.error}"}
    params = {k: float(g(k)) for k in ("margin_cm", "detail", "white_threshold", "max_stroke_cm") if g(k) not in (None, "")}
    try:
        plan = paint_planner.plan_image(img, ws, params)
        prog = paint_program.compile_program(plan, ws, tool, g("name") or image_name, image_name, dry_run=g("dry") == "1")
    except ValueError as e:
        return {"error": str(e)}
    name = paint_program.save(prog)
    return {"name": name, "stats": prog["stats"], "plan_stats": plan["stats"], "skipped": prog["skipped"],
            "preview_png": base64.b64encode(paint_planner.preview_png(plan)).decode(), "grid": plan["grid"],
            "strokes": plan["strokes"], "palette": plan["palette"]}



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
        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            if u.path == "/paint/plan":
                try: res = paint_plan(ctrl, parse_qs(u.query), body)
                except Exception as e:
                    log(f"paint plan error: {e}"); res = {"error": str(e)}
                _json(self, res)
            else:
                self.send_response(404); self.end_headers()

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
                st["video"] = ctrl.video.status(); st["videos"] = VideoRecorder.catalog()
                body = json.dumps(st).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            elif u.path in ("/top.mjpg", "/wrist.mjpg", "/paint/top.mjpg", "/paint/wrist.mjpg"):
                paint = u.path.startswith("/paint/")
                name = (u.path[7:] if paint else u.path[1:])[:-5]
                self.send_response(200); self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f"); self.end_headers()
                try:
                    while True:
                        with ctrl.lock:
                            frame = (ctrl.paint_jpeg if paint else ctrl.jpeg).get(name)
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
            elif u.path.startswith("/videos/"):
                f = VIDEO_DIR / Path(u.path).name
                if not f.is_file(): self.send_response(404); self.end_headers(); return
                data = f.read_bytes(); ctype = "video/mp4" if f.suffix == ".mp4" else "video/x-msvideo"
                self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", f'inline; filename="{f.name}"'); self.end_headers(); self.wfile.write(data)
            elif u.path == "/paint":
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(PAINT_PAGE))); self.end_headers(); self.wfile.write(PAINT_PAGE)
            elif u.path == "/paint/state":
                _json(self, paint_state(ctrl))
            elif u.path == "/paint/cmd":
                try: paint_cmd(ctrl, parse_qs(u.query))
                except Exception as e: log(f"paint cmd error: {e}")
                self.send_response(204); self.end_headers()
            elif u.path.startswith("/paint/program/"):
                prog = paint_program.load(u.path.rsplit("/", 1)[1])
                _json(self, prog or {"error": "not found"}, 200 if prog else 404)
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
                elif a == "video_start": ctrl.video.start(g("name", "clip"))
                elif a == "video_stop": ctrl.video.stop()
                elif a == "video_delete":
                    f = VIDEO_DIR / Path(g("name", "")).name
                    if f.is_file(): f.unlink(); log(f"VIDEO deleted {f.name}")
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

