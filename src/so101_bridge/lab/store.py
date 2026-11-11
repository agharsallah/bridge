"""A lab's config file plus its pending (staged) changes.

``config/<lab>.json`` is what is saved; ``var/lab/<lab>.pending.json`` is what the operator has staged
but not applied yet (it survives a page reload and a daemon restart). Readers that act on the arm use
:meth:`LabStore.effective` — saved + pending — so a staged point can be visited *before* it is applied.
"""

import copy
import json
import threading
from pathlib import Path

from ..paths import CONFIG_DIR, ROOT, VAR_DIR
from ..shared.store import load_json, save_json
from ..util import log
from . import staging

BASE_DEFAULTS = {
    "points": {},          # name -> {"pose": {joint: deg}, "role": str, "note": str}
    "rest": None,          # {joint: deg} — this lab's rest; None = the global config/rest.json
    "envelope": {},        # joint -> [lo, hi]: lab-only widening of the soft limits (capped by the motor sweep)
    "motion": {"speed": 4.0, "jog_step": 1.0},
    "video": {"dir": "var/videos/lab", "raw": True},     # raw = no overlays; dir is relative to the repo root
    "cameras": {"width": 960, "quality": 70, "fps": 10},  # stream transport only — nothing looks at the pixels
}


class LabStore:
    def __init__(self, name, defaults=None, config_file=None, pending_file=None):
        self.name = name
        self.defaults = copy.deepcopy(BASE_DEFAULTS)
        for k, v in (defaults or {}).items(): self.defaults[k] = copy.deepcopy(v)
        self.config_file = Path(config_file or CONFIG_DIR / f"{name}.json")
        self.pending_file = Path(pending_file or VAR_DIR / "lab" / f"{name}.pending.json")
        self.lock = threading.RLock()

    # -------------------------------------------------------------- reading
    def saved(self):
        return load_json(self.config_file, self.defaults)

    def pending(self):
        if not self.pending_file.is_file(): return []
        try:
            d = json.loads(self.pending_file.read_text())
            return d if isinstance(d, list) else []
        except Exception as e:
            log(f"{self.pending_file.name} unreadable: {e}"); return []

    def effective(self):
        with self.lock: return staging.overlay(self.saved(), self.pending())

    # -------------------------------------------------------------- staging
    def _write_pending(self, pending):
        self.pending_file.parent.mkdir(parents=True, exist_ok=True)
        self.pending_file.write_text(json.dumps(pending, indent=1))
        return pending

    def stage(self, path, value=None, op="set", label=""):
        with self.lock:
            return self._write_pending(staging.stage(self.pending(), self.saved(), path, value, op, label))

    def discard(self, ids=None):
        with self.lock:
            return self._write_pending(staging.discard(self.pending(), ids))

    def apply(self, ids=None):
        """Write the pending changes (or only ``ids``) into the config file; returns the number applied."""
        with self.lock:
            pending, saved = self.pending(), self.saved()
            chosen = [c for c in pending if ids is None or c["id"] in set(ids)]
            if not chosen: return 0
            new = staging.overlay(saved, chosen)
            new.pop("updated", None)
            save_json(self.config_file, new)
            self._write_pending([c for c in pending if c not in chosen])
            log(f"LAB {self.name}: applied {len(chosen)} change(s): {', '.join(c['op'] + ' ' + c['path'] for c in chosen)}")
            return len(chosen)

    def view(self):
        """Everything the page needs about the config: saved, effective, pending and which pending went stale."""
        with self.lock:
            saved, pending = self.saved(), self.pending()
            return {"file": _rel(self.config_file), "pending_file": _rel(self.pending_file), "saved": saved,
                    "effective": staging.overlay(saved, pending), "pending": pending,
                    "stale": staging.stale(pending, saved)}


def _rel(p):
    try: return str(Path(p).resolve().relative_to(ROOT))
    except ValueError: return str(p)


def resolve_dir(d):
    """A directory from the config: relative paths are taken from the repository root."""
    p = Path(str(d or "var/videos/lab")).expanduser()
    return p if p.is_absolute() else ROOT / p
