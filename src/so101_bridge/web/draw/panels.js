// Drawing lab: this lab's tabs on the shared lab page. Each panel: {id, title, mount(el, ctx) -> {update(S)}}.
// ctx = {api, prefix, S (last state), speed(), openTab(id), addTab(...)}; helpers come from /static/lab/core.js.
import { h, renderIf, fix } from '/static/lab/core.js';
import { mountSheet } from '/static/draw/sheet.js';

export const panels = [{ id: 'draw', title: 'Drawing lab', first: true, mount: mountMain }];

function mountMain(el, ctx) {
  const view = h('div', { style: { flex: '2', minWidth: '300px', maxWidth: '560px' } }), pick = h('div.row'), status = h('div.restcard'), runs = h('div.scroll');
  const photo = h('img', { style: { width: '100%', maxWidth: '420px', borderRadius: '8px', border: '1px solid var(--line)' }, alt: 'camera' });
  const num = (title, step, w = '60px') => h('input', { type: 'number', step, title, style: { width: w } });
  const scale = num('fraction of the drawing area', 0.05), dx = num('shift right, cm', 0.5), dy = num('shift away from the arm, cm', 0.5);
  const sheet = mountSheet(view);
  let cam = 'top', lastPhase = '';
  const place = () => ({ scale: +scale.value || undefined, dx_cm: +dx.value || 0, dy_cm: +dy.value || 0 });
  const shoot = () => { photo.src = `${ctx.prefix}/cam/${cam}.jpg?w=960&t=${Date.now()}`; };
  const sketchBtn = h('button.sm', { onclick: () => { sketchBtn.textContent = sheet.sketch(!sheet.sketching()) ? '✎ Sketching: draw on the sheet' : '✎ Sketch your own'; } }, '✎ Sketch your own');
  const useSketch = () => {
    const strokes = sheet.strokes();
    if (!strokes.length) return;
    const a = sheet.area(), xs = strokes.flat().map((q) => q[0]), ys = strokes.flat().map((q) => q[1]);
    const lo = (v) => Math.min(...v), hi = (v) => Math.max(...v), mid = (v) => (v[0] + v[1]) / 2;
    ctx.api.cmd('show', { strokes,                                         // saved as sketch_<time>, placed exactly where it was sketched
      scale: Math.min(1.5, Math.max(0.05, (hi(xs) - lo(xs)) / (a.x[1] - a.x[0]), (hi(ys) - lo(ys)) / (a.y[1] - a.y[0]))),
      dx_cm: (lo(xs) + hi(xs)) / 2 - mid(a.x), dy_cm: (lo(ys) + hi(ys)) / 2 - mid(a.y) });
    delete scale.dataset.touched;
    sheet.sketch(false); sheet.clear(); sketchBtn.textContent = '✎ Sketch your own';
  };
  el.append(
    h('p.dim.small', 'What the arm intends to draw is shown on the sheet (x to the arm’s right, y away from it, in cm). Blue: the plan. '
      + 'Orange: where the arm says the pen went. Measure the paper once after the pen or the sheet moved, then draw.'),
    pick,
    h('div.row', h('label.inline', 'size', scale), h('label.inline', 'right cm', dx), h('label.inline', 'away cm', dy),
      h('button.sm', { onclick: () => ctx.api.cmd('show', place(), { quiet: true }) }, 'Place'),
      sketchBtn, h('button.sm', { onclick: useSketch }, 'Use sketch'), h('button.sm', { onclick: () => sheet.clear() }, 'Clear sketch')),
    h('div.row', { style: { alignItems: 'flex-start' } }, view,
      h('div', { style: { flex: '1', minWidth: '240px' } },
        h('div.row', h('button.go', { onclick: () => ctx.api.cmd('draw', place()) }, '▶ Draw'),
          h('button', { onclick: () => ctx.api.cmd('calibrate') }, 'Measure the paper'),
          h('button.sm', { onclick: () => ctx.api.cmd('pen') }, 'Pen above the middle')),
        h('h4.sub', 'Now'), status,
        h('h4.sub', 'The paper'), h('div.row', h('button.sm', { onclick: shoot }, '↻ Refresh'),
          h('button.sm', { onclick: () => ctx.api.cmd('photo') }, 'Save a photo')), photo)),
    h('h4.sub', 'Runs'), runs);

  const row = (r) => h('tr', h('td.mono.small', r.time), h('td', r.name),
    h('td', h(r.result === 'drawn' ? 'span.badge.new' : 'span.badge.pending', r.result)),
    h('td.num', r.place ? `${fix(r.place.scale, 2)} · ${fix(r.place.dx_cm, 1)}, ${fix(r.place.dy_cm, 1)}` : '–'), h('td.num', `${fix(r.duration_s, 0)} s`),
    h('td.acts', h('button.sm', { title: 'same drawing, same place (change size / shift above first to move it)',
      onclick: () => ctx.api.cmd('draw', { run: r.file, ...(scale.dataset.touched ? place() : {}) }) }, '↻ Draw again'),
      r.video ? h('button.sm', { onclick: () => window.open(`${ctx.prefix}/videos/${encodeURIComponent(r.video)}`, '_blank') }, '▶ Video') : null));
  for (const i of [scale, dx, dy]) i.addEventListener('input', () => { scale.dataset.touched = '1'; });

  return {
    update(S) {
      const d = S.draw || {}, p = d.plan || {}, L = d.live || {}, pl = p.place || {};
      cam = d.camera || 'top';
      if (!photo.src || (L.phase !== lastPhase && /^(drawn|measured)/.test(L.phase || ''))) shoot();
      lastPhase = L.phase;
      for (const [i, v] of [[scale, pl.scale], [dx, pl.dx_cm], [dy, pl.dy_cm]]) if (document.activeElement !== i && !scale.dataset.touched && v != null) i.value = v;
      sheet.update(d);
      renderIf(pick, 'pick', [d.names, p.name], () => (d.names || []).map((n) => h(n === p.name ? 'button.sm.go' : 'button.sm',
        { onclick: () => { delete scale.dataset.touched; ctx.api.cmd('show', { name: n }, { quiet: true }); } }, n)));
      renderIf(status, 'status', [p.name, p.problem, p.length_cm, L.phase, L.stroke, d.pen, (d.touch || []).length, S.tool && S.tool.name], () => h('div',
        h('div', h('b', p.name || '–'), h('span.dim', ` · ${(p.strokes || []).length} strokes · ${fix(p.length_cm || 0, 0)} cm of line`)),
        p.problem ? h('div', h('span.badge.delete', 'cannot draw'), ' ', p.problem) : null,
        h('div.row', h(L.phase === 'draw' ? 'span.chip.ok' : 'span.chip', h('span.dot'), L.phase || 'idle'),
          L.stroke != null ? h('span.dim', `stroke ${L.stroke + 1} of ${(p.strokes || []).length}`) : null),
        d.pen ? h('div.small.dim', `pen at x ${fix(d.pen.x, 1)}, y ${fix(d.pen.y, 1)} cm, ${fix(d.pen.up_cm, 1)} cm above the paper`) : h('div.small.dim', 'no floor model'),
        h('div.small', (d.touch || []).length ? h('span.dim', `paper measured at ${d.touch.length} point(s)`)
          : h('span.badge.pending', 'paper not measured yet')),
        S.tool && S.tool.name ? null : h('div.small', h('span.badge.pending', 'no pen mounted'), ' mount it in the Tool tab')));
      renderIf(runs, 'runs', d.runs || [], (rs) => rs.length
        ? h('table.t', h('tr', ['run', 'drawing', 'result', 'size · shift', 'total', ''].map((x) => h('th', x))), rs.map(row))
        : h('div.empty', 'Nothing drawn yet.'));
    },
  };
}
