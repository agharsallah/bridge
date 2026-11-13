# Drawing lab: draw on paper with a pen

`/draw` — built on the lab template (`docs/lab.md`). A pen is taped to the fixed jaw, 8 cm past the
fingertips, and mounted as the tool `pen`. The arm draws on a sheet taped to the table in front of it.

## Start here

1. Sheet in front of the arm, covering at least **x −9…9 cm, y 26…38 cm** (cm from the base axis: x to the
   arm's right, y away from it). Pen mounted (Tool tab → `pen`; the lab's config starts with it defined).
2. `make run`, open `http://localhost:8765/draw`.
3. Pick a drawing (or **✎ Sketch your own** on the sheet, then **Use sketch**). The sheet shows the plan in blue.
4. **Measure the paper** once after the pen or the sheet moved: the pen touches the four corners of the area
   (it leaves four dots) and the heights are staged as `draw.touch`.
5. **▶ Draw**. It asks once, then films the run (`var/videos/lab/`), traces the tip in orange and saves the
   run (`var/lab/draw_runs/`, with a photo of the result). **↻ Draw again** repeats a run; change size /
   shift first to move or resize it.

From a script or an agent: `show` (choose / upload strokes, no move), `draw` (`dry: true` = plan only),
`calibrate`, `pen` (put the tip somewhere, up or down), `photo`. `draw` and `calibrate` need
`"confirm": "yes"`, which an agent only sends after the operator said go.

## How it works

- **Landing on the line**: see "Still open" for why; `land_cm` is the length of the run-in, `ramp_cm` the height it
  starts from.
- **Pen at 45°** (`pitch_deg` 135). A 5-joint arm cannot hold a pen upright over a whole sheet; at 45° the
  tip moves smoothly from 26 to 39 cm. Closer than that the arm is too folded and the lab refuses.
- **The paper's height is measured, not assumed.** The tool model only knows the gripper axis; the taped pen
  sits beside it and the arm sags with reach. `calibrate` lowers the tip in 0.2 cm steps until its reading
  stops lagging by its usual sag (`touch_err_cm`, two steps in a row); a plane through the touches gives the
  height everywhere. The pen then draws `press_cm` below it and travels `lift_cm` above it.
- **Everything is checked before the first move**: each point of the plan, pen down and pen up, against the
  soft limits and the table guard. A drawing that does not fit is refused with the point that fails.
- **From rest** the wrist lifts the tip first, then the arm goes above the first stroke, then pans.

## Pieces

- `labs/draw.py` — the lab, table geometry, the chosen drawing and its plan, `show`, `photo`, the panel state.
- `labs/draw_run.py` — `Pen` (straight tip moves, the approach, the touch), `Run`, `draw`, `calibrate`, `pen`.
- `labs/draw_shapes.py` — built-in drawings (`jumua`, `sailboat`, `robot`, `star`, `heart`, `house`, `spiral`, `frame`), fit, resample.
  `frame` is the test pattern: a square with its diagonals.
- `labs/draw_kufic.py` — Square Kufic: words as a unit-grid bitmap (strokes and gaps one unit), traced into closed
  outlines, right to left. `jumua` is جمعة مباركة.
- `web/draw/sheet.js` — the sheet widget (plan, trace, pen, touch points, sketch pad); `panels.js` — the tab.

## What the first session measured (2026-11-13)

- **Reach offset 6 cm** (`reach_offset_cm`): the real tip lands that much further from the base axis than the
  floor model says. Without it a 14 cm wide square came out 16–18.6 cm wide.
- **Sheet rotation** (`pan0_deg`, staged −10.8 that day): the sheet was turned 11° against the arm's straight
  ahead. Found from the four touch dots in a top-camera photo, mapped to cm through the sheet's corners (A4).
- **Paper height** in the arm's terms: 0.1 cm at the near corners, 0.9–0.95 at the far ones (it rises with
  reach because the arm sags). The tip reads 0.5 cm (near) to 1 cm (far) below what is asked; on the paper that
  gap closes over ~0.6 cm of descent. Joint loads were too noisy to use.
- Positive pan is to the arm's right (`pan_sign` 1). The top camera sees the whole sheet; the arm must swing
  aside (pan −45°) for a clear photo, the parked pose hides the near-left quarter.
