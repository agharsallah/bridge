# SO-101 control bridge

A Python daemon that owns an SO-101 follower arm and its two cameras. Everything else — an operator at
the dashboard, an AI agent writing JSON files, the built-in autonomous pick-and-place — sends *goals*
to it. The daemon enforces every safety rule itself, so a bad command becomes a slow, limited or
refused move, never a jerk and never a push into the table.

```bash
uv sync                 # install (lerobot comes from ../lerobot)
uv run so101-bridge     # start the daemon; dashboard on http://localhost:8765
```

Stop the arm at any time: the dashboard **STOP** button, `scripts/STOP.command` (double-click in
Finder), or `touch ESTOP` in any terminal. Clear it with **RESUME** or `scripts/RESUME.command`.

**Read [`docs/operations.md`](docs/operations.md) before touching the arm** — it is the operator and
agent manual: startup, the command interface, the state format, what every log line means, and what
to do when something goes wrong.

## Layout

| Path | What it holds |
| --- | --- |
| `src/so101_bridge/` | the package (see below) |
| `config/` | what you may edit: `limits.json`, `rest.json`, `floor_points.json`, `waypoints.json`, and optional overlays `settings.json` (any constant in `settings.py`, see `settings.example.json`) and `poses.json` (taught paths, see `poses.example.json`) |
| `var/` | runtime only, never committed: `bridge.log`, `state.json`, `cmd/`, `done/`, `recordings/`, camera snapshots |
| `scripts/` | double-clickable `STOP` / `RESUME` / `RELEASE` for the operator |
| `docs/` | operations manual, learnings, machine-readable `metadata.json`, decision log |
| `tests/` | unit tests for the parts that need no hardware |
| `ESTOP` | presence of this file freezes the arm; kept at the root so it is one short `touch` away |

## Modules

| Module | Responsibility |
| --- | --- |
| `app.py` | entry point and the 30 Hz control loop — the only code that talks to the motor bus |
| `hardware.py` | connect, motor gains, soft-limit derivation |
| `controller.py` | shared state, command queue, recording, waypoints, motion helpers |
| `routines.py` | registry of named routines; a routine is `run(ctrl)` that only requests goals and reports `ctrl.phase(...)` |
| `autopick.py` | the built-in `pick_place` routine (registered via `@routine`) |
| `floor.py` | fitted floor model: predicted fingertip height, the guard that protects the table |
| `vision.py` | brick detection and the dashboard overlay |
| `dashboard.py` + `web/dashboard.html` | local HTTP dashboard (status chips, camera streams, to-scale side view of the floor model, joint tracks with load, tip-height and tracking-error charts, log tail) and the JSON/MJPEG interfaces |
| `settings.py` | hardware identity and tuning constants (defaults); override per deployment in `config/settings.json` or `$SO101_SETTINGS` |
| `paths.py` | where config and runtime files live |
| `poses.py`, `util.py` | proven joint poses; logging and atomic writes |

## Dashboard overview

Header chips (mode, torque, routine, predicted tip height, loop rate, floor model, state age) and the
control bar; <kbd>Esc</kbd> triggers STOP from anywhere on the page. Panels: camera streams with the
detection overlay; to-scale side view of the arm as the floor model sees it; joint tracks (present /
goal / command, load vs guard limit, jog buttons and sliders); tip-height and tracking-error charts;
**Routine** (phase tracker with timings, preflight checklist, approach-convergence gauges);
**Guard events** (what the safety layer did, newest first); Teach (REC, waypoints, floor contacts); log tail.

`/state` carries everything the page shows — `progress`, `events`, `preflight`, `routines` — so an
agent can follow a run without the page. Start a routine with `/cmd?a=routine&name=pick_place` or a
`{"action": "routine", "name": "pick_place"}` command file.

## Development

```bash
uv run pytest        # unit tests (no hardware needed)
uv run ruff check .  # lint
```

Safety rules live in the control loop (`app.control_loop`) and in `Controller`; the routines can only
*request* goals, never bypass a guard. Keep it that way when adding behaviour.
