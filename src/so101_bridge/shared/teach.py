"""Guided teaching: the daemon asks, the operator drives the arm, the daemon records.

Teaching a workspace used to mean knowing which dashboard button to press in which order, and the
order only existed in someone's head. A session turns it into a script the daemon reads out:

    {"action": "teach_start", "session": "chess_board"}     # or the Teach panel's Start button
    -> /state.teach = {"prompt": "Gripper closed. Fingertips on the CENTRE of a1.", "step": 1, "steps": 9}
    operator free-drives the arm there (teach_free releases the torque, teach_engage takes it back)
    {"action": "teach_done"}                                # records what this step asks for, moves on

``teach_skip`` leaves a step unrecorded, ``teach_back`` returns to the previous one, ``teach_fix``
jumps to a named step, ``teach_abort`` stops. Everything is written to the domain's config file as
soon as it is recorded, so an interrupted session keeps what it already learned.

Session files live in ``config/teach/<name>.json``::

    {"name": "chess_board", "label": "Chess board", "store": "chess", "steps": [
      {"id": "a1", "kind": "contact", "prompt": "...", "to": "board.corners.a1", "uv": [0, 25.4]},
      {"id": "grasp_k", "kind": "grasp", "prompt": "...", "to": "pieces.k", "piece": "k", "square": "e4"},
      {"id": "probe_e4", "kind": "probe", "prompt": "...", "uv": [14.5, 14.5], "z_cm": 1.0}]}

``kind`` decides what Done records:

    contact  present arm pose                      -> the pose at ``to``, or, with ``append``, an entry
                                                      ``{**with, "pose": pose}`` in the list at that path
                                                      (replacing any entry whose ``match`` key is equal)
    grasp    present arm pose + gripper opening    -> {"grasp_pose", "close"} merged into ``to``
    probe    (u, v) and the commanded height       -> appended to the store's ``probes`` list
    pose     present arm pose                      -> the pose at ``to`` (same as contact; reads better
                                                      for things that are not on the surface, e.g. a slot)
    note     nothing                               -> just shows a prompt and waits for Done

A store is a module with ``load()`` and ``save(d)``; if it also defines ``on_teach(cfg, step, record, ctrl)``
that hook runs after each recording, which is where a domain turns a raw pose into what it actually wants
(a grasp height above the board, the lean the operator used).
"""

import json

from ..paths import TEACH_DIR
from ..util import log
from .store import pose_round, set_path

KINDS = ("contact", "grasp", "probe", "pose", "note")
STORES = {"chess": "so101_bridge.chess.workspace", "paint": "so101_bridge.paint.workspace"}


def sessions():
    """Every session file on disk: [{"name", "label", "store", "steps"}, ...]."""
    out = []
    if TEACH_DIR.is_dir():
        for p in sorted(TEACH_DIR.glob("*.json")):
            try:
                d = json.loads(p.read_text())
                out.append({"name": d.get("name", p.stem), "label": d.get("label", p.stem),
                            "store": d.get("store"), "steps": len(d.get("steps", []))})
            except Exception as e:
                out.append({"name": p.stem, "error": str(e)})
    return out


def load_session(name):
    path = TEACH_DIR / f"{name}.json"
    if not path.is_file(): raise FileNotFoundError(f"no teaching session {name!r} in config/teach/")
    spec = json.loads(path.read_text())
    steps = spec.get("steps") or []
    if not steps: raise ValueError(f"session {name!r} has no steps")
    for i, s in enumerate(steps):
        if s.get("kind", "contact") not in KINDS: raise ValueError(f"step {i} ({s.get('id')}): unknown kind {s.get('kind')!r}")
        if not s.get("id"): raise ValueError(f"step {i} has no id")
    spec.setdefault("name", name)
    return spec


def _store(name):
    import importlib
    if name not in STORES: raise ValueError(f"unknown store {name!r} (known: {sorted(STORES)})")
    return importlib.import_module(STORES[name])


