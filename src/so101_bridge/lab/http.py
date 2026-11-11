"""HTTP routes for every registered lab, plus the shared front-end modules.

    GET  /<lab>                 the page (web/<page>, read per request: edit, reload, no restart)
    GET  /<lab>/state           everything the page draws (state.build)
    GET  /<lab>/observe[?images=1]   compact observation for a model / LLM (camera JPEGs as base64 on request)
    GET  /<lab>/actions         every action with its parameters and whether it needs a confirm
    POST /<lab>/cmd             {"action": ..., ...} -> {"ok", "error"?, "message"?, "needs_confirm"?}
    GET  /<lab>/cam/<cam>.mjpg?w=960&q=70&fps=10   raw camera stream (new frames only)
    GET  /<lab>/cam/<cam>.jpg?w=&q=                one raw frame
    GET  /<lab>/videos/<file>   a clip from the lab's video directory
    GET  /<lab>/journal?n=50    the lab's recent actions (journal.py)
    GET  /labs                  every registered lab: name, title, prefix, description, ui module
    GET  /static/lab/<file>     shared CSS / JS modules (web/lab/), reused by every lab page
    GET  /static/<lab>/<file>   a lab's own front-end files (web/<lab>/), e.g. its ``ui`` panels module
"""

import json
from pathlib import Path
from urllib.parse import parse_qs

from ..util import log
from .cameras import FRAMES

WEB = Path(__file__).resolve().parent.parent / "web"
STATIC_PREFIX = "/static/"
TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
         ".html": "text/html; charset=utf-8", ".mp4": "video/mp4", ".avi": "video/x-msvideo"}


def _send(h, body, ctype, status=200, extra=()):
    h.send_response(status); h.send_header("Content-Type", ctype); h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    for k, v in extra: h.send_header(k, v)
    h.end_headers(); h.wfile.write(body)


def _json(h, obj, status=200):
    _send(h, json.dumps(obj, default=str).encode(), "application/json", status)


def _num(q, k, default):
    try: return float(q.get(k, [default])[0])
    except (TypeError, ValueError): return default


def _file_in(base, name):
    """``base/name`` if it is a file directly inside ``base`` (no traversal), else None."""
    f = (base / Path(name).name).resolve()
    return f if f.parent == base.resolve() and f.is_file() else None


def handle(h, ctrl, labs, method, u, body=b""):
    """Serve ``u`` if it belongs to a lab; returns False to let the dashboard carry on with its own routes."""
    path = u.path
    if method == "GET" and path.startswith(STATIC_PREFIX):
        folder, _, name = path[len(STATIC_PREFIX):].partition("/")
        f = _file_in(WEB / folder, name) if folder == "lab" or folder in labs else None
        if not f or f.suffix not in TYPES: _send(h, b"not found", "text/plain", 404)
        else: _send(h, f.read_bytes(), TYPES[f.suffix])
        return True
    if method == "GET" and path == "/labs":
        _json(h, [lab.info() for lab in labs.values()]); return True
    lab = labs.get(path.strip("/").split("/")[0])
    if lab is None: return False
    rest, q = path[len(lab.prefix):].rstrip("/"), parse_qs(u.query)
    if method == "POST":
        if rest != "/cmd": return False
        try: res = lab.command(ctrl, json.loads(body or b"{}"))
        except Exception as e:
            log(f"LAB {lab.name} cmd error: {e}"); res = {"ok": False, "error": str(e)}
        _json(h, res); return True
    if rest == "":
        page = WEB / lab.page
        _send(h, page.read_bytes(), TYPES[".html"]) if page.is_file() else _send(h, b"page missing", "text/plain", 404)
    elif rest == "/state": _json(h, lab.state(ctrl))
    elif rest == "/observe": _json(h, lab.observe(ctrl, images=q.get("images", ["0"])[0] == "1"))
    elif rest == "/actions": _json(h, lab.describe())
    elif rest == "/journal": _json(h, lab.journal(_num(q, "n", 50)))
    elif rest.startswith("/cam/") and rest.endswith(".jpg"):
        data, _ = FRAMES.jpeg(ctrl, rest[5:-4], _num(q, "w", 1280), _num(q, "q", 85))
        _send(h, data, "image/jpeg") if data else _send(h, b"no frame", "text/plain", 503)
    elif rest.startswith("/cam/") and rest.endswith(".mjpg"):
        _stream(h, ctrl, rest[5:-5], q, lab.cfg().get("cameras") or {})
    elif rest.startswith("/videos/"):
        f = _file_in(lab.video_dir(), rest[8:])
        if not f or f.suffix not in (".mp4", ".avi"): _send(h, b"not found", "text/plain", 404)
        else: _send(h, f.read_bytes(), TYPES[f.suffix], extra=[("Content-Disposition", f'inline; filename="{f.name}"')])
    else:
        return False
    return True


def _stream(h, ctrl, cam, q, cfg):
    h.send_response(200); h.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
    h.send_header("Cache-Control", "no-store"); h.end_headers()
    gen = FRAMES.stream(ctrl, cam, _num(q, "w", cfg.get("width", 960)), _num(q, "q", cfg.get("quality", 70)),
                        _num(q, "fps", cfg.get("fps", 10)))
    try:
        for data in gen:
            h.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(data))
            h.wfile.write(data); h.wfile.write(b"\r\n"); h.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        gen.close()
