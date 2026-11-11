"""Staged changes: every edit a lab makes to its config is held as *pending* until the operator applies it.

A change addresses the config by a dotted path (``points.corner_a``, ``rest``, ``envelope.elbow_flex``)
and is either ``set`` (with a value) or ``delete``. Staging the same path twice keeps one entry — the
latest value with the *original* ``before`` — and a change that ends up equal to the saved value drops
out on its own. Pure functions over plain dicts/lists, so they are trivial to test and to reuse.
"""

import copy
import json
import time
import uuid

from ..shared.store import get_path, set_path

_MISSING = object()


def _same(a, b):
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def del_path(d, dotted):
    """Remove ``dotted`` from ``d`` if it is there; parents are left in place."""
    parts = dotted.split("."); cur = d
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None: return d
    if isinstance(cur, dict): cur.pop(parts[-1], None)
    return d


def stage(pending, saved, path, value=None, op="set", label=""):
    """Return a new pending list with this change folded in (see module doc). ``saved`` is the config on disk."""
    if op not in ("set", "delete"): raise ValueError(f"unknown op {op!r}")
    if not path or any(not p for p in path.split(".")): raise ValueError(f"bad path {path!r}")
    before = get_path(saved, path, _MISSING)
    rest = [c for c in pending if c["path"] != path]
    prev = next((c for c in pending if c["path"] == path), None)
    if op == "delete" and before is _MISSING: return rest              # deleting what was never saved = un-staging it
    if op == "set" and before is not _MISSING and _same(before, value): return rest
    change = {"id": prev["id"] if prev else uuid.uuid4().hex[:8], "path": path, "op": op,
              "value": copy.deepcopy(value) if op == "set" else None,
              "before": None if before is _MISSING else copy.deepcopy(before),
              "is_new": before is _MISSING, "label": label or (prev or {}).get("label", ""), "time": time.strftime("%H:%M:%S")}
    return rest + [change]


def discard(pending, ids=None):
    """Drop the given change ids (all of them when ``ids`` is None)."""
    return [] if ids is None else [c for c in pending if c["id"] not in set(ids)]


def overlay(saved, pending, ids=None):
    """``saved`` with the pending changes (or only ``ids``) applied — the config as it *will* be."""
    out = copy.deepcopy(saved)
    for c in pending:
        if ids is not None and c["id"] not in ids: continue
        if c["op"] == "delete": del_path(out, c["path"])
        else: set_path(out, c["path"], copy.deepcopy(c["value"]))
    return out


def stale(pending, saved):
    """Changes whose ``before`` no longer matches the file (someone edited it by hand meanwhile)."""
    out = []
    for c in pending:
        now = get_path(saved, c["path"], _MISSING)
        if (c["is_new"] and now is not _MISSING) or (not c["is_new"] and (now is _MISSING or not _same(now, c["before"]))):
            out.append(c["id"])
    return out
