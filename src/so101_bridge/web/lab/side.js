// Side view (to scale, from the floor model the table guard uses) + a schematic top view (pan).
// Data: S.side = {fitted, arm:[[r,z] m], goal_arm, tip, goal_tip, markers:[{name,kind,status,r_cm,z_cm,pan}],
//                 margin_cm, grasp_cm, reach_cm, shoulder_z_cm, contacts, rms_mm}
import { fix } from './core.js';

const C = { tool: '#39d0d8', table: '#18202a', grid: '#1b2330', gridTxt: '#5b6778', guard: '#f85149', grasp: '#d29922', reach: '#334155',
  links: ['#3fb950', '#d29922', '#ff7b9c'], goal: '#58a6ff', point: '#58a6ff', rest: '#bc8cff', ink: '#e6edf3', dim: '#8b98a8' };

function setup(cv, fallbackH) {
  const dpr = window.devicePixelRatio || 1, w = cv.clientWidth || 300, hh = cv.clientHeight || fallbackH;
  cv.width = Math.round(w * dpr); cv.height = Math.round(hh * dpr);
  const g = cv.getContext('2d'); g.scale(dpr, dpr); g.font = '11px ui-monospace, Menlo, monospace'; g.lineCap = 'round'; g.lineJoin = 'round';
  return { g, w, h: hh };
}
// text with a dark halo so it stays readable over links, grid and bands
const text = (g, s, x, y, col, align = 'left') => { g.save(); g.textAlign = align; g.lineWidth = 3; g.strokeStyle = '#0b0f14'; g.strokeText(s, x, y); g.fillStyle = col; g.fillText(s, x, y); g.restore(); };
const empty = (g, w, h, msg) => { g.fillStyle = C.dim; g.textAlign = 'center'; g.fillText(msg, w / 2, h / 2); };

function marker(g, x, y, m, placed = []) {
  const col = m.kind === 'rest' ? C.rest : C.point, pend = ['new', 'modified', 'pending'].includes(m.status), del = m.status === 'deleted';
  g.save(); g.globalAlpha = del ? 0.35 : 1; g.lineWidth = 1.5; g.strokeStyle = pend ? C.grasp : col; g.fillStyle = col + '55';
  if (pend) g.setLineDash([3, 2]);
  g.beginPath();
  if (m.kind === 'rest') g.rect(x - 5, y - 5, 10, 10); else { g.moveTo(x, y - 6); g.lineTo(x + 6, y); g.lineTo(x, y + 6); g.lineTo(x - 6, y); g.closePath(); }
  g.fill(); g.stroke(); g.setLineDash([]); g.restore();
  let ly = y - 6;                                                      // stack labels that would overlap
  while (placed.some((p) => Math.abs(p.x - x) < 70 && Math.abs(p.y - ly) < 12)) ly -= 12;
  placed.push({ x, y: ly }); g.save(); g.globalAlpha = del ? 0.35 : 1; text(g, m.name, x + 8, ly, C.dim); g.restore();
}

