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
from .chess import history as chess_history
from .chess import kinematics as chess_kin
from .chess import view as chess_view
from .chess import vision as chess_vision
from .chess import workspace as chess_ws
from .chess.log import GameLog
from .controller import Controller
from .floor import FLOOR_MARGIN, GRASP_Z
from .paint import kinematics as paint_kin
from .paint import planner as paint_planner
from .paint import program as paint_program
from .paint import workspace as paint_ws
from .paths import ESTOP, LOG_FILE
from .settings import HTTP_PORT, JOINTS
from .shared import teach as teach_mod
from .util import log
from .video import VIDEO_DIR, VideoRecorder

PAGE = (Path(__file__).parent / "web" / "dashboard.html").read_bytes()
PAINT_PAGE = (Path(__file__).parent / "web" / "paint.html").read_bytes()
CHESS_PAGE = (Path(__file__).parent / "web" / "chess.html").read_bytes()


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



# ------------------------------------------------------------------------------- chess page
def chess_state(ctrl):
    """Everything the /chess page needs: taught workspace, fitted model, reachability, teaching, game,
    the live camera reading, the arm as the floor model sees it, and what the history has learned."""
    st, _ = ctrl.snapshot()
    d = chess_ws.load()
    model = chess_kin.GripperModel(ctrl.floor, d, st.get("limits") or {})
    out = {"workspace": d, "status": chess_ws.status(d), "model": model.summary(),
           "squares": chess_ws.squares(d), "sessions": teach_mod.sessions(),
           "auto": ctrl.routine_status, "games": GameLog.catalog(),
           "arm": {k: st.get(k) for k in ("mode", "torque_on", "estop", "present", "time")}}
    with ctrl.lock:
        out["teach"] = ctrl.teach.state() if ctrl.teach else None
    out["reach"] = model.reachability_report() if model.ok else {}
    if model.ok and st.get("present"):
        try: out["jaws"] = list(model.pose_square(st["present"]))
        except Exception: pass
    # the arm as a side view, plus where the board plane and the working heights sit on it
    out["side"] = {"arm": ctrl.floor.arm_points(st["present"]) if st.get("present") else None,
                   "fitted": ctrl.floor.params is not None,
                   "margin_cm": FLOOR_MARGIN * 100,
                   "board_z_cm": round(model.z_plane * 100, 1) if model.ok else None,
                   "heights": d["heights"] if model.ok else {}}
    g = GameLog.latest()
    doc = g.doc if g else None
    out["moves"] = chess_view.move_rows(doc)
    out["board"] = chess_view.board_view(d, doc["fen"] if doc else None,
                                         out["moves"][-1] if out["moves"] else None,
                                         doc.get("start_fen") if doc else None)
    out["camera"] = chess_camera_view(ctrl, d, doc["fen"] if doc else None)
    if g:
        out["game"] = g.summary(); out["pgn"] = g.doc.get("pgn")
    stats = chess_history.stats(d=d)
    out["history"] = stats
    out["suggestions"] = chess_history.suggestions(stats, d)
    return out


def chess_camera_view(ctrl, d, fen):
    """What the camera says right now, against what the rules say should be there.

    ``None`` until the image corners are taught and an empty-board reference has been captured — the
    page says which of the two is missing rather than showing an empty grid."""
    reader = getattr(ctrl, "chess_reader", None)
    if reader is None or not reader.ok:
        return {"ok": False, "why": "the board's image corners have not been taught — click them on the view above"}
    if reader.reference is None:
        return {"ok": False, "why": "no empty-board reference yet — clear the board and press Capture empty board"}
    frame = ctrl.latest_frame(d["board"].get("cam"))
    if frame is None:
        return {"ok": False, "why": f"no frame from camera {d['board'].get('cam')!r}"}
    try:
        read = reader.read(frame)
    except Exception as e:
        return {"ok": False, "why": f"could not read the board: {e}"}
    if read is None:
        return {"ok": False, "why": "the board reader is not ready"}
    from .chess import game as chess_game
    expected = chess_game.occupancy(chess_game.new_board(fen))
    cells = []
    for name in chess_ws.squares(d):
        seen = bool(read["occupancy"].get(name))
        want = bool(expected.get(name))
        cells.append({"name": name, "seen": seen, "expected": want, "agree": seen == want,
                      "colour": read["colours"].get(name),
                      "diff": round(read["features"][name]["diff"], 1) if name in read["features"] else None})
    return {"ok": True, "cells": cells, "stable": reader.stable(),
            "mismatch": [c["name"] for c in cells if not c["agree"]],
            "threshold": reader.threshold, "light_dark": reader.light_dark}


