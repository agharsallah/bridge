// Points & rest: record the present pose under a name (staged), visit it (with a move preview), edit,
// rename, delete, copy; the lab rest (or the global one) with record / reset / go.
import { h, fix, renderIf, dialog, copy, JOINTS, SHORT } from './core.js';

const ROLES = ['reference', 'calibration', 'approach', 'waypoint', 'contact'];

// a modal preview of a move: per-joint change, the largest one, rough time, joints that need the envelope
export async function previewMove(ctx, title, pose, extra = '') {
  const now = ctx.S?.present || {}, speed = ctx.speed?.() || 4;
  const rows = JOINTS.filter((j) => pose[j] != null).map((j) => ({ j, d: pose[j] - (now[j] ?? pose[j]) }));
  const max = rows.reduce((m, r) => Math.max(m, Math.abs(r.d)), 0);
  const soft = Object.fromEntries((ctx.S?.joints || []).map((J) => [J.name, J.soft]));
  const out = rows.filter((r) => soft[r.j] && (pose[r.j] < soft[r.j][0] || pose[r.j] > soft[r.j][1])).map((r) => SHORT[r.j]);
  const body = h('div',
    h('table.t.small', h('tr', h('th', 'joint'), h('th', 'now'), h('th', 'target'), h('th', 'Δ')),
      rows.map((r) => h('tr', h('td', SHORT[r.j]), h('td.num', fix(now[r.j], 1)), h('td.num', fix(pose[r.j], 1)),
        h('td.num' + (Math.abs(r.d) === max && max > 0 ? '.warn' : ''), (r.d > 0 ? '+' : '') + fix(r.d, 1))))),
    h('p', `Largest change ${fix(max, 1)}° at ${speed} °/s, in ≤10° steps: about ${fix(max / speed + 1, 0)} s. `,
      out.length ? h('span.warn', `Beyond the soft limit (uses the lab envelope): ${out.join(', ')}. `) : null,
      'Table guard, step cap and load guards apply. Keep a hand near STOP (Esc).', extra));
  return dialog({ title, body, ok: 'Move', wide: true });
}

