# Labs: the template for every new experiment

A **lab** is a named experiment space on the arm: a page, a config file, a set of actions and an API.
`/lab` is the template itself. Every new idea starts as a new lab built on it, never as a new copy of
`/paint` or `/chess`.

```
make lab NAME=pour TITLE="Pouring lab" DESC="pour 50 ml from cup A into cup B"
make run                       # restart the bridge
open http://localhost:8765/pour
```

That's it: the new lab already has everything below, plus one example action and one example tab to
replace with your own.

---

## 1. What every lab gets for free

| Feature | Where | Notes |
|---|---|---|
| Guards (step cap, soft limits, table guard, load/stall, ESTOP) | control loop | every move is a request; a lab **cannot** bypass them |
| STOP / Resume / Hold / Torque on / Free-drive | top bar | `Esc` = STOP; Free-drive always asks first, Cancel focused |
| Cameras, raw | Cameras panel, `/<lab>/cam/<cam>.mjpg` / `.jpg` | no vision; encode-once, new-frames-only, quality per browser |
| Joints & jog | Joints panel | speed / step, typed targets, range bars, load bars |
| Side view + top view | Side view panel | to scale, from the floor model the guard uses; goal ghost, points, rest, tool |
| Named points | Points & rest tab | record present pose, preview before Go, edit, rename, delete, copy |
| Lab rest | Points & rest tab | lab-only rest; the global `config/rest.json` is never touched |
| Reach envelope | Reach envelope tab | lab-only widening of soft limits, capped by the motor sweep, no register writes |
| Tool in the gripper | Tool tab | pen/brush/probe: guard on its tip and back, gripper locked (see §6) |
| Staged changes | Pending changes panel | nothing is saved until *Apply*; edit / discard per change |
| Video | Video tab | folder + raw/overlay in config, full path shown while recording |
| Raw state | Raw state tab | tree, filter, freeze, copy as JSON / path / goal command / Python |
| Journal | Journal tab, `/<lab>/journal` | every action that ran or was refused, from the page, a script or an agent |
| API for scripts, models, LLMs | API tab, `/<lab>/actions`, `/<lab>/observe` | see §5 |

## 2. Files

```
src/so101_bridge/
  lab/                 the template (generic — keep it that way, see §8)
    __init__.py        LABS registry, register(), discover(), the /lab template itself
    core.py            Lab class: actions, state keys, command(), confirm, journal
    actions.py         BUILTIN actions every lab starts with
    state.py           /state and /observe (side view, guards, points, tool…)
    store.py           config/<lab>.json + staged changes (var/lab/<lab>.pending.json)
    staging.py         pure functions behind the staging
    motion.py          jog / goto / rest helpers, the per-move `allow` (envelope)
    cameras.py         raw frame transport (no vision)
    journal.py         var/lab/<lab>.journal.jsonl
    http.py            every /<lab>/… route and /static/…
  labs/                YOUR labs, one module each — imported at start-up (discover())
  tool.py              tool geometry, tool guard, gripper lock (used by the control loop)
  web/lab.html         the shared page every lab uses
  web/lab/*.js|css     shared front-end modules (one panel per file)
  web/<lab>/panels.js  a lab's own tabs (its `ui` module)
scripts/new_lab.py     the scaffold behind `make lab`
config/<lab>.json      a lab's saved config (tracked)
var/lab/               pending changes + journal (runtime, ignored)
```

## 3. Anatomy of a lab

```python
# src/so101_bridge/labs/pour.py
from so101_bridge.lab import BUILTIN, Lab, motion, register

LAB = register(Lab(
    "pour", "Pouring lab",                      # URL /pour, config/pour.json
    actions=BUILTIN,                            # start from every template action
    ui="panels.js",                             # web/pour/panels.js -> extra tabs on the shared page
    description="pour 50 ml from cup A into cup B",
    defaults={"pour": {"tilt_deg": 70, "hold_s": 2.0}},   # config/pour.json starts from this
))

@LAB.action("pour", "Pour", "Tilt the cup over B and hold.", {"ml": "number", "speed": "number?"}, moves=True)
def pour(lab, ctrl, args):
    cfg = lab.cfg()                             # saved + staged: a staged value is used right away
    ...
    motion.run_move(ctrl, "pour", run)          # long moves run in the routine slot: STOP/HOLD/ABORT stop them
    return "pouring"                            # -> toast + journal

@LAB.state_key("pour")
def pour_state(lab, ctrl, st):                  # added to /pour/state on every poll — keep it small
    return {...}
```