export function drawSide(cv, sd, present, hover) {
  const { g, w, h } = setup(cv, 320);
  const hits = [];
  if (!sd?.fitted || !sd.arm) { empty(g, w, h, 'Floor model not fitted: mark at least 4 table contacts on the control dashboard'); return hits; }
  const cm = (p) => [p[0] * 100, p[1] * 100];
  const arm = sd.arm.map(cm), goal = sd.goal_arm ? sd.goal_arm.map(cm) : null;
  const reach = sd.reach_cm || 36, z0 = sd.shoulder_z_cm ?? arm[0][1];
  let rMin = -reach * 0.45, rMax = reach + 4, zMin = -5, zMax = z0 + reach + 2;
  const toolPts = sd.tool ? [sd.tool.back, sd.tool.front] : [];
  for (const [r, z] of [...arm, ...(goal || []), ...toolPts, ...sd.markers.map((m) => [m.r_cm, m.z_cm])]) {
    rMin = Math.min(rMin, r - 3); rMax = Math.max(rMax, r + 3); zMin = Math.min(zMin, z - 3); zMax = Math.max(zMax, z + 3);
  }
  const L = 30, B = 18, s = Math.min((w - L - 8) / (rMax - rMin), (h - B - 8) / (zMax - zMin));
  const ox = L + (w - L - 8 - (rMax - rMin) * s) / 2;
  const X = (r) => ox + (r - rMin) * s, Y = (z) => h - B - (z - zMin) * s;
  const R = (x) => (x - ox) / s + rMin, Z = (y) => (h - B - y) / s + zMin;

  g.lineWidth = 1;                                                    // grid, labelled every 10 cm
  for (let r = Math.ceil(rMin / 5) * 5; r <= rMax; r += 5) {
    g.strokeStyle = C.grid; g.beginPath(); g.moveTo(X(r), Y(zMax)); g.lineTo(X(r), Y(zMin)); g.stroke();
    if (r % 10 === 0) { g.fillStyle = C.gridTxt; g.textAlign = 'center'; g.fillText(r, X(r), h - 5); }
  }
  for (let z = Math.ceil(zMin / 5) * 5; z <= zMax; z += 5) {
    g.strokeStyle = C.grid; g.beginPath(); g.moveTo(X(rMin), Y(z)); g.lineTo(X(rMax), Y(z)); g.stroke();
    if (z % 10 === 0) { g.fillStyle = C.gridTxt; g.textAlign = 'right'; g.fillText(z, L - 4, Y(z) + 4); }
  }
  g.fillStyle = C.table; g.fillRect(0, Y(0), w, h - Y(0));            // table + guard band
  g.fillStyle = 'rgba(248,81,73,.16)'; g.fillRect(0, Y(sd.margin_cm), w, Y(0) - Y(sd.margin_cm));
  const line = (z, col, label, dash, lx, align, dy) => {         // labels spread out: the lines are mm apart
    g.strokeStyle = col; g.setLineDash(dash || []); g.beginPath(); g.moveTo(X(rMin), Y(z)); g.lineTo(X(rMax), Y(z)); g.stroke(); g.setLineDash([]);
    text(g, label, lx, Y(z) + dy, col, align);
  };
  line(0, '#5b6778', 'table', null, L + 4, 'left', 14);
  line(sd.margin_cm, C.guard, `table guard ${fix(sd.margin_cm, 1)} cm`, [5, 4], w - 6, 'right', -4);
  if (sd.grasp_cm) line(sd.grasp_cm, C.grasp, `grasp ${fix(sd.grasp_cm, 1)} cm`, [2, 4], w - 150, 'right', -4);

  g.save(); g.beginPath(); g.rect(0, 0, w, Y(0)); g.clip();           // reach circle, above the table only
  g.strokeStyle = C.reach; g.setLineDash([4, 5]); g.beginPath(); g.arc(X(0), Y(z0), reach * s, 0, Math.PI * 2); g.stroke(); g.setLineDash([]); g.restore();
  g.fillStyle = '#2b3442'; g.fillRect(X(0) - 2 * s, Y(z0), 4 * s, Y(0) - Y(z0));    // the base

  const placed = [];
  for (const m of sd.markers) { const x = X(m.r_cm), y = Y(m.z_cm); marker(g, x, y, m, placed); hits.push({ x, y, m }); }

  if (goal) {                                                         // where the arm is going
    g.strokeStyle = C.goal; g.globalAlpha = 0.7; g.lineWidth = 2; g.setLineDash([6, 4]);
    g.beginPath(); goal.forEach(([r, z], i) => (i ? g.lineTo(X(r), Y(z)) : g.moveTo(X(r), Y(z)))); g.stroke();
    g.setLineDash([]); g.beginPath(); g.arc(X(goal[3][0]), Y(goal[3][1]), 5, 0, 7); g.stroke(); g.globalAlpha = 1;
  }
  for (let i = 0; i < 3; i++) {                                       // the arm
    g.strokeStyle = C.links[i]; g.lineWidth = 7; g.beginPath(); g.moveTo(X(arm[i][0]), Y(arm[i][1])); g.lineTo(X(arm[i + 1][0]), Y(arm[i + 1][1])); g.stroke();
  }
  const T = sd.tool;                                                  // the tool along the gripper axis
  if (T) {
    const on = T.state === 'mounted';
    g.save(); g.strokeStyle = C.tool; g.globalAlpha = on ? 1 : 0.45; g.lineWidth = on ? 5 : 3; if (!on) g.setLineDash([5, 4]);
    g.beginPath(); g.moveTo(X(T.back[0]), Y(T.back[1])); g.lineTo(X(T.front[0]), Y(T.front[1])); g.stroke(); g.setLineDash([]);
    g.fillStyle = C.tool; g.beginPath(); g.arc(X(T.front[0]), Y(T.front[1]), 4, 0, 7); g.fill(); g.restore();
    text(g, on ? `${T.name} tip z ${fix(T.front[1], 1)}` : `${T.name} (not mounted)`, X(T.front[0]) + 8, Y(T.front[1]) + 14, C.tool);
    if (on && Math.abs(T.margin_cm - sd.margin_cm) > 0.05) {
      g.strokeStyle = C.tool; g.lineWidth = 1; g.setLineDash([2, 4]); g.beginPath(); g.moveTo(X(rMin), Y(T.margin_cm)); g.lineTo(X(rMax), Y(T.margin_cm)); g.stroke(); g.setLineDash([]);
      text(g, `${T.name} tip margin ${fix(T.margin_cm, 1)} cm`, L + 60, Y(T.margin_cm) - 4, C.tool);
    }
  }
  g.fillStyle = '#0b0f14'; g.strokeStyle = C.ink; g.lineWidth = 1.5;
  arm.slice(0, 3).forEach(([r, z]) => { g.beginPath(); g.arc(X(r), Y(z), 4, 0, 7); g.fill(); g.stroke(); });
  const [tr, tz] = arm[3]; g.fillStyle = C.ink; g.beginPath(); g.arc(X(tr), Y(tz), 5, 0, 7); g.fill();
  const labels = [['lift', 'shoulder_lift'], ['elbow', 'elbow_flex'], ['wrist', 'wrist_flex']];
  labels.forEach(([n, j], i) => present?.[j] != null && text(g, `${n} ${fix(present[j], 1)}°`, X(arm[i][0]) + 8, Y(arm[i][1]) + 16, C.dim));
  const clr = tz <= sd.margin_cm ? C.guard : tz <= sd.margin_cm + 2 ? C.grasp : C.ink;
  g.font = '600 12px ui-monospace, Menlo, monospace';
  text(g, `fingertips z ${fix(tz, 1)} · r ${fix(tr, 1)} cm`, Math.min(X(tr) + 10, w - 215), Math.max(14, Y(tz) - 14), clr);

  if (hover) {                                                        // crosshair + readout / marker tooltip
    g.font = '11px ui-monospace, Menlo, monospace'; g.strokeStyle = 'rgba(230,237,243,.25)'; g.lineWidth = 1;
    g.beginPath(); g.moveTo(hover.x, 0); g.lineTo(hover.x, h); g.moveTo(0, hover.y); g.lineTo(w, hover.y); g.stroke();
    const near = hits.find((p) => Math.hypot(p.x - hover.x, p.y - hover.y) < 9);
    const txt = near ? `${near.m.name} · r ${fix(near.m.r_cm, 1)} z ${fix(near.m.z_cm, 1)} cm · pan ${fix(near.m.pan, 1)}° · ${near.m.status}`
      : `r ${fix(R(hover.x), 1)} · z ${fix(Z(hover.y), 1)} cm`;
    g.fillStyle = 'rgba(11,15,20,.85)'; g.fillRect(L, 4, g.measureText(txt).width + 12, 18);
    g.fillStyle = C.ink; g.textAlign = 'left'; g.fillText(txt, L + 6, 17);
  }
  return hits;
}

