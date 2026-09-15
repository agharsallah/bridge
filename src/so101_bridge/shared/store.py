"""Tiny JSON helpers for the config files the operator teaches into.

Taught configuration is hand-editable JSON under ``config/``. It has to survive being missing, being
half-written and being edited by hand with a typo in it: the daemon must come up and *say* what is
wrong rather than refuse to start, because the arm is the only way to fix it.
"""

import json
import re
import time

from ..settings import ARM
from ..util import log


def load_json(path, defaults=None):
    """Parse ``path``, or return ``defaults`` (deep-copied) if it is missing or unreadable — and say so."""
    d = {}
    if path.is_file():
        try:
            d = json.loads(path.read_text())
        except Exception as e:
            log(f"{path.name} unreadable: {e}")
            d = {}
    if not isinstance(d, dict): d = {}
    return _merge(json.loads(json.dumps(defaults or {})), d)


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict): _merge(base[k], v)
        else: base[k] = v
    return base


def save_json(path, d):
    """Write ``d`` with an ``updated`` timestamp. Indented: these files get read and edited by humans."""
    d["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=1))
    return d


def pose_round(p, nd=2):
    """A joint pose trimmed to the arm joints and rounded — what gets stored when the operator teaches."""
    return {j: round(float(p[j]), nd) for j in ARM if j in p}


def safe_name(name, limit=60, fallback="unnamed"):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name).strip())[:limit] or fallback


def catalog(directory, pattern="*.json", fields=("name", "created")):
    """Summaries of every JSON document in a directory, newest name order, errors reported in place."""
    out = []
    if directory.is_dir():
        for p in sorted(directory.glob(pattern)):
            try:
                d = json.loads(p.read_text())
                row = {"name": d.get("name", p.stem)}
                row.update({f: d.get(f) for f in fields if f != "name"})
                out.append(row)
            except Exception as e:
                out.append({"name": p.stem, "error": str(e)})
    return out


def get_path(d, dotted, default=None):
    """``get_path(cfg, "board.corners.a1")`` — the teaching session addresses config this way."""
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur: return default
        cur = cur[part]
    return cur


def set_path(d, dotted, value):
    """``set_path(cfg, "board.corners.a1", pose)``, creating the intermediate dicts."""
    parts = dotted.split("."); cur = d
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict): nxt = {}; cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return d
