"""Jenga turn: play one brick on its own — approach, line up by camera, push, pull the stick out, record.

This is the 2026-11-12 sessions turned into a routine (docs/jenga.md has the why behind every step):
get the stick level in open space, swing in front of the tower, stop short of the face, judge the height
from the wrist camera (never from the model alone), push in one slow stroke, lower the tip a little and
come back out. It stops rather than guesses: if the camera cannot find the rows, nothing is pushed.
"""

import json
import time

from so101_bridge.lab import motion
from so101_bridge.lab.cameras import FRAMES
from so101_bridge.lab.state import tip as tip_of
from so101_bridge.labs import jenga_tower as tower_mod
from so101_bridge.labs import jenga_vision
from so101_bridge.labs.balloon import ik
from so101_bridge.labs.jenga import LAB, LIVE, held, jcfg, line_to, pitch_of, runs_dir, shifted
from so101_bridge.settings import ARM
from so101_bridge.util import log

STAGE_R, STAGE_Z = 21.0, 17.0       # the staging pose (goal tip reach and height, stick level): reachable from rest, clear of the table
CLEAR_Z, AWAY_DEG = 5.0, 15.0       # a tip lower than this is "at rest"; a pan this far from the tower is clear of it


def tower_file(lab):
    return lab.journal_file.parent / "jenga_tower.json"


def reading(ctrl):
    """(present pose, present tool-tip {r_cm, z_cm, pan}) — what the arm reports right now, sag included."""
    st, _ = ctrl.snapshot()
    pose = {j: float(v) for j, v in st["present"].items() if j in ARM}
    return pose, tip_of(ctrl.floor, pose, ctrl.tool)


def look(ctrl, c):
    """Tip against the rows, with the offset always given against the nearest *end* row. None if the rows cannot be read."""
    frame, _ = FRAMES.raw(ctrl, "wrist")
    m = jenga_vision.measure(frame, c.get("vision")) if frame is not None else None
    if m is None: return None
    off = m["offset_cm"] if m["end_row"] else m["offset_cm"] + (c["row_cm"] if m["offset_cm"] < 0 else -c["row_cm"])
    return {**m, "to_end_row_cm": round(off, 2)}


def make_plan(lab, ctrl, args):
    c, t = jcfg(lab), tower_mod.load(tower_file(lab))
    rows = tower_mod.candidates(t, c)
    row = int(args.get("row") or (rows[0] if rows else 0))
    why = tower_mod.playable(t, c, row) if row else "no row left that can be played: reset the tower"
    if why: raise ValueError(why)
    depth = float(args.get("depth_cm") or c["turn_depth_cm"])
    if not 0 < depth <= c["turn_depth_max_cm"]: raise ValueError(f"depth must be in (0, {c['turn_depth_max_cm']:g}] cm")
    return {"row": row, "col": 1, "depth_cm": depth, "reading_cm": round(tower_mod.row_reading(c, row), 2), "pan_deg": c["pan_deg"],
            "stop_r_cm": c["face_r_cm"] - c["standoff_cm"], "stroke_cm": round(c["standoff_cm"] + depth, 2)}


