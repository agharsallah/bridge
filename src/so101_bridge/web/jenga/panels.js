// Jenga lab: this lab's tabs on the shared lab page. Each panel: {id, title, mount(el, ctx) -> {update(S)}}.
// ctx = {api, prefix, S (last state), speed(), openTab(id), addTab(...)}; helpers come from /static/lab/core.js.
import { h, renderIf, fix } from '/static/lab/core.js';
import { mountTower } from '/static/jenga/tower3d.js';

export const panels = [{ id: 'jenga', title: 'Jenga lab', first: true, mount: mountMain }];

const PHASES = ['stage', 'place', 'align', 'push', 'retract'];
const bar = (frac, color) => h('div', { style: { height: '8px', borderRadius: '4px', background: 'var(--line)', overflow: 'hidden', flex: '1', minWidth: '120px' } },
  h('div', { style: { height: '100%', width: `${Math.round(100 * Math.max(0, Math.min(1, frac)))}%`, background: color } }));

function mountMain(el, ctx) {
  const info = h('div.restcard'), live = h('div.restcard'), runs = h('div.scroll'), view = h('div'), pick = h('div.restcard');
  let chosen = null, drawn = '';
  const tower3d = mountTower(view, (b) => { chosen = b; });
  const step = h('input', { type: 'number', step: 0.1, min: 0.1, max: 2, value: 0.3, title: 'cm per click', style: { width: '64px' } });
  const depth = h('input', { type: 'number', step: 0.5, min: 0.5, title: 'how far the brick is pushed, cm', style: { width: '64px' } });
  const nudge = (dr, dz) => ctx.api.cmd('nudge', { dr_cm: dr * +step.value, dz_cm: dz * +step.value }, { quiet: true });
  el.append(
    h('p.dim.small', 'Play a turn: the arm goes to the tower, lines the stick up on a row by camera, pushes the middle brick out and pulls the stick back. Filmed and saved.'),
    h('div.row', h('button.go', { onclick: () => ctx.api.cmd('turn', chosen && chosen.col === 1 ? { row: chosen.row } : {}) }, '▶ Play a turn'),
      h('button', { onclick: () => ctx.api.cmd('see') }, 'Check the height'),
      h('button.sm', { onclick: () => ctx.api.cmd('tower_reset') }, 'Tower rebuilt')),
    h('div.row', { style: { alignItems: 'flex-start' } }, view, h('div', { style: { flex: '1', minWidth: '220px' } }, pick, h('h4.sub', 'Now'), live)),
    h('h4.sub', 'By hand'),
    h('p.dim.small', 'Nudge the tip yourself, or push from where it is (the tip moves straight; at most 3.5 cm).'),
    h('div.row', h('button', { onclick: () => nudge(0, 1) }, '↑ up'), h('button', { onclick: () => nudge(0, -1) }, '↓ down'),
      h('button', { onclick: () => nudge(1, 0) }, '→ forward'), h('button', { onclick: () => nudge(-1, 0) }, '← back'),
      h('label.inline', 'step cm', step)),
    h('div.row', h('button.go', { onclick: () => ctx.api.cmd('push', { depth_cm: +depth.value || undefined }) }, 'Push the brick'),
      h('label.inline', 'depth cm', depth)),
    h('h4.sub', 'Tip'), info, h('h4.sub', 'Runs'), runs);

  const row = (r) => h('tr', h('td.mono.small', r.time), h('td', r.name),
    h('td', h(r.result === 'pushed' ? 'span.badge.new' : 'span.badge.pending', r.result)),
    h('td.num', r.depth_cm == null ? '–' : `${fix(r.depth_cm, 1)} cm`), h('td.num', `${fix(r.duration_s, 1)} s`),
    h('td.acts', r.video ? h('button.sm', { onclick: () => window.open(`${ctx.prefix}/videos/${encodeURIComponent(r.video)}`, '_blank') }, '▶ Video') : null));

  return {
    update(S) {
      const d = S.jenga || {}, t = (S.side || {}).tip, L = d.live || {};
      if (document.activeElement !== depth && !depth.value) depth.value = d.depth_cm;
      // how hard the arm is working: the joint closest to its load guard (a brick that jams shows here first)
      const load = Math.max(0, ...(S.joints || []).map((j) => (j.load_limit ? Math.abs(j.load || 0) / j.load_limit : 0)));
      const p = S.path, done = p && p.total ? 1 - p.remaining / p.total : 0;
      const frac = L.phase === 'push' ? done : L.phase === 'hold' ? 1 : L.phase === 'retract' ? 1 - done : 0;
      const T = S.tower;
      const sigT = JSON.stringify(T || null);
      if (T && sigT !== drawn) { drawn = sigT; tower3d.update(T); }           // not renderIf: it would replace the SVG the view owns
      renderIf(pick, 'pick', [chosen, T && T.playable, T && T.stick], () => pickCard(ctx, chosen, T));
      renderIf(live, 'live', [L.phase, Math.round(frac * 20), Math.round(load * 20)], () => h('div',
        h('div.row', PHASES.map((x) => h(x === L.phase ? 'span.chip.ok' : 'span.chip', h('span.dot'), x)),
          PHASES.includes(L.phase) ? null : h('span.dim', L.phase === 'idle' ? 'no push yet' : `last: ${L.phase}`)),
        h('div.row', h('span.dim', 'brick'), bar(frac, 'var(--ok)'), h('b.num', `${fix(frac * (L.depth_cm || d.depth_cm || 0), 1)} cm`)),
        h('div.row', h('span.dim', 'load'), bar(load, load > 0.85 ? 'var(--bad)' : load > 0.6 ? 'var(--warn)' : 'var(--faint)'), h('b.num', `${Math.round(load * 100)} %`))));
      renderIf(info, 'info', t, (x) => x ? h('div.row', h('span.dim', 'reach'), h('b.num', fix(x.r_cm, 1) + ' cm'),
        h('span.dim', 'height'), h('b.num', fix(x.z_cm, 1) + ' cm'), h('span.dim', 'pan'), h('b.num', fix(x.pan, 1) + '°')) : h('span.dim', 'no floor model'));
      renderIf(runs, 'runs', d.runs || [], (rs) => rs.length
        ? h('table.t', h('tr', ['run', 'name', 'result', 'depth', 'total', ''].map((x) => h('th', x))), rs.map(row))
        : h('div.empty', 'No pushes yet.'));
    },
  };
}