```js
// src/so101_bridge/web/pour/panels.js
import { h, renderIf, fix } from '/static/lab/core.js';
export const panels = [{ id: 'pour', title: 'Pour', first: true, mount }];
function mount(el, ctx) {                       // ctx = {api, prefix, S, speed(), openTab(id), addTab(...)}
  el.append(h('button.go', { onclick: () => ctx.api.cmd('pour', { ml: 50 }) }, 'Pour 50 ml'));
  return { update(S) { /* S.pour, S.present, S.side, … */ } };
}
```

### Actions
- `@LAB.action(name, label, description, params, confirm=..., moves=..., group=...)`. The signature is
  `fn(lab, ctrl, args) -> str | dict | None`. Raise `ValueError` or `RuntimeError` with a sentence the
  operator can act on; the page shows it as a toast and it goes to the journal.
- `params` are documentation and tool schema: `"number" | "string" | "pose" | "bool" | "list" | "any"`.
  Add `?` for optional params.
- `moves=True`: the action is refused while ESTOP is set. Use it for anything that can move the arm.
- `confirm="question"` or `confirm=fn(lab, ctrl, args) -> str`: the action is refused until it is repeated
  with `"confirm": "yes"`. The page shows a dialog with Cancel focused. Use it for torque off, deleting
  data, anything irreversible or surprising. A function that returns `""` means there is nothing to confirm.
- To override a built-in, register an action with the same name. To remove one: `LAB.actions.pop("name")`.

### Moving the arm (the only allowed ways)
| Need | Use |
|---|---|
| one relative step | `motion.jog(ctrl, joint, delta, speed, envelope)` |
| reach a pose from anywhere | `motion.goto_pose(ctrl, pose, speed, envelope, label)` (threaded, interruptible) |
| several steps in a row | `motion.run_move(ctrl, label, fn)`; inside: `ctrl.goto_far(pose, speed, allow=motion.allow_for(env, pose))`, `ctrl.path(points, speed, allow=…)`, `ctrl.gripper_to(v)`; check `ctrl.routine_abort` between steps |
| the lab rest | `motion.go_rest(ctrl, rest, speed, envelope)` |
| a full autonomous routine | `@routine(...)` in routines.py style, started with the `routine` action |

Inside a move, every target is clipped by the loop (step cap 12°, soft limits or the per-move `allow`,
table guard, tool guard). `goto_far` already splits big moves into ≤10° steps.

### Config and staging
- `lab.cfg()` is the effective config (saved + staged). `lab.store.saved()` is what is on disk.
- Write only through staging: `lab.store.stage("pour.tilt_deg", 65, label="tilt")`, or the generic
  `config_set` action (`{"path": "pour.tilt_deg", "value": 65}`). The operator sees it under Pending changes
  and applies it.
- Keep a lab's own settings under one key named after the lab (`"pour": {...}`). The template's keys are
  `points`, `rest`, `envelope`, `motion`, `video`, `cameras` and `tool`.

### Panels (front end)
- Shared helpers come from `/static/lab/core.js`:
  - `h()` for elements, `renderIf()` to rebuild only when the data changed, `fix()` for numbers;
  - `dialog()` / `confirmBox()` for dialogs, `copy()`, `toast()`;
  - `pref` for per-browser settings only.
- A panel is `mount(el, ctx)`, which returns `{update(S)}`. `update` runs on every poll (~400 ms), so keep
  it cheap. Don't rebuild an input the user is typing in: `renderIf` already skips that case.
- Need a different layout? Set `page="pour.html"` and copy `web/lab.html`. Keep the `data-panel` names
  you reuse. Prefer the `ui` tabs: copied pages drift.

## 4. Cameras for your own code
- Python: `from so101_bridge.lab.cameras import FRAMES`. Then:
  - `FRAMES.raw(ctrl, "top")` returns `(rgb, t)`, or `None` when the frame is stale;
  - `FRAMES.jpeg(ctrl, cam, width, quality)` returns an encoded frame, shared and cached;
  - `FRAMES.grab_b64(ctrl, cam)` returns base64 for a model.
- HTTP: `/<lab>/cam/<cam>.jpg?w=1280&q=85` for one frame, `.mjpg` for a stream.
- The template never analyses pixels. Detection, overlays and calibration belong to *your* lab, or to a
  model that consumes the frames.