// Top view: schematic — pan 0° points up, +pan turns counter-clockwise; radius = horizontal reach r.
export function drawTop(cv, sd, present) {
  const { g, w, h } = setup(cv, 220);
  if (!sd?.fitted || !sd.tip) { empty(g, w, h, 'no top view'); return; }
  const reach = sd.reach_cm || 36, cx = w / 2, cy = h * 0.62, s = Math.min(w / 2 - 12, cy - 10) / reach;
  const P = (r, pan) => [cx - r * s * Math.sin(pan * Math.PI / 180), cy - r * s * Math.cos(pan * Math.PI / 180)];
  g.strokeStyle = C.grid; g.lineWidth = 1;
  for (let r = 10; r < reach; r += 10) { g.beginPath(); g.arc(cx, cy, r * s, 0, 7); g.stroke(); }
  g.strokeStyle = C.reach; g.setLineDash([4, 5]); g.beginPath(); g.arc(cx, cy, reach * s, 0, 7); g.stroke(); g.setLineDash([]);
  g.strokeStyle = C.grid; g.beginPath(); g.moveTo(cx, cy); g.lineTo(cx, cy - reach * s); g.stroke();
  g.fillStyle = C.gridTxt; g.textAlign = 'center'; g.fillText('pan 0°', cx, 11); g.fillText('+pan ↺', 30, h - 6);
  const placed = [];
  for (const m of sd.markers) { const [x, y] = P(m.r_cm, m.pan); marker(g, x, y, m, placed); }
  if (sd.goal_tip) {
    const [x, y] = P(sd.goal_tip.r_cm, sd.goal_tip.pan); g.strokeStyle = C.goal; g.setLineDash([4, 3]); g.lineWidth = 2;
    g.beginPath(); g.moveTo(cx, cy); g.lineTo(x, y); g.stroke(); g.setLineDash([]);
  }
  const [tx, ty] = P(sd.tip.r_cm, sd.tip.pan);
  g.strokeStyle = C.links[0]; g.lineWidth = 6; g.beginPath(); g.moveTo(cx, cy); g.lineTo(tx, ty); g.stroke();
  g.fillStyle = C.ink; g.beginPath(); g.arc(tx, ty, 5, 0, 7); g.fill();
  g.fillStyle = '#2b3442'; g.beginPath(); g.arc(cx, cy, 7, 0, 7); g.fill();
  g.fillStyle = C.dim; g.textAlign = 'left'; g.fillText(`pan ${fix(present?.shoulder_pan, 1)}°`, 6, 14);
}

