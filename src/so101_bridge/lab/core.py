"""``Lab``: a named experiment space — its config store, its actions, its state, its page.

    from so101_bridge.lab import Lab, BUILTIN
    pour = Lab("pour", "Pouring lab", defaults={"cup": {"height_cm": 9}})

    @pour.action("fill", "Fill the cup", params={"ml": "number"}, moves=True)
    def fill(lab, ctrl, args): ...

    @pour.state_key("cup")
    def cup(lab, ctrl, st): return {...}

Every action takes ``(lab, ctrl, args)`` and returns a dict / str / None, or raises (ValueError,
RuntimeError) with a message the operator will read. Actions declared with ``confirm=...`` are refused
until the caller repeats them with ``confirm: "yes"`` — the page shows the text in a dialog first; an
LLM tool sees ``needs_confirm`` and has to ask. ``describe()`` lists every action with its parameters,
which is all a model needs to build tool definitions.
"""

from dataclasses import dataclass, field
from typing import Callable

from ..util import log
from . import journal
from . import state as lab_state
from .cameras import FRAMES
from .store import LabStore, resolve_dir


@dataclass(frozen=True)
class Action:
    name: str
    fn: Callable
    label: str = ""
    description: str = ""
    params: dict = field(default_factory=dict)     # name -> "number" | "string" | "pose" | "bool" | "list" (+ "?" = optional)
    confirm: object = ""                            # str or fn(lab, ctrl, args) -> str: refused without confirm="yes"
    moves: bool = False                             # the arm may move — refused while ESTOP is set
    group: str = "lab"

    def meta(self):
        return {"name": self.name, "label": self.label or self.name, "description": self.description,
                "params": self.params, "confirm": (self.confirm if isinstance(self.confirm, str) else "yes (details at call time)") or None, "moves": self.moves, "group": self.group}


class Lab:
    def __init__(self, name, title=None, defaults=None, page="lab.html", store=None, actions=None, ui=None, description=""):
        """``page``: the HTML in web/ (the shared lab.html fits most labs); ``ui``: an optional JS module in
        web/<name>/ whose ``panels`` become extra tabs on that page (see docs/lab.md)."""
        self.name, self.title, self.page, self.ui, self.description = name, title or name.title(), page, ui, description
        self.prefix = f"/{name}"
        self.store = store or LabStore(name, defaults)
        self.actions: dict[str, Action] = dict(actions or {})
        self.extra_state: dict[str, Callable] = {}

    # ------------------------------------------------------------ extension points
    def action(self, name, label="", description="", params=None, confirm="", moves=False, group=None):
        def deco(fn):
            self.actions[name] = Action(name, fn, label, description, dict(params or {}), confirm, moves, group or self.name)
            return fn
        return deco

    def state_key(self, key):
        """Add ``key`` to this lab's state, computed by ``fn(lab, ctrl, snapshot)`` on every poll."""
        def deco(fn):
            self.extra_state[key] = fn
            return fn
        return deco

    # ------------------------------------------------------------ what the routes call
    def describe(self):
        return [a.meta() for a in self.actions.values()]

    def state(self, ctrl):
        return lab_state.build(self, ctrl, FRAMES)

    def observe(self, ctrl, images=False):
        return lab_state.observe(self, ctrl, FRAMES, images=images)

    def command(self, ctrl, body):
        """Run one action: ``{"action": name, ...args}`` -> ``{"ok": bool, "error"?, "message"?, ...}``."""
        name = str(body.get("action") or "")
        act = self.actions.get(name)
        if act is None: return {"ok": False, "error": f"unknown action {name!r}"}
        if act.confirm and str(body.get("confirm", "")).lower() != "yes":
            q = act.confirm(self, ctrl, body) if callable(act.confirm) else act.confirm
            if q: return {"ok": False, "needs_confirm": q, "action": name}      # "" = nothing to confirm: run (and fail with the reason)
        if act.moves:
            st, _ = ctrl.snapshot()
            if st.get("estop"):
                res = {"ok": False, "error": "ESTOP is set: press RESUME first", "action": name}
                self.note(name, body, res); return res
        args = {k: v for k, v in body.items() if k not in ("action", "confirm")}
        try:
            res = act.fn(self, ctrl, args)
        except (ValueError, RuntimeError, KeyError, TypeError) as e:
            log(f"LAB {self.name} {name}: {e}")
            res = {"ok": False, "error": str(e), "action": name}
            self.note(name, args, res); return res
        out = {"ok": True, "action": name, **res} if isinstance(res, dict) else {"ok": True, "action": name, "message": res or ""}
        self.note(name, args, out)
        return out

    def note(self, name, args, res):
        """Write one journal line; a full disk must never fail the action that already ran."""
        try: journal.add(self.journal_file, name, args, res)
        except OSError as e: log(f"LAB {self.name} journal: {e}")

    @property
    def journal_file(self):
        return self.store.pending_file.with_name(f"{self.name}.journal.jsonl")

    def journal(self, n=50):
        return journal.tail(self.journal_file, n)

    def info(self):
        """What a page and the lab list need to know about this lab."""
        return {"name": self.name, "title": self.title, "prefix": self.prefix, "description": self.description,
                "module": f"/static/{self.name}/{self.ui}" if self.ui else None}

    # ------------------------------------------------------------ helpers the actions share
    def cfg(self):
        return self.store.effective()

    def video_dir(self):
        return resolve_dir((self.cfg().get("video") or {}).get("dir"))

    def videos(self):
        from ..video import VideoRecorder
        return VideoRecorder.catalog(self.video_dir())