- Which device is which camera: `settings.CAMS` names each one as `"model#n"`, the n-th camera of that
  model in OpenCV order (OpenCV sorts devices by uniqueID): `{"top": "Innomaker#1", "wrist": "Innomaker#0"}`.
  Other cameras (laptop, OBS) are never opened, and a missing camera shows as missing. Two cameras never
  share a device: one that is already taken is skipped. A camera coming back from a USB drop returns to its
  own port first.
  - The two Innomakers share one USB serial, so only the port tells them apart. After a (re)connect,
    `camera_identity` watches which view changes while the arm moves. The camera named by
    `settings.CAM_ON_ARM` ("wrist") must be that one; otherwise the two are swapped and saved to
    `config/settings.json`. Any USB ports work. It needs a few seconds of arm motion, and an unclear result
    changes nothing.
  - If the check cannot decide, or only one of the two is connected, the camera panel asks you to pick by hand.
  - **Assign…** in the camera panel (actions `cameras_devices` / `cameras_assign`) chooses the device for each
    camera at any time, any device included. Each option shows the device's index, name and USB port, and
    each tile shows the device it uses. A checkbox turns the automatic check on or off (`CAM_AUTO_ID`).
    **⇄ Swap** (`cameras_swap`) flips the two. A choice made by hand is kept as you set it.
  - To list the connected devices, run `uv run python -m so101_bridge.camera_ids`.

## 5. Hooking a model or an LLM
- **Tools**: build them from `GET /<lab>/actions`. Each entry has `name`, `description`, `params`, `moves`
  and `confirm`.
- **Call**: `POST /<lab>/cmd {"action": ..., ...}` returns `{ok, error?, message?, needs_confirm?}`.
  - On `needs_confirm`, the agent must ask a human and then repeat the call with `"confirm": "yes"`. Never
    auto-confirm.
  - Torque release always needs the user's explicit OK.
- **Observe**: `GET /<lab>/observe` returns pose, tip (tool tip when a tool is mounted), table clearance,
  points, rest, limits, cameras and the last 10 journal entries. Add `?images=1` to get one JPEG per camera
  as base64.
- Everything an agent does is journaled and goes through the same guards and staging as the page.

## 6. Tool in the gripper (optional)
1. Define the tool in the Tool tab: length, the part sticking out past the fingertips, the tip's own
   margin (≥ −0.5 cm; ≤ 0 lets it touch), and the grip value. Apply.
2. Mount it (with a confirm). From then on the **control loop**, on every page:
   - watches the tool tip (its own margin) and its back end (0.8 cm);
   - freezes 0.8 cm below either margin;
   - locks the gripper.
3. Unmount before taking the tool out. The mounted tool lives in `var/tool.json`, so it survives restarts.

The tool is modelled along the gripper axis only. A tilt or an off-centre offset would be a new field in
`tool.py`, which then benefits every lab.

## 7. Tests for a lab
- Copy the `FakeCtrl` pattern from `tests/test_lab.py`. No hardware is needed, and `conftest.py` already
  redirects every registered lab's files to `tmp_path`.
- Test your actions' contracts: what gets staged, what gets requested (`ctrl.requests`), what is refused.
  Don't test movement, timings or the hardware.

## 8. Rules: keep the template agnostic
- `lab/`, `web/lab/`, `web/lab.html` and `tool.py` stay **generic**. No paint, chess or any experiment's
  vocabulary goes in there. Experiment code lives in `labs/<name>.py` and `web/<name>/`.
- A feature belongs in the template only when **every** lab benefits and it needs no experiment-specific
  knowledge. Examples: a new transport, a guard, a better panel, a journal. When in doubt, build it in your
  lab first and promote it later.
- A promoted feature becomes a `BUILTIN` action, a field in `state.build`, a shared panel, and a test in
  `tests/test_lab*.py`. Then document it in §1.
- Safety rules, never broken by a lab:
  - every move goes through `ctrl.request` / the motion helpers;
  - never `src="floor_ok"`;
  - never write motor registers, `config/limits.json`, `config/rest.json` or `config/floor_points.json`;
  - reach further only through the envelope / `allow`;
  - anything that releases torque or deletes data is `confirm`-gated.
- Keep files under ~300 lines and split them by panel or by concern.