// What is selected in the 3-D view, whether it can be played, and buttons to correct the tower's bookkeeping.
function pickCard(ctx, b, T) {
  if (!T) return h('span.dim', 'no tower');
  const next = (T.playable || [])[0], k = T.stick;
  const where = k && k.at_tower ? h('div.small.dim', k.gap_cm >= 0 ? `stick tip ${fix(k.gap_cm, 1)} cm before the face, ${fix(k.z_cm, 1)} cm high`
    : `stick tip ${fix(-k.gap_cm, 1)} cm inside the tower`) : h('div.small.dim', 'stick away from the tower');
  if (!b) return h('div', h('div', h('span.dim', 'next turn: '), next ? h('b', `row ${next}, middle brick`) : h('b', 'nothing left to play')),
    h('div.small.dim', `${T.rows} rows · ${fix(T.depth_cm, 1)} cm push · click a brick to pick it, drag to turn the tower`), where);
  const ok = b.col === 1 && (T.playable || []).includes(b.row);
  const mark = (state) => h('button.sm', { onclick: () => ctx.api.cmd('tower_mark', { row: b.row, col: b.col, state }) }, state);
  return h('div', h('div', h('b', `row ${b.row}, ${['left', 'middle', 'right'][b.col]} brick`), h('span.dim', ` · ${b.state}`),
    ok ? h('span.badge.new', 'playable') : h('span.badge.pending', b.col === 1 ? 'not playable' : 'only middle bricks')),
    h('div.row', h('span.dim.small', 'mark as'), mark('in'), mark('pushed'), mark('out')), where);
}
