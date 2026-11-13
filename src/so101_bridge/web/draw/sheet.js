// The sheet: the drawing area to scale (cm), the plan, what was traced, the pen, the touch points — and a sketch pad.
// Plain SVG. Table axes: x to the arm's right, y away from the arm (so y points up on the screen).
const NS = 'http://www.w3.org/2000/svg';
const el = (name, attrs, text) => {
  const e = document.createElementNS(NS, name);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
};
const line = (pts, attrs) => el(pts.length > 1 ? 'polyline' : 'circle', pts.length > 1
  ? { points: pts.map((p) => `${p[0]},${-p[1]}`).join(' '), fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', ...attrs }
  : { cx: pts[0][0], cy: -pts[0][1], r: 0.12, fill: attrs.stroke });

export function mountSheet(host) {
  const svg = el('svg', { style: 'width:100%;height:auto;background:#f6f4ee;border-radius:8px;touch-action:none' });
  host.append(svg);
  let data = null, drawn = '', sketching = false, mine = [], cur = null;

  const at = (e) => {                                    // pointer -> table cm
    const p = svg.createSVGPoint(); p.x = e.clientX; p.y = e.clientY;
    const q = p.matrixTransform(svg.getScreenCTM().inverse());
    return [Math.round(q.x * 10) / 10, Math.round(-q.y * 10) / 10];
  };

  function draw() {
    svg.replaceChildren();
    if (!data || !data.area) return;
    const [x0, x1] = data.area.x, [y0, y1] = data.area.y, m = 2, L = data.live || {}, strokes = (data.plan || {}).strokes || [];
    svg.setAttribute('viewBox', `${x0 - m} ${-(y1 + m)} ${x1 - x0 + 2 * m} ${y1 - y0 + 2 * m}`);
    for (let x = Math.ceil(x0); x <= x1; x++) svg.append(el('line', { x1: x, y1: -y0, x2: x, y2: -y1, stroke: '#e3dfd3', 'stroke-width': x % 5 ? 0.03 : 0.07 }));
    for (let y = Math.ceil(y0); y <= y1; y++) svg.append(el('line', { x1: x0, y1: -y, x2: x1, y2: -y, stroke: '#e3dfd3', 'stroke-width': y % 5 ? 0.03 : 0.07 }));
    svg.append(el('rect', { x: x0, y: -y1, width: x1 - x0, height: y1 - y0, fill: 'none', stroke: '#b9b29d', 'stroke-width': 0.08, 'stroke-dasharray': '0.4 0.3' }),
      el('text', { x: x0, y: -y0 + 1.3, 'font-size': 0.9, fill: '#8a8370' }, `x ${x0}…${x1} cm · y ${y0}…${y1} cm · arm below`));
    const busy = L.stroke != null && /^(travel|draw)$/.test(L.phase || '');
    strokes.forEach((s, i) => svg.append(line(s, { stroke: busy && i === L.stroke ? '#1f6feb' : '#6f9bd8', 'stroke-width': busy && i === L.stroke ? 0.28 : 0.16,
      opacity: sketching ? 0.25 : busy && i > L.stroke ? 0.45 : 1 })));
    for (const s of L.trace || []) if (s.length) svg.append(line(s, { stroke: '#e8590c', 'stroke-width': 0.12, opacity: 0.9 }));
    for (const t of data.touch || []) {
      svg.append(el('path', { d: `M${t[0] - 0.4},${-t[1]}h0.8M${t[0]},${-t[1] - 0.4}v0.8`, stroke: '#2b8a3e', 'stroke-width': 0.1 }),
        el('text', { x: t[0] + 0.5, y: -t[1] - 0.3, 'font-size': 0.7, fill: '#2b8a3e' }, `${t[2]}`));
    }
    for (const s of mine.concat(cur ? [cur] : [])) svg.append(line(s, { stroke: '#111', 'stroke-width': 0.18 }));
    const p = data.pen;
    if (p) {
      const down = p.up_cm < 0.3;
      svg.append(el('circle', { cx: p.x, cy: -p.y, r: down ? 0.3 : 0.45, fill: down ? '#e8590c' : 'none', stroke: '#e8590c', 'stroke-width': 0.12 }));
    }
  }

  svg.addEventListener('pointerdown', (e) => { if (sketching) { cur = [at(e)]; svg.setPointerCapture(e.pointerId); draw(); } });
  svg.addEventListener('pointermove', (e) => {
    if (!cur) return;
    const q = at(e), a = cur[cur.length - 1];
    if (Math.hypot(q[0] - a[0], q[1] - a[1]) >= 0.3) { cur.push(q); draw(); }
  });
  svg.addEventListener('pointerup', () => { if (cur) { mine.push(cur); cur = null; draw(); } });

  return {
    update(d) {
      const sig = JSON.stringify([d.area, d.plan && d.plan.strokes, d.live, d.pen, d.touch]);
      data = d;
      if (sig !== drawn) { drawn = sig; draw(); }
    },
    sketch(on) { sketching = on; svg.style.cursor = on ? 'crosshair' : ''; draw(); return on; },
    sketching: () => sketching,
    strokes: () => mine.slice(),
    area: () => data && data.area,
    clear() { mine = []; cur = null; draw(); },
  };
}