export function mountSide(panel) {
  const side = panel.querySelector('canvas.side'), top = panel.querySelector('canvas.topv'), out = panel.querySelector('.readout');
  let S = null, hover = null;
  const draw = () => { if (!S) return; drawSide(side, S.side, S.present, hover); drawTop(top, S.side, S.present); };
  side.addEventListener('mousemove', (e) => { hover = { x: e.offsetX, y: e.offsetY }; draw(); });
  side.addEventListener('mouseleave', () => { hover = null; draw(); });
  new ResizeObserver(draw).observe(side);
  return {
    update(state) {
      S = state; draw();
      const sd = S.side || {}, t = sd.tip, gt = sd.goal_tip, clr = S.guards?.floor?.clearance_cm;
      out.innerHTML = '';
      const kv = (k, v, cls = '') => { const d = document.createElement('div'); d.innerHTML = `<span class=dim>${k}</span> <b class="mono ${cls}">${v}</b>`; out.append(d); };
      const which = sd.tool?.state === 'mounted' ? `${sd.tool.name} tip` : 'tip';
      kv(which + ' r', t ? fix(t.r_cm, 1) + ' cm' : '—'); kv(which + ' z', t ? fix(t.z_cm, 1) + ' cm' : '—');
      kv('clearance', clr == null ? '—' : `${fix(clr, 1)} cm${S.guards?.floor?.closest ? ' (' + S.guards.floor.closest + ')' : ''}`, clr == null ? '' : clr <= 0 ? 'bad' : clr < 2 ? 'warn' : 'ok');
      kv('pan', fix(S.present?.shoulder_pan, 1) + '°');
      kv('→ goal', gt ? `r ${fix(gt.r_cm, 1)} z ${fix(gt.z_cm, 1)}` : 'at goal');
      kv('floor fit', sd.fitted ? `${sd.contacts} contacts · ${fix(sd.rms_mm, 1)} mm` : 'not fitted', sd.fitted ? '' : 'bad');
    },
  };
}
