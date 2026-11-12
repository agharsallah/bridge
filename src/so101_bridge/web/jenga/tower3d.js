// A small 3-D view of the tower: every brick a shaded box, drag to turn it, click a brick to pick it.
// Plain SVG and a painter's sort — no library. World axes in cm: x to the arm's right, y away from the arm, z up.
const NS = 'http://www.w3.org/2000/svg';
const L = 7.5, W = 2.5, PITCH = 0.42;                    // brick length and width as drawn (three across make a square), view tilt in rad
const FILL = { in: ['#e9d9b8', '#cdb88e', '#b39b6f'], pushed: ['#f2b84b', '#d99a2b', '#b87d18'], pick: ['#7fd1ff', '#4aa8dc', '#2f86b8'] };

const el = (name, attrs) => { const e = document.createElementNS(NS, name); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };

// One brick as a box {x, y, z, dx, dy, dz, row, col, state}. End rows point at the arm; the rows between lie across.
export function bricks(tower) {
  const out = [], h = tower.row_cm || 1.5, odd = (tower.end_rows || 'odd') === 'odd';
  for (let row = 1; row <= tower.rows; row++) {
    const ends = (row % 2 === 1) === odd;
    for (let col = 0; col < 3; col++) {
      const b = (tower.bricks || {})[`${row}-${col}`] || { state: 'in' };
      const push = b.state === 'pushed' ? (b.depth_cm || 0) : 0;
      out.push(ends
        ? { x: (col - 1.5) * W, y: push, z: (row - 1) * h, dx: W, dy: L, dz: h, row, col, state: b.state }
        : { x: -L / 2, y: col * W, z: (row - 1) * h, dx: L, dy: W, dz: h, row, col, state: b.state });
    }
  }
  return out;
}

export function mountTower(host, onPick) {
  const svg = el('svg', { viewBox: '-17 -33 34 42', style: 'width:100%;max-width:340px;height:auto;touch-action:none;cursor:grab' });
  host.append(svg);
  let yaw = -0.6, tower = null, picked = null, drag = null;

  const project = (x, y, z) => {                         // turn about the tower's own axis, then tilt towards the viewer
    const c = Math.cos(yaw), s = Math.sin(yaw), yy = y - L / 2;
    const xr = x * c - yy * s, yr = x * s + yy * c;
    return [xr, yr * Math.sin(PITCH) - z * Math.cos(PITCH), yr];
  };
  const face = (pts, fill, b) => {
    const p = el('polygon', { points: pts.map((q) => `${q[0].toFixed(2)},${q[1].toFixed(2)}`).join(' '), fill, stroke: '#3a3226', 'stroke-width': 0.08 });
    if (b.state === 'out') { p.setAttribute('fill', 'none'); p.setAttribute('stroke-dasharray', '0.4 0.4'); p.setAttribute('stroke', '#8a7f6a'); }
    p.addEventListener('click', () => { picked = `${b.row}-${b.col}`; onPick && onPick(b); draw(); });
    svg.append(p);
  };

  function draw() {
    svg.replaceChildren();
    if (!tower) return;
    const c = Math.cos(yaw), s = Math.sin(yaw);
    const depth = (b) => (b.x + b.dx / 2) * s + (b.y + b.dy / 2 - L / 2) * c;
    const list = bricks(tower).sort((a, b) => a.z - b.z || depth(b) - depth(a));      // lower rows first, far bricks first
    for (const b of list) {
      const P = (i, j, k) => project(b.x + i * b.dx, b.y + j * b.dy, b.z + k * b.dz);
      const col = FILL[`${b.row}-${b.col}` === picked ? 'pick' : b.state === 'pushed' ? 'pushed' : 'in'];
      face(s > 0 ? [P(0, 0, 0), P(0, 1, 0), P(0, 1, 1), P(0, 0, 1)] : [P(1, 0, 0), P(1, 1, 0), P(1, 1, 1), P(1, 0, 1)], col[2], b);   // the side towards the viewer
      face(c > 0 ? [P(0, 0, 0), P(1, 0, 0), P(1, 0, 1), P(0, 0, 1)] : [P(0, 1, 0), P(1, 1, 0), P(1, 1, 1), P(0, 1, 1)], col[1], b);   // front or back
      face([P(0, 0, 1), P(1, 0, 1), P(1, 1, 1), P(0, 1, 1)], col[0], b);                                                            // top
    }
    const k = tower.stick;
    if (k && k.at_tower) {                                 // the stick: 10 cm of it, tip `gap` before the face (negative: inside the tower)
      const a = project(0, -k.gap_cm, k.z_cm), b = project(0, -k.gap_cm - 10, k.z_cm);
      svg.append(el('line', { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: '#15171a', 'stroke-width': 0.8, 'stroke-linecap': 'round' }),
        el('circle', { cx: a[0], cy: a[1], r: 0.5, fill: '#ff5a4d' }));
    }
  }

  svg.addEventListener('pointerdown', (e) => { drag = { x: e.clientX, yaw }; svg.setPointerCapture(e.pointerId); });
  svg.addEventListener('pointermove', (e) => { if (drag) { yaw = drag.yaw + (e.clientX - drag.x) * 0.012; draw(); } });
  svg.addEventListener('pointerup', () => { drag = null; });
  return { update(t) { tower = t; draw(); }, picked: () => picked };
}
