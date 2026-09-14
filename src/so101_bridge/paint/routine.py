"""The 'paint' routine: execute a saved program step by step through the guarded control loop.

Select what to run before starting:  ctrl.paint_request = {"name": "<program>"}  (the dashboard does this),
then ctrl.start_routine("paint"). Progress for the paper canvas is in ctrl.progress["paint"].
"""

import time

from ..routines import routine
from ..util import log
from . import program as store

PHASES = ("preflight", "start", "painting", "finish", "done")
BRIDGE_STEP_DEG = 8.0      # densify the approach from wherever the arm is to a step's first point


def _bridge_from_present(ctrl, points):
    """Prepend a densified approach from the arm's present pose to the first point of a step.

    Programs are compiled without knowing where the arm will be when they start (rest, or wherever a
    previous run was interrupted). A far first point would be step-capped to 12 deg by the control loop and
    the arm would run the rest of the step from the wrong place — so walk there first, in small steps."""
    st, _ = ctrl.snapshot(); present = st.get("present") or {}
    first = points[0]
    joints = [j for j in first if j in present]
    if not joints: return points
    far = max(abs(first[j] - present[j]) for j in joints)
    if far <= BRIDGE_STEP_DEG: return points
    n = int(far // BRIDGE_STEP_DEG) + 1
    lead = [{j: round(present[j] + (first[j] - present[j]) * k / n, 3) for j in joints} for k in range(1, n)]
    return lead + list(points)


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
    steps = prog["steps"]
    from_stroke = req.get("from_stroke")
    if from_stroke is not None:                      # resume: start with the dip/rinse that precedes stroke N
        first = next((i for i, st in enumerate(steps) if st["op"] == "stroke" and st.get("index", 0) >= int(from_stroke)), None)
        if first is None: return fail(f"no stroke >= {from_stroke} in {name}")
        prev = max((i for i in range(first) if steps[i]["op"] == "stroke"), default=-1)
        steps = steps[prev + 1:]; log(f"PAINT: resuming {name} from stroke {from_stroke} (step {prev + 1})")
    n = len(steps)
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
        pts = _bridge_from_present(ctrl, st["points"])
        if not ctrl.path(pts, st.get("speed", 6.0)):
            return fail(f"move interrupted at step {i}/{n}: {st.get('label')} (guard, ESTOP or timeout)")
        if st["op"] == "stroke":
            done_strokes.append(st.get("index"))
            with ctrl.lock: ctrl.progress["paint"]["stroke_done"] = len(done_strokes)
    ctrl.phase("finish", "program complete")
    ctrl.routine_status = "done"; ctrl.phase("done"); log(f"PAINT: done ({name})")