class TeachSession:
    """One run through a session file. Owned by the controller (``ctrl.teach``), driven by commands."""

    def __init__(self, ctrl, spec):
        self.ctrl, self.spec = ctrl, spec
        self.name = spec["name"]
        self.label = spec.get("label", self.name)
        self.steps = spec["steps"]
        self.store = _store(spec.get("store", "chess"))
        self.i = 0
        self.recorded = {}              # step id -> short description of what was stored
        self.skipped = []
        self.finished = False
        self.message = ""
        log(f"teach: session {self.name} started ({len(self.steps)} steps)")

    # ------------------------------------------------------------------ what the operator sees
    @property
    def step(self):
        return self.steps[self.i] if 0 <= self.i < len(self.steps) else None

    def state(self):
        s = self.step or {}
        return {"session": self.name, "label": self.label, "step": min(self.i + 1, len(self.steps)),
                "steps": len(self.steps), "id": s.get("id"), "kind": s.get("kind", "contact"),
                "prompt": s.get("prompt", ""), "free": bool(s.get("free")), "finished": self.finished,
                "recorded": self.recorded, "skipped": list(self.skipped), "message": self.message,
                "remaining": [st["id"] for st in self.steps[self.i:]] if not self.finished else []}

    # ------------------------------------------------------------------ commands
    def done(self):
        """Record what the current step asks for, then advance."""
        if self.finished: return self.state()
        s = self.step
        st, _ = self.ctrl.snapshot(); present = st.get("present") or {}
        if not present:
            self.message = "no arm state yet — is the control loop running?"; return self.state()
        kind = s.get("kind", "contact")
        cfg = self.store.load()
        if kind == "note":
            record = None
        elif kind == "probe":
            record = {"u": float(s["uv"][0]), "v": float(s["uv"][1]), "z_touch_cm": float(s.get("z_cm", 0.0)),
                      "note": s.get("id", "")}
            probes = [p for p in cfg.get("probes", []) if (p.get("u"), p.get("v")) != (record["u"], record["v"])]
            cfg["probes"] = probes + [record]
        elif kind == "grasp":
            record = {"grasp_pose": pose_round(present), "close": round(float(present.get("gripper", 0.0)), 1)}
            cur = _at(cfg, s["to"]) or {}
            set_path(cfg, s["to"], {**cur, **record})
        elif s.get("append"):                                    # one entry in a list, e.g. board.extra
            record = {**(s.get("with") or {}), "pose": pose_round(present)}
            key = s.get("match")
            rows = _at(cfg, s["append"]) or []
            if key: rows = [r for r in rows if r.get(key) != record.get(key)]
            set_path(cfg, s["append"], list(rows) + [record])
        else:                                                    # contact / pose
            record = pose_round(present)
            set_path(cfg, s["to"], record)
        hook = getattr(self.store, "on_teach", None)
        if hook is not None:
            try: hook(cfg, s, record, self.ctrl)
            except Exception as e: log(f"teach: {self.name}/{s['id']} on_teach failed: {e}")
        self.store.save(cfg)
        self.recorded[s["id"]] = _describe(kind, record)
        self.skipped = [x for x in self.skipped if x != s["id"]]
        log(f"teach: {self.name}/{s['id']} recorded {self.recorded[s['id']]}")
        self.message = ""
        return self._advance()

    def skip(self):
        s = self.step
        if s is None: return self.state()
        if s["id"] not in self.skipped: self.skipped.append(s["id"])
        log(f"teach: {self.name}/{s['id']} skipped")
        return self._advance()

    def back(self):
        self.finished = False
        self.i = max(0, self.i - 1); self.message = ""
        return self.state()

    def fix(self, step_id):
        """Jump back to a named step to re-record it."""
        for k, s in enumerate(self.steps):
            if s["id"] == step_id:
                self.i, self.finished, self.message = k, False, f"re-recording {step_id}"
                return self.state()
        self.message = f"no step {step_id!r} in this session"
        return self.state()

    def abort(self):
        log(f"teach: session {self.name} aborted at step {self.i + 1}/{len(self.steps)}")
        self.finished = True; self.message = "aborted"
        return self.state()

    def _advance(self):
        self.i += 1
        if self.i >= len(self.steps):
            extra = self._extra_steps()
            if extra:
                self.steps = self.steps + extra
                log(f"teach: {self.name} continues with {len(extra)} more step(s): {', '.join(s['id'] for s in extra)}")
                return self.state()
            self.i = len(self.steps) - 1; self.finished = True
            missing = [s["id"] for s in self.steps if s["id"] not in self.recorded and s.get("kind") != "note"]
            self.message = "session complete" + (f" — not recorded: {', '.join(missing)}" if missing else "")
            log(f"teach: session {self.name} finished ({len(self.recorded)}/{len(self.steps)} recorded)")
            hook = getattr(self.store, "on_finish", None)
            if hook is not None:
                try: hook(self.store.load(), self, self.ctrl)
                except Exception as e: log(f"teach: {self.name} on_finish failed: {e}")
        return self.state()

    def _extra_steps(self):
        """Steps the domain only knows it needs once the scripted ones are in.

        The chess session uses it to send the operator back to the squares the fitted model says it
        cannot reach: whether the arm really cannot get there is a question about the arm, and the
        answer is in the operator's hands, not in the model. Skipped steps are not asked for again.
        """
        hook = getattr(self.store, "teach_extra_steps", None)
        if hook is None: return []
        have = {s["id"] for s in self.steps} | set(self.skipped)
        try:
            return [s for s in (hook(self.store.load(), self.ctrl) or []) if s.get("id") not in have]
        except Exception as e:
            log(f"teach: {self.name} extra steps failed: {e}"); return []