class Turn:
    def __init__(self, lab, ctrl, plan):
        self.lab, self.ctrl, self.plan, self.c = lab, ctrl, plan, jcfg(lab)
        self.t0, self.stamp, self.log, self.last_dir = time.time(), time.strftime("%Y%m%d_%H%M%S"), [], 0

    def step(self, what, **more):
        LIVE["phase"] = what
        self.log.append({"t": round(time.time() - self.t0, 2), "step": what, "tip": reading(self.ctrl)[1], **more})

    def path(self, pts, speed):
        ok = self.ctrl.path(pts, speed)
        if ok: time.sleep(0.4)                              # "reached" is within 3 degrees: let it finish
        return ok and not self.ctrl.routine_abort.is_set()

    def move(self, dr, dz, speed=2.0):
        """Tip straight by (dr, dz); a change of vertical direction gets the slack it would otherwise lose."""
        if dz:
            d = 1 if dz > 0 else -1
            if self.last_dir and d != self.last_dir: dz += d * self.c["slack_cm"]
            self.last_dir = d
        st, pose = held(self.ctrl)
        return self.path(shifted(self.ctrl, st, pose, dr, dz), speed)

    def approach(self):
        """From rest or from in front of the tower to level, 0.8 cm before the stop point, at the row's height."""
        c, ctrl, plan = self.c, self.ctrl, self.plan
        pose, at = reading(ctrl)
        goal_z = plan["reading_cm"] + c["sag_cm"]
        if abs(pose["shoulder_pan"] - plan["pan_deg"]) >= AWAY_DEG:
            self.step("stage")
            if at["z_cm"] < CLEAR_Z and not ctrl.goto_far({"wrist_flex": pose["wrist_flex"] - 24.0}, 5.0): return False      # tip up first: see docs
            stage = ik(ctrl.floor, ctrl.tool, STAGE_R, STAGE_Z, 90.0)
            if stage is None or not ctrl.goto_far(stage, 6.0) or not ctrl.goto_far({"shoulder_pan": plan["pan_deg"]}, 6.0): return False
        elif at["r_cm"] > c["face_r_cm"] - 0.3 or abs(pitch_of(ctrl.floor, pose) - 90.0) > 20.0:
            raise RuntimeError("the stick is at the tower but not clear of it: back it out with nudges (or go to rest) and play again")
        self.step("place")
        st, pose = held(ctrl)
        return self.path(line_to(ctrl, st, pose, plan["stop_r_cm"] - 0.8, goal_z, 90.0), 4.0)

    def align(self):
        """Bring the tip to the middle of the row by camera. False (and no push) when the rows cannot be read or it will not settle."""
        c, plan, seen = self.c, self.plan, None
        for _ in range(7):
            _, at = reading(self.ctrl)
            by_model = plan["reading_cm"] - at["z_cm"]
            seen = look(self.ctrl, c)
            self.step("align", seen=seen, by_model=round(by_model, 2))
            if abs(by_model) > c["row_cm"] * 1.1: dz = by_model                 # a whole row off: the model is good enough for that
            elif seen is None: return False
            elif abs(seen["to_end_row_cm"]) <= c["align_tol_cm"]: return True
            else: dz = seen["to_end_row_cm"]                                       # + = tip below the middle -> up
            if not self.move(0.0, max(-2.0, min(2.0, dz))): return False
        return False

    def play(self):
        c, ctrl, plan = self.c, self.ctrl, self.plan
        v, own_video = self.lab.cfg().get("video") or {}, ctrl.video.status() is None
        if own_video: ctrl.video.start(f"jenga_turn_row{plan['row']}", directory=self.lab.video_dir(), raw=bool(v.get("raw")))
        result, LIVE["depth_cm"] = "stopped", plan["depth_cm"]
        try:
            if not self.approach(): result = "stopped on the way to the tower"
            elif not self.align(): result = "not pushed: could not line up on the row by camera"
            elif not self.move(0.8, 0.0, c["push_speed"]) or not self.align(): result = "not pushed: lost the row at the stop point"
            else:
                self.step("push")
                pushed = self.move(plan["stroke_cm"], 0.0, c["push_speed"])
                _, at = reading(ctrl)
                done = max(0.0, at["r_cm"] - c["face_r_cm"])
                if done > 0.5:
                    t = tower_mod.load(tower_file(self.lab))
                    tower_mod.save(tower_file(self.lab), tower_mod.mark(t, plan["row"], plan["col"], "out" if done >= 4.3 else "pushed", done))
                self.step("retract", pushed_cm=round(done, 1))
                back = self.move(-0.3, -c["pre_lower_cm"], 1.5) and self.move(-(at["r_cm"] - 0.3 - (plan["stop_r_cm"] - 1.0)), 0.0, 2.5)
                result = ("pushed" if pushed else "push stopped early") + ("" if back else ", stick not fully out")
        except (ValueError, RuntimeError) as e:
            result = f"stopped: {e}"
        finally:
            LIVE["phase"] = result
            video = ctrl.video.stop() if own_video else (ctrl.video.status() or {}).get("file")
            d = runs_dir(self.lab); d.mkdir(parents=True, exist_ok=True)
            f = d / f"{self.stamp}_turn_row{plan['row']}.json"
            f.write_text(json.dumps({"file": f.name, "name": f"turn row {plan['row']}", "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.t0)),
                                     "result": result, "duration_s": round(time.time() - self.t0, 2), "video": video, "plan": plan,
                                     "tool": ctrl.tool, "events": list(ctrl.events)[-10:], "steps": self.log}))
            self.lab.note("turn", {"file": f.name}, {"ok": result == "pushed", "message": result})
            log(f"JENGA turn {f.name}: {result} (video {video})")
        return result == "pushed"


