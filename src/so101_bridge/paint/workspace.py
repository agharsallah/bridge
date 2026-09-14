"""The painting workspace: everything the operator teaches, stored in config/painting.json.

    {
      "paper": {"width_cm": 21.0, "height_cm": 14.8,
                "corners": {"A": pose, "B": pose, "C": pose, "D": pose},   # brush tip ON the paper, A top-left,
                "extra": [{"u": 10.5, "v": 7.4, "pose": pose}]}              # B top-right, C bottom-right, D bottom-left
      "stations": {"red": {"kind": "color", "rgb": [200, 30, 30], "dip": pose, "hover": pose|null},
                   "water": {"kind": "water", "dip": pose, "hover": pose|null},
                   "towel": {"kind": "towel", "dip": pose, "hover": pose|null}},
      "brush": {"hover_cm": 1.5, "press_cm": 0.15, "width_mm": 6.0, "dip_every_cm": 12.0,
                "stroke_speed": 6.0, "travel_speed": 8.0, "dip_dwell_s": 1.0, "rinse_dips": 2}
    }

Poses are joint dicts (degrees). "u" runs along the paper width (A->B), "v" down the height (A->D), in cm.
"""

import json
import time

from ..paths import CONFIG_DIR
from ..settings import ARM
from ..util import log

PAINT_FILE = CONFIG_DIR / "painting.json"
CORNERS = ("A", "B", "C", "D")
KINDS = ("color", "water", "towel")
BRUSH_DEFAULTS = {"hover_cm": 1.5, "press_cm": 0.15, "width_mm": 6.0, "dip_every_cm": 12.0,
                  "stroke_speed": 6.0, "travel_speed": 8.0, "dip_dwell_s": 1.0, "rinse_dips": 2}


def _pose(p):
    return {j: round(float(p[j]), 2) for j in ARM if j in p}


def load():
    try:
        d = json.loads(PAINT_FILE.read_text()) if PAINT_FILE.is_file() else {}
    except Exception as e:
        log(f"config/painting.json unreadable: {e}"); d = {}
    d.setdefault("paper", {}).setdefault("corners", {}); d["paper"].setdefault("extra", [])
    d["paper"].setdefault("width_cm", 21.0); d["paper"].setdefault("height_cm", 14.8)
    d.setdefault("stations", {}); d["brush"] = {**BRUSH_DEFAULTS, **d.get("brush", {})}
    return d


def save(d):
    d["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    PAINT_FILE.write_text(json.dumps(d, indent=1))


def corner_uv(d, name):
    w, h = float(d["paper"]["width_cm"]), float(d["paper"]["height_cm"])
    return {"A": (0.0, 0.0), "B": (w, 0.0), "C": (w, h), "D": (0.0, h)}[name]


def contact_points(d):
    """All taught brush-on-paper poses with their paper coordinates: [(u, v, pose), ...]."""
    pts = [(*corner_uv(d, n), d["paper"]["corners"][n]) for n in CORNERS if n in d["paper"]["corners"]]
    pts += [(float(e["u"]), float(e["v"]), e["pose"]) for e in d["paper"].get("extra", [])]
    return pts


# ---------------------------------------------------------------- teaching operations (called from the dashboard)
def set_paper_size(width_cm, height_cm):
    d = load(); d["paper"]["width_cm"], d["paper"]["height_cm"] = float(width_cm), float(height_cm); save(d)
    log(f"paint: paper {width_cm} x {height_cm} cm")


def mark_corner(name, pose):
    if name not in CORNERS: raise ValueError(name)
    d = load(); d["paper"]["corners"][name] = _pose(pose); save(d)
    log(f"paint: corner {name} = {d['paper']['corners'][name]}")


def mark_extra(u, v, pose):
    d = load(); d["paper"]["extra"].append({"u": float(u), "v": float(v), "pose": _pose(pose)}); save(d)
    log(f"paint: extra contact at ({u}, {v}) cm")


def clear_paper_marks():
    d = load(); d["paper"]["corners"], d["paper"]["extra"] = {}, []; save(d); log("paint: paper marks cleared")


def set_station(name, kind, rgb=None):
    if kind not in KINDS: raise ValueError(kind)
    name = name.strip()[:24]
    if not name: raise ValueError("station name")
    d = load(); st = d["stations"].setdefault(name, {"dip": None, "hover": None})
    st["kind"] = kind
    if kind == "color": st["rgb"] = [int(x) for x in (rgb or [128, 128, 128])][:3]
    save(d); log(f"paint: station {name} ({kind})")


def mark_station(name, which, pose):
    if which not in ("dip", "hover"): raise ValueError(which)
    d = load()
    if name not in d["stations"]: raise KeyError(name)
    d["stations"][name][which] = _pose(pose); save(d); log(f"paint: station {name}.{which} = {d['stations'][name][which]}")


def delete_station(name):
    d = load(); d["stations"].pop(name, None); save(d); log(f"paint: station {name} deleted")


def set_brush(**kw):
    d = load()
    for k, v in kw.items():
        if k in BRUSH_DEFAULTS and v is not None: d["brush"][k] = float(v)
    save(d); log(f"paint: brush {d['brush']}")


def status(d=None):
    d = d or load()
    corners = [n for n in CORNERS if n in d["paper"]["corners"]]
    colors = [n for n, s in d["stations"].items() if s.get("kind") == "color" and s.get("dip")]
    water = [n for n, s in d["stations"].items() if s.get("kind") == "water" and s.get("dip")]
    return {"paper": {"width_cm": d["paper"]["width_cm"], "height_cm": d["paper"]["height_cm"], "corners": corners,
                      "extra": len(d["paper"].get("extra", []))},
            "colors": colors, "water": water,
            "towel": [n for n, s in d["stations"].items() if s.get("kind") == "towel" and s.get("dip")],
            "ready": len(corners) == 4 and bool(colors) and bool(water)}