def _at(cfg, dotted):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur: return None
        cur = cur[part]
    return cur


def _describe(kind, record):
    if kind == "note": return "acknowledged"
    if "pose" in record and len(record) > 1: record = record["pose"]
    if kind == "probe": return f"touch at commanded z={record['z_touch_cm']} cm"
    if kind == "grasp": return f"gripper closed at {record['close']}"
    return ", ".join(f"{j.split('_')[-1]} {v}" for j, v in record.items())


# ------------------------------------------------------------------ controller-facing entry points
def start(ctrl, name):
    if ctrl.routine_thread is not None and ctrl.routine_thread.is_alive():
        log("teach: a routine is running (ABORT first)"); return None
    ctrl.teach = TeachSession(ctrl, load_session(name))
    return ctrl.teach


def reach(ctrl, store_name="chess"):
    """A session made only of the steps the domain still wants — for chess, the out-of-reach squares.

    The same steps a full session ends with, on their own, so a board taught before this existed can be
    revisited without re-teaching the corners.
    """
    if ctrl.routine_thread is not None and ctrl.routine_thread.is_alive():
        log("teach: a routine is running (ABORT first)"); return None
    mod = _store(store_name)
    hook = getattr(mod, "teach_extra_steps", None)
    steps = (hook(mod.load(), ctrl) or []) if hook is not None else []
    if not steps:
        log(f"teach: {store_name} has nothing out of reach — no session started"); return None
    ctrl.teach = TeachSession(ctrl, {"name": f"{store_name}_reach", "label": "Squares the model cannot reach",
                                     "store": store_name, "steps": steps})
    return ctrl.teach


def command(ctrl, action, **kw):
    """Dispatch a ``teach_*`` action. Returns the session state, or None when there is no session."""
    if action == "teach_start":
        s = start(ctrl, kw.get("session") or kw.get("name"))
        return s.state() if s else None
    if action == "teach_reach":
        s = reach(ctrl, kw.get("store", "chess"))
        return s.state() if s else None
    if action == "teach_free":
        ctrl.request({"action": "release", "src": "teach"}); return _state(ctrl)
    if action == "teach_engage":
        ctrl.request({"action": "engage", "src": "teach"}); return _state(ctrl)
    s = ctrl.teach
    if s is None:
        log(f"teach: {action} ignored, no session running"); return None
    if action == "teach_done": return s.done()
    if action == "teach_skip": return s.skip()
    if action == "teach_back": return s.back()
    if action == "teach_fix": return s.fix(kw.get("step") or kw.get("id"))
    if action == "teach_abort":
        out = s.abort(); ctrl.teach = None; return out
    log(f"teach: unknown action {action!r}")
    return s.state()


def _state(ctrl):
    return ctrl.teach.state() if ctrl.teach else None