@LAB.action("turn", "Play a turn", "Push the middle brick of a row out on its own: approach, line up by camera, push, pull the stick out. "
            "Without `row` it takes the highest row that can still be played.",
            {"row": "number?", "depth_cm": "number?", "dry": "bool?"},
            confirm=lambda lab, ctrl, a: "" if a.get("dry") else "Play a turn now? The arm goes to the tower and pushes a brick by itself.", moves=True)
def turn(lab, ctrl, args):
    plan = make_plan(lab, ctrl, args)
    if args.get("dry"): return {"message": f"plan: row {plan['row']}, middle brick, {plan['depth_cm']:g} cm", "plan": plan}
    if not ctrl.tool: raise ValueError("no tool mounted: mount the stick first (Tool tab)")
    held(ctrl)
    motion.run_move(ctrl, f"jenga turn row {plan['row']}", Turn(lab, ctrl, plan).play)
    return {"message": f"playing row {plan['row']}", "plan": plan}


@LAB.action("see", "Check the height", "What the wrist camera says about the stick tip against the rows (no move).")
def see(lab, ctrl, args):
    seen = look(ctrl, jcfg(lab))
    if seen is None: return {"message": "cannot read the rows: get the tip within ~1 cm of an untouched part of the face", "seen": None}
    return {"message": f"tip {abs(seen['to_end_row_cm']):g} cm {'below' if seen['to_end_row_cm'] > 0 else 'above'} the middle of the nearest end row", "seen": seen}


@LAB.action("tower_reset", "Reset the tower", "The tower was rebuilt: every brick is back in place.", {"rows": "number?"},
            confirm="Forget which bricks were pushed or taken out?")
def tower_reset(lab, ctrl, args):
    t = tower_mod.save(tower_file(lab), tower_mod.new(args.get("rows") or tower_mod.load(tower_file(lab))["rows"]))
    return f"tower: {t['rows']} rows, all bricks in"


@LAB.action("tower_mark", "Mark a brick", "Record that a brick is in place, pushed part-way or out of the tower.",
            {"row": "number", "col": "number", "state": "string", "depth_cm": "number?"})
def tower_mark(lab, ctrl, args):
    t = tower_mod.mark(tower_mod.load(tower_file(lab)), args["row"], args["col"], str(args["state"]), args.get("depth_cm") or 0.0)
    tower_mod.save(tower_file(lab), t); return f"row {int(args['row'])}, brick {int(args['col'])}: {args['state']}"


@LAB.state_key("tower")
def tower_state(lab, ctrl, st):
    """The tower for the 3-D view, the rows that can still be played, and where the stick tip is against the face."""
    c, t = jcfg(lab), tower_mod.load(tower_file(lab))
    at = tip_of(ctrl.floor, st.get("present"), ctrl.tool) if st.get("present") else None
    stick = {"gap_cm": round(c["face_r_cm"] - at["r_cm"], 1), "z_cm": round(at["z_cm"] - c["tip_z_offset_cm"], 1),
             "at_tower": abs(at["pan"] - c["pan_deg"]) < AWAY_DEG} if at else None
    return {**t, "playable": tower_mod.candidates(t, c), "end_rows": c["end_rows"], "row_cm": c["row_cm"], "stick": stick,
            "depth_cm": c["turn_depth_cm"]}
