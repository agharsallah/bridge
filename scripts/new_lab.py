"""Scaffold a new lab on the template:  make lab NAME=<name> TITLE="<Title>"  (docs/lab.md).

Writes two files and touches nothing else:
    src/so101_bridge/labs/<name>.py       the lab: registration, an example action and state key
    src/so101_bridge/web/<name>/panels.js its tab(s) on the shared lab page
Restart the bridge and open http://localhost:8765/<name>.
"""

import argparse
import re
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "so101_bridge"
sys.path.insert(0, str(ROOT / "src"))

PY = Template('''"""$title: $description

Built on the lab template (docs/lab.md). Points, rest, reach envelope, tool, cameras, video, staged
changes, journal, the guards and the model/LLM API come with it — add only what this experiment needs.
Every move goes through the control loop: never send src="floor_ok", never write motor registers.
"""

from so101_bridge.lab import BUILTIN, Lab, motion, register

LAB = register(Lab(
    "$name", "$title", actions=BUILTIN, ui="panels.js", description="$description",
    defaults={"$name": {"dwell_s": 1.0}},        # config/$name.json starts from this; edit it via staged changes
))


@LAB.action("visit_points", "Visit points", "Go to every point (saved or staged) in name order, pausing at each.",
            {"speed": "number?"}, moves=True)
def visit_points(lab, ctrl, args):
    """Example of a multi-step move: it runs in the routine slot, so STOP / HOLD / ABORT interrupt it."""
    cfg = lab.cfg()
    points = [p for _, p in sorted((cfg.get("points") or {}).items())]
    if not points: raise ValueError("no points yet: record some in the Points tab")
    dwell, speed = float(cfg["$name"]["dwell_s"]), float(args.get("speed") or 4.0)
    keep_grip = getattr(ctrl, "tool", None) is None

    def run():
        for p in points:
            pose = motion.clean_pose(p["pose"], with_gripper=keep_grip)
            if not ctrl.goto_far(pose, speed, allow=motion.allow_for(cfg.get("envelope"), pose)): return False
            if ctrl.routine_abort.wait(dwell): return False
        return True
    motion.run_move(ctrl, "visit points", run)
    return f"visiting {len(points)} point(s)"


@LAB.state_key("$name")
def summary(lab, ctrl, st):
    """Whatever this lab's panel needs every poll — keep it small, it is sent ~2.5 times a second."""
    cfg = lab.cfg()
    return {"points": len(cfg.get("points") or {}), "dwell_s": cfg["$name"]["dwell_s"]}
''')

JS = Template('''// $title: this lab's tabs on the shared lab page. Each panel: {id, title, mount(el, ctx) -> {update(S)}}.
// ctx = {api, prefix, S (last state), speed(), openTab(id), addTab(...)}; helpers come from /static/lab/core.js.
import { h, renderIf, fix } from '/static/lab/core.js';

export const panels = [{ id: '$name', title: '$title', first: true, mount: mountMain }];

function mountMain(el, ctx) {
  const info = h('div.restcard');
  const dwell = h('input', { type: 'number', step: 0.5, min: 0, title: 'seconds at each point' });
  el.append(
    h('p.dim.small', '$description'),
    h('div.row', h('button.go', { onclick: () => ctx.api.cmd('visit_points', { speed: ctx.speed && ctx.speed() }) }, 'Visit points'),
      h('label.inline', 'dwell s', dwell),
      h('button.sm', { onclick: () => ctx.api.cmd('config_set', { path: '$name.dwell_s', value: +dwell.value }) }, 'Stage')),
    h('h4.sub', 'Status'), info);
  return {
    update(S) {
      const d = S.$name || {};
      if (document.activeElement !== dwell) dwell.value = d.dwell_s;
      renderIf(info, 'info', d, (x) => h('div.row', h('span.dim', 'points'), h('b.num', String(x.points)),
        h('span.dim', 'dwell'), h('b.num', fix(x.dwell_s, 1) + ' s')));
    },
  };
}
''')


def main(argv=None, pkg=PKG):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", help="lowercase id: the URL (/name) and config/name.json")
    ap.add_argument("--title", default="", help="shown in the page header")
    ap.add_argument("--description", default="", help="one line: what this experiment tries")
    a = ap.parse_args(argv)
    from so101_bridge.lab import RESERVED  # the same rule register() enforces
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", a.name) or a.name in RESERVED or a.name == "lab":
        sys.exit(f"bad name {a.name!r}: lowercase letters, digits and _, not a dashboard route")
    py, js = pkg / "labs" / f"{a.name}.py", pkg / "web" / a.name / "panels.js"
    for f in (py, js):
        if f.exists(): sys.exit(f"{f} exists already: nothing written")
    words = {"name": a.name, "title": (a.title or a.name.replace("_", " ").title()).replace('"', "'"),
             "description": (a.description or "what this experiment tries").replace('"', "'").replace("'", "’")}
    py.write_text(PY.substitute(words))
    js.parent.mkdir(parents=True, exist_ok=True); js.write_text(JS.substitute(words))
    print(f"wrote {py}\nwrote {js}\n"
          f"restart the bridge (make run) and open http://localhost:8765/{a.name}")
    return py, js


if __name__ == "__main__":
    main()
