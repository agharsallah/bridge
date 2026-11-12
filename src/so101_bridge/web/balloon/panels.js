// Balloon lab: this lab's tabs on the shared lab page. Each panel: {id, title, mount(el, ctx) -> {update(S)}}.
// ctx = {api, prefix, S (last state), speed(), openTab(id), addTab(...)}; helpers come from /static/lab/core.js.
import { h, renderIf, fix } from '/static/lab/core.js';

export const panels = [{ id: 'balloon', title: 'Balloon lab', first: true, mount: mountMain }];

const num = (v, step, title) => h('input', { type: 'number', step, value: v, title, style: { width: '64px' } });

function mountMain(el, ctx) {
  const seen = h('div.restcard'), runs = h('div.scroll');
  const speed = num('', 1, 'thrust speed, deg/s (empty: config)');
  const dpan = num(0, 1, 'pan shift, degrees'), dr = num(0, 0.5, 'deeper (+) or shallower (-), cm'), dz = num(0, 0.5, 'higher (+) or lower (-), cm');
  const shift = () => ({ dpan: +dpan.value || 0, dr_cm: +dr.value || 0, dz_cm: +dz.value || 0, speed: +speed.value || undefined });
  el.append(
    h('p.dim.small', 'Ready pose → aim by the wrist camera → thrust until the orange disappears → retract. Every run is filmed and saved.'),
    h('div.row', h('button', { onclick: () => ctx.api.cmd('aim') }, 'Aim'),
      h('button.go', { onclick: () => ctx.api.cmd('pop', { speed: +speed.value || undefined }) }, 'Pop the balloon'),
      h('label.inline', 'speed', speed)),
    h('h4.sub', 'Wrist camera'), seen,
    h('h4.sub', 'Runs'),
    h('div.row', h('span.dim.small', 'replay shifted by'), h('label.inline', 'pan °', dpan), h('label.inline', 'reach cm', dr), h('label.inline', 'height cm', dz)),
    runs);

  const row = (r) => h('tr',
    h('td.mono.small', r.time), h('td', r.name), h('td', r.popped ? h('span.badge', 'popped') : h('span.badge.pending', r.result)),
    h('td.num', r.pop ? `${fix(r.pop.t, 1)} s` : '–'), h('td.num', `${fix(r.duration_s, 1)} s`),
    h('td.num', r.pan == null ? '–' : `${fix(r.pan, 1)}°`), h('td.num', r.pop ? `${fix(r.pop.r_cm, 1)} cm` : '–'),
    h('td.acts',
      r.pan == null ? null : h('button.sm.go', { onclick: () => ctx.api.cmd('replay', { run: r.file, ...shift() }) }, '↻ Replay'),
      r.video ? h('button.sm', { onclick: () => window.open(`${ctx.prefix}/videos/${encodeURIComponent(r.video)}`, '_blank') }, '▶ Video') : null));

  return {
    update(S) {
      const d = S.balloon || {};
      renderIf(seen, 'seen', [d.orange, d.aim], ([o, a]) => h('div.row',
        h('span.dim', 'orange'), h('b.num', o ? `${fix(o.frac * 100, 1)} %` : 'no frame'),
        h('span.dim', 'at x'), h('b.num', o && o.x != null ? fix(o.x, 2) : '–'), h('span.dim', `(needle points at ${fix(d.aim_x, 2)})`),
        a ? h('span.dim', `· last aim: pan ${fix(a.pan, 1)}° after ${a.steps} step(s)`) : null));
      renderIf(runs, 'runs', d.runs || [], (rs) => rs.length
        ? h('table.t', h('tr', ['run', 'name', 'result', 'pop at', 'total', 'pan', 'depth', ''].map((x) => h('th', x))), rs.map(row))
        : h('div.empty', 'No runs yet.'));
    },
  };
}
