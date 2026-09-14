"""The 'paint' routine: execute a saved program step by step through the guarded control loop.

Select what to run before starting:  ctrl.paint_request = {"name": "<program>"}  (the dashboard does this),
then ctrl.start_routine("paint"). Progress for the paper canvas is in ctrl.progress["paint"].
"""

import time

from ..routines import routine
from ..util import log
from . import program as store

PHASES = ("preflight", "start", "painting", "finish", "done")


@routine("paint", label="PAINT — run a saved program",
         description="Replay a compiled painting program (dips, rinses, strokes) with all guards active.", phases=PHASES)
def run(ctrl):
    req = getattr(ctrl, "paint_request", None) or {}
    name = req.get("name")

    def fail(msg):
        log(f"PAINT FAILED: {msg}"); ctrl.routine_status = f"failed: {msg}"
        ctrl.phase("failed", msg); ctrl.request({"action": "hold", "src": "auto"})

    ctrl.routine_status = "running"; ctrl.phase("preflight", name or "no program selected")
    prog = store.load(name) if name else None
    if not prog:
        return fail(f"program {name!r} not found")
    steps = prog["steps"]; n = len(steps)
    strokes_total = prog["stats"].get("strokes", 0)
    done_strokes, color = [], None
    with ctrl.lock:
        ctrl.progress["paint"] = {"program": name, "dry_run": prog.get("dry_run", False), "step": 0, "steps": n,
                                  "stroke_done": 0, "strokes": strokes_total, "done_strokes": done_strokes, "color": None,
                                  "label": ""}
    ctrl.phase("start", f"{n} steps, {strokes_total} strokes{' (DRY RUN at hover height)' if prog.get('dry_run') else ''}")
    ctrl.phase("painting")
    for i, st in enumerate(steps):
        if ctrl.routine_abort.is_set():
            return fail(f"aborted at step {i}/{n}: {st.get('label')}")
        with ctrl.lock:
            ctrl.progress["paint"].update(step=i + 1, label=st.get("label", st["op"]))
        if st.get("color") and st["color"] != color:
            color = st["color"]
            with ctrl.lock: ctrl.progress["paint"]["color"] = color
            ctrl.phase("painting", f"colour {color}")
        if st["op"] == "dwell":
            t0 = time.time()
            while time.time() - t0 < float(st.get("seconds", 0)):
                if ctrl.routine_abort.is_set(): return fail("aborted during dwell")
                time.sleep(0.05)
            continue
        if not ctrl.path(st["points"], st.get("speed", 6.0)):
            return fail(f"move interrupted at step {i}/{n}: {st.get('label')} (guard, ESTOP or timeout)")
        if st["op"] == "stroke":
            done_strokes.append(st.get("index"))
            with ctrl.lock: ctrl.progress["paint"]["stroke_done"] = len(done_strokes)
    ctrl.phase("finish", "program complete")
    ctrl.routine_status = "done"; ctrl.phase("done"); log(f"PAINT: done ({name})")
