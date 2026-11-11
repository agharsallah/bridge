// Envelope: a lab-only widening of the soft limits for poses the arm can reach but the calibration margin
// excludes. Capped by the motor sweep in the control loop, carried only by this lab's moves, never written
// to the motor registers or to config/limits.json.
import { h, fix, renderIf, SHORT } from './core.js';

export function mountEnvelope(el, ctx) {
  const box = h('div');
  el.append(h('p.dim.small', 'Soft limit = calibration sweep minus a safety margin. A lab may use part of that margin for its own moves ',
    '(up to the motor sweep, the hard ceiling). Points recorded beyond the soft limit already carry their own allowance; ',
    'the envelope also lets jogs and typed poses go there. Staged like every other change.'), box);

  const fitToPoints = (S) => {
    const pts = (S.points || []).filter((p) => p.status !== 'deleted');
    for (const J of S.joints || []) {
      if (!J.soft || J.name === 'gripper') continue;
      const vals = pts.map((p) => p.pose?.[J.name]).filter((v) => v != null);
      const out = vals.filter((v) => v < J.soft[0] || v > J.soft[1]);
      if (!out.length) continue;
      let lo = Math.min(J.soft[0], ...vals) - 0.5, hi = Math.max(J.soft[1], ...vals) + 0.5;
      if (J.hard) { lo = Math.max(lo, J.hard[0]); hi = Math.min(hi, J.hard[1]); }
      ctx.api.cmd('envelope_set', { joint: J.name, lo: +lo.toFixed(1), hi: +hi.toFixed(1) }, { quiet: true });
    }
  };

  function table(S) {
    const rows = (S.joints || []).filter((J) => J.name !== 'gripper');
    return [h('table.t', h('tr', h('th', 'joint'), h('th', 'soft limit'), h('th', 'motor sweep (hard)'), h('th', 'lab envelope'), h('th', '')),
      rows.map((J) => {
        const lo = h('input', { type: 'number', step: 0.5, value: J.envelope ? J.envelope[0] : J.soft?.[0] ?? '' });
        const hi = h('input', { type: 'number', step: 0.5, value: J.envelope ? J.envelope[1] : J.soft?.[1] ?? '' });
        const over = J.hard && J.envelope && (J.envelope[0] < J.hard[0] || J.envelope[1] > J.hard[1]);
        return h('tr', h('td', h('b', SHORT[J.name]), h('div.faint.small', J.name)),
          h('td.num', J.soft ? `${fix(J.soft[0])} … ${fix(J.soft[1])}` : '—'),
          h('td.num.dim', J.hard ? `${fix(J.hard[0])} … ${fix(J.hard[1])}` : 'known once connected'),
          h('td', h('div.row', lo, '…', hi, J.envelope ? h('span.badge.lab', 'set') : h('span.dim.small', 'none'),
            over ? h('span.warn.small', { title: 'the loop will cap it at the motor sweep' }, 'past the sweep: capped') : null)),
          h('td.acts', h('button.sm', { onclick: () => ctx.api.cmd('envelope_set', { joint: J.name, lo: +lo.value, hi: +hi.value }) }, 'Stage'),
            J.envelope ? h('button.sm', { onclick: () => ctx.api.cmd('envelope_clear', { joint: J.name }) }, 'Clear') : null));
      })),
    h('div.row', { style: { marginTop: '10px' } }, h('button', { onclick: () => fitToPoints(ctx.S) }, 'Stage envelope to cover the taught points'),
      h('span.dim.small', 'Only for joints where a point lies beyond the soft limit; capped at the motor sweep.'))];
  }

  return { update(S) { renderIf(box, 'env', { j: S.joints?.map((J) => [J.name, J.soft, J.hard, J.envelope]) }, () => table(S)); } };
}