def chess_overlay(ctrl, cam):
    """The raw overhead frame with the taught board drawn on it — encoded on demand, not in the loop."""
    import cv2
    import numpy as np
    frame = ctrl.latest_frame(cam)
    if frame is None: return None
    img = frame.copy()
    d = chess_ws.load()
    ic = d["board"].get("image_corners") or {}
    pts = [ic[n] for n in chess_ws.CORNERS if n in ic]
    for n in chess_ws.CORNERS:
        if n in ic:
            x, y = int(ic[n][0]), int(ic[n][1])
            cv2.circle(img, (x, y), 7, (0, 200, 255), 2)
            cv2.putText(img, n, (x + 9, y - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
    if len(pts) == 4:
        cv2.polylines(img, [np.array(pts, np.int32)], True, (0, 200, 255), 2)
    h = img.shape[0]
    scale = 720.0 / h if h > 720 else 1.0
    if scale != 1.0: img = cv2.resize(img, None, fx=scale, fy=scale)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
    return buf.tobytes() if ok else None


def chess_warp_jpeg(ctrl):
    """The warped 8x8 board as a still JPEG, with the square grid drawn on it.

    A still rather than a stream: the board only changes between moves, and warping every frame at
    30 Hz would cost more than the whole control loop."""
    import cv2
    d = chess_ws.load()
    reader = getattr(ctrl, "chess_reader", None)
    H = reader.H if (reader is not None and reader.ok) else chess_vision.homography(d)
    if H is None: return None
    frame = ctrl.latest_frame(d["board"].get("cam"))
    if frame is None: return None
    px = int(d["vision"].get("warp_px", chess_vision.WARP_PX))
    img = chess_vision.warp(frame, H, px)
    cell = px // 8
    names = chess_vision.square_names(d, px)
    for k in range(9):
        cv2.line(img, (k * cell, 0), (k * cell, px), (0, 200, 255), 1)
        cv2.line(img, (0, k * cell), (px, k * cell), (0, 200, 255), 1)
    for (r, c), name in names.items():
        cv2.putText(img, name, (c * cell + 3, r * cell + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 200, 255), 1)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return buf.tobytes() if ok else None


def chess_cmd(ctrl, payload):
    """One entry point for every chess and teaching command, shared with the file interface."""
    from . import app
    act = payload.get("action", "")
    st, _ = ctrl.snapshot()
    if act.startswith("teach_"):
        return teach_mod.command(ctrl, act, **{k: v for k, v in payload.items() if k != "action"})
    if act == "routine":
        ctrl.start_routine(payload.get("name", "chess_verify")); return {"ok": True}
    app.chess_command(ctrl, act, payload, st.get("limits") or {}, st.get("present") or {})
    return {"ok": True}


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
            if u.path == "/chess/cmd":
                try: res = chess_cmd(ctrl, json.loads(body or b"{}"))
                except Exception as e:
                    log(f"chess cmd error: {e}"); res = {"error": str(e)}
                _json(self, res)
            elif u.path == "/paint/plan":
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
            elif u.path == "/chess":
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(CHESS_PAGE))); self.end_headers(); self.wfile.write(CHESS_PAGE)
            elif u.path == "/chess/state":
                _json(self, chess_state(ctrl))
            elif u.path == "/chess/cmd":
                try: chess_cmd(ctrl, {k: v[0] for k, v in parse_qs(u.query).items()})
                except Exception as e: log(f"chess cmd error: {e}")
                self.send_response(204); self.end_headers()
            elif u.path == "/chess/warp.jpg":
                body = chess_warp_jpeg(ctrl)
                if not body: self.send_response(503); self.end_headers(); return
                self.send_response(200); self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            elif u.path == "/chess/history":
                _json(self, {"rows": chess_history.read(limit=int((parse_qs(u.query).get("n", ["200"])[0]) or 200)),
                             "stats": chess_history.stats()})
            elif u.path.startswith("/chess/game/"):
                g = GameLog.load(u.path.rsplit("/", 1)[1])
                _json(self, g.doc if g else {"error": "not found"}, 200 if g else 404)
            elif u.path.endswith(".mjpg") and u.path.startswith("/chess/"):
                cam = u.path[len("/chess/"):-len(".mjpg")]
                self.send_response(200); self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f"); self.end_headers()
                try:
                    while True:
                        frame = chess_overlay(ctrl, cam)
                        if frame:
                            self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame))
                            self.wfile.write(frame); self.wfile.write(b"\r\n")
                        time.sleep(0.2)
                except (BrokenPipeError, ConnectionResetError):
                    pass
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