- First robot face: square head, straight lines, 3 cm drawn as 3 cm, about 1.3 cm further away than planned.

## What the second drawing taught (sailboat, 2026-11-13)

- **The encoder touch (`calibrate`) reads low.** Play in the gears and the taped pen absorb more than a
  centimetre of pressing before the readings show it, so the heights taken from it were too low, worst at far
  reach: every far stroke dragged a 1–2 cm leg on landing and leaving, and the sun came out squashed.
  Use **`ramp`** instead: one line drawn while the tip comes down; where the ink starts is the paper.
- The paper's height follows the **reach** (sag), so `paper_z` is a curve through the touch points by distance
  from the base, not a plane.
- **Draw things as one line.** The first sailboat had 19 strokes (each sun ray and sail its own); the scene is
  now 6 strokes: the sun goes round its circle and out-and-back along each ray, the boat is hull, mast, flag
  and both sails without lifting, the sea is two rows joined at the end. Fewer landings, fewer faults.

## What the third drawing taught (six-stroke sailboat, 2026-11-13)

- `ramp` lines in the right margin gave 1.05 / 1.4 / 2.05 cm at 30 / 34.6 / 39.3 cm reach. With them the far
  half (sun, rays, sails, mountains) drew cleanly: no legs, round sun.
- A smooth curve through those three was **too high near the arm**: at 26–28 cm the pen hovered and the sea and
  the hull's bottom did not ink. Redrawn at 0.35 they inked, the lowest wave row (26.5 cm) only partly. Working
  values that day: 0.1 at 26.5, 0.45 at 28, 1.05 at 30, 1.4 at 34.6, 2.05 at 39.3 cm (staged as `draw.touch`).
  `paper_z` now joins the points with straight lines instead of fitting a curve.
- **Measure where you draw**: put a `ramp` at the nearest row too, not only in a side margin.
- A missing part can be redrawn in register: `show` the run's own strokes (cm) with the scale and shift that
  place them where they were (`sea_and_hull` run), the frame holds to a few millimetres.

## What the Square Kufic drawing taught (جمعة مباركة, 2026-11-13)

- Outlined letters on a 0.7 cm unit grid are readable: straight parallel lines 0.7 cm apart hold. The bottom line
  (27–32 cm reach) came out square; the **top line (34–38 cm) is squeezed front to back**, so the raised box of ع
  and the opening of ج closed up. Distances along the reach shrink at far reach: put fine detail near, or make the
  far rows taller.
- **0.7 cm squares (dots, holes) are too small**: they came out as 0.3–0.4 cm flags with a landing leg. Dots need
  1 cm or more, or a single short dash.
- One `ramp` under the text (later covered by the underline) gave 0.15 cm at 27.3 cm reach; `press_cm` 0.45
  inked every row.
- Seen on the real sheet: the top line is not as squeezed as the camera crop suggested; the ع box is low but
  the shapes hold. The camera's perspective exaggerates it — judge proportions from a straight-on photo.
- **Approach bug found**: from a low pose off to the side, going joint-by-joint to a far first stroke dipped the tip
  into the table guard and the run stalled before drawing (the guard held; nothing was hit hard). `ready` now goes
  to a high point near the arm first and then across in a straight line.

## Still open

- **Small squares** (the operator's photo of جمعة مباركة, 2026-11-13): the large outlines were clean, but every
  dot and hole had a 0.7–1 cm leg hanging from its start corner, as long as the square itself. The leg is the
  landing: the pen came down *beside* the line (first straight down, later along its own axis) and inked on
  the way. Since then the pen lands and leaves **on the line**: down over the first `land_cm` of the stroke
  while already moving along it, and for a closed shape up again over the same first stretch. Simulated and
  tested, **not yet drawn**: the next drawing must check the dots first.
- The legs were ~1 cm although the tip should only touch in the last 0.3 cm: the `ramp` reading is taken while
  the tip is still moving down and sideways, so it reads a little low. Treat ramp heights as a lower bound.
- A height test line ends with a small hook where the pen lifts; it showed through the underline. End test
  lines where the drawing will cover them completely.
- `calibrate` with the rewritten detection has not been run on the arm; the heights in use were read by hand
  from the first probes (`var/lab/draw_probes/`).
- Pen-up travel at 1.5 cm left a faint streak at far reach; 2.5 cm (now the default) did not.