export function mountPoints(el, ctx) {
  const name = h('input', { type: 'text', placeholder: 'point name', maxlength: 40, style: { width: '170px' } });
  const role = h('select', ROLES.map((r) => h('option', r)));
  const note = h('input', { type: 'text', placeholder: 'note (optional)', style: { width: '220px' } });
  const record = async () => {
    const n = name.value.trim(); if (!n) { name.focus(); return; }
    const exists = (ctx.S?.points || []).some((p) => p.name === n && p.status !== 'deleted');
    if (exists && !(await dialog({ title: 'Re-record point', body: `"${n}" exists. Stage the present pose in its place? (You can still discard it.)`, ok: 'Stage' }))) return;
    const r = await ctx.api.cmd('point_record', { name: n, role: role.value, note: note.value });
    if (r.ok) { name.value = ''; note.value = ''; }
  };
  name.addEventListener('keydown', (e) => e.key === 'Enter' && record());
  const table = h('div.scroll'), restBox = h('div.restcard');
  el.append(
    h('div.row.recbar', h('b', 'Record present pose as'), name, role, note, h('button.primary', { onclick: record }, 'Stage point'),
      h('span.dim.small', 'Free-drive or jog the arm there first. Staged, so nothing is saved until you apply it.')),
    table, h('h4.sub', 'Rest position'), restBox);

  const go = async (p, grip) => {
    const pose = { ...p.pose }; if (!grip) delete pose.gripper;
    if (await previewMove(ctx, `Go to ${p.name}`, pose, p.status !== 'saved' ? ' This point is not saved yet (staged).' : '')) ctx.api.cmd('goto_point', { name: p.name, with_gripper: grip, speed: ctx.speed?.() });
  };
  const edit = async (p) => {
    const r = await dialog({ title: `Edit ${p.name}`, body: 'Pose in degrees, role and note. Staged until applied.', json: { pose: p.pose, role: p.role, note: p.note }, ok: 'Stage', wide: true });
    if (r) ctx.api.cmd('point_edit', { name: p.name, ...r.value });
  };
  const rename = async (p) => {
    const r = await dialog({ title: `Rename ${p.name}`, input: p.name, ok: 'Stage rename' });
    if (r && r.value.trim() && r.value.trim() !== p.name) ctx.api.cmd('point_rename', { name: p.name, new: r.value.trim() });
  };

  function rows(points) {
    if (!points.length) return h('div.empty', 'No points yet. Move the arm, name the pose above and stage it.');
    return h('table.t', h('tr', h('th', ''), h('th', 'name'), h('th', 'role'), h('th', 'tip r / z'), h('th', 'pose'), h('th', '')),
      points.map((p) => h('tr' + (p.status === 'deleted' ? '.deleted' : ''),
        h('td', h('span.badge.' + p.status, p.status), p.beyond.length ? h('span.badge.beyond', { title: 'beyond the soft limit: ' + p.beyond.join(', ') }, 'envelope') : null),
        h('td.name', h('b', p.name), p.note ? h('div.dim.small', p.note) : null),
        h('td.dim', p.role || '—'),
        h('td.num', p.tip ? `${fix(p.tip.r_cm, 1)} / ${fix(p.tip.z_cm, 1)}` : '—'),
        h('td.num.small.dim', JOINTS.filter((j) => p.pose[j] != null).map((j) => `${SHORT[j]} ${fix(p.pose[j], 1)}`).join('  ')),
        h('td.acts', p.status === 'deleted'
          ? h('span.dim.small', 'staged for deletion')
          : [h('button.sm.go', { onclick: () => go(p, false) }, 'Go'),
            h('button.sm', { title: 'Go, including the gripper opening', onclick: () => go(p, true) }, 'Go+grip'),
            h('button.sm', { title: 'Stage the present pose for this point', onclick: () => { name.value = p.name; role.value = p.role || 'reference'; note.value = p.note || ''; record(); } }, 'Re-record'),
            h('button.sm', { onclick: () => edit(p) }, 'Edit'), h('button.sm', { onclick: () => rename(p) }, 'Rename'),
            h('button.sm', { onclick: () => copy(JSON.stringify(p.pose), `${p.name} pose copied`) }, 'Copy'),
            h('button.sm.danger', { onclick: () => ctx.api.cmd('point_delete', { name: p.name }) }, 'Delete')]))));
  }

  function rest(r) {
    return [h('div.row',
      h('span.badge.' + (r.source === 'lab' ? 'lab' : ''), r.source === 'lab' ? 'lab rest' : 'global rest'), r.pending ? h('span.badge.pending', 'pending') : null,
      h('span.num.small', JOINTS.filter((j) => r.pose[j] != null).map((j) => `${SHORT[j]} ${fix(r.pose[j], 1)}`).join('  ')),
      r.tip ? h('span.dim.small', `tip r ${fix(r.tip.r_cm, 1)} z ${fix(r.tip.z_cm, 1)} cm`) : null),
    h('div.row', { style: { marginTop: '8px' } },
      h('button.go', { onclick: async () => { if (await previewMove(ctx, 'Go to rest', r.pose, r.source === 'lab' ? ' Path: up to the high folded pose, then down onto the lab rest.' : ' Path: the proven global REST fold.')) ctx.api.cmd('rest', { speed: ctx.speed?.() }); } }, 'Go to rest'),
      h('button', { onclick: () => ctx.api.cmd('rest_record') }, 'Stage present as lab rest'),
      r.source === 'lab' ? h('button', { onclick: () => ctx.api.cmd('rest_clear') }, 'Use the global rest') : null,
      h('span.dim.small', 'The global rest (config/rest.json) is never changed from a lab.'))];
  }

  return {
    update(S) {
      renderIf(table, 'pts', S.points || [], rows);
      renderIf(restBox, 'rest', S.rest || {}, rest);
    },
  };
}
