- Do not rewrite the motor register or hardware saved configurations.
- Do not over complicate things and do the least changes.
- Only write test that matters and avoid redundant checks.
- Do not add flacky tests with timouts and unreliable dependencies.
- Do not make assumptions about the initial state of the hardware.
- Do not write tests including hardware movements.
- You are dealing with hardware, an S101 arm that have limited range of motion and specific constraints.
- Always consider the safety of the hardware and avoid actions that could cause damage.
- If a script reaches +300 lines, consider breaking it into smaller, manageable, reusable parts.
- Never release the torque again without me giving you the OK

## Labs: every new experiment starts from the lab template
- New idea = new lab: `make lab NAME=<name> TITLE="..." DESC="..."`. This creates `src/so101_bridge/labs/<name>.py`
  and `src/so101_bridge/web/<name>/panels.js`, served at `/<name>`. Never start from /paint or /chess, and never copy them.
- Full guide: `docs/lab.md` (anatomy, actions, moving the arm, staging, panels, cameras, model/LLM hooks, tests). Read it first.
- Keep the template agnostic: `src/so101_bridge/lab/`, `web/lab/`, `web/lab.html` and `tool.py` hold only generic
  features that every lab benefits from. Experiment-specific code goes in `labs/<name>.py` and `web/<name>/`. When in
  doubt, put it in the lab and promote it later.
- Lab safety rules:
  - Move only through `ctrl.request` or the `lab.motion` helpers.
  - Never send `src="floor_ok"`.
  - Reach beyond the soft limits only through the lab envelope / `allow`. Never through motor registers or `config/limits.json`.
  - Never write `config/rest.json` or `config/floor_points.json` from a lab.
- Config edits go through staging (`lab.store.stage` / `config_set`), so the operator reviews and applies them. Do not
  write `config/<lab>.json` directly.
- Actions that release torque, delete data or surprise the operator must be `confirm`-gated. An agent never
  sends `"confirm": "yes"` by itself: it asks the user, and torque release still needs my explicit OK.
- A mounted tool (`var/tool.json`) changes the table guard and locks the gripper on every page. Check `/lab/state` → `tool`
  before moving.
- Tests for a lab use the `FakeCtrl` pattern from `tests/test_lab.py`. They never move hardware, and conftest already
  sends lab files to tmp.
