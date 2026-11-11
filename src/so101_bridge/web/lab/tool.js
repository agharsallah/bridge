// Tool tab: a pen / brush / probe held in the gripper. Define it (staged), record the grip, mount it
// (the table guard then watches its tip and both ends, and the gripper locks), unmount it.
import { h, fix, renderIf } from './core.js';

const FIGURE = `<svg viewBox="0 0 360 120" width="360" height="120" role="img" aria-label="tool held in the gripper">
  <rect x="10" y="40" width="70" height="40" rx="6" fill="#263140"/><text x="45" y="64" fill="#8b98a8" font-size="11" text-anchor="middle">wrist</text>
  <path d="M80 44 L170 50 L170 56 L80 56Z M80 76 L170 70 L170 64 L80 64Z" fill="#3a4658"/>
  <line x1="120" y1="60" x2="300" y2="60" stroke="#39d0d8" stroke-width="7" stroke-linecap="round"/>
  <circle cx="300" cy="60" r="4" fill="#e6edf3"/>
  <line x1="170" y1="30" x2="170" y2="92" stroke="#5b6778" stroke-dasharray="3 3"/><text x="170" y="24" fill="#8b98a8" font-size="10" text-anchor="middle">fingertips</text>
  <path d="M120 100 H300" stroke="#bc8cff"/><text x="210" y="114" fill="#bc8cff" font-size="11" text-anchor="middle">length</text>
  <path d="M170 86 H300" stroke="#d29922"/><text x="235" y="82" fill="#d29922" font-size="11" text-anchor="middle">out (past the fingertips)</text>
  <text x="306" y="56" fill="#e6edf3" font-size="11">tip</text></svg>`;

export function mountTool(el, ctx) {
  const status = h('div.restcard'), form = h('div'), help = h('div');
  help.innerHTML = `<div class="row" style="align-items:flex-start;gap:16px">${FIGURE}<ol class="small dim" style="margin:0;padding-left:18px;max-width:520px">
    <li>Measure the tool: total <b>length</b> and the part sticking <b>out</b> past the fingertips. Stage and apply.</li>
    <li>Open the gripper, put the tool in, close it on the tool (gripper row), then <b>Use present gripper</b> and apply.</li>
    <li><b>Mount</b>: the table guard now watches the tool tip (its own margin) and both ends; the gripper is locked on every page.</li>
    <li><b>Unmount</b> before taking the tool out; the jaws stay closed until you open them.</li></ol></div>`;
  el.append(status, h('h4.sub', 'Definition'), form, h('h4.sub', 'How it works'), help);

  function card(T) {
    const m = T.mounted, side = ctx.S?.side?.tool, g = ctx.S?.guards?.floor;
    if (m) return h('div.row', h('span.badge.lab', 'mounted'), h('b', m.name),
      h('span.num.small', `${fix(m.length_cm, 1)} cm long · ${fix(m.out_cm, 1)} cm out · tip margin ${fix(m.margin_cm, 1)} cm · grip ${fix(m.grip, 1)}`),
      side ? h('span.small', h('span.dim', 'tip clearance '), h('b.num' + (side.clearance_cm <= 0 ? '.bad' : side.clearance_cm < 1 ? '.warn' : '.ok'), `${fix(side.clearance_cm, 1)} cm`)) : null,
      g?.closest ? h('span.dim.small', `closest to the table: ${g.closest}`) : null,
      h('button.danger.right', { onclick: () => ctx.api.cmd('tool_unmount') }, 'Unmount…'));
    const why = !T.saved ? 'define the tool and apply the change first' : T.pending ? 'apply or discard the pending tool change first' : '';
    return h('div.row', h('span.badge', 'no tool mounted'), h('span.dim.small', 'The table guard watches the fingertips only; the gripper is free.'),
      h('button.go.right', { disabled: !!why, title: why || 'lock the gripper on the tool and guard its tip', onclick: () => ctx.api.cmd('tool_mount') }, 'Mount…'),
      why ? h('span.warn.small', why) : null);
  }

  function editor(T) {
    const d = T.defined || {};
    const num = (v, step, min) => h('input', { type: 'number', step, min, value: v ?? '' });
    const name = h('input', { type: 'text', value: d.name || 'pen', maxlength: 40, style: { width: '120px' } });
    const len = num(d.length_cm, 0.1, 0.1), out = num(d.out_cm, 0.1, 0), margin = num(d.margin_cm ?? 0.8, 0.1, T.margin_min_cm), grip = num(d.grip ?? 0, 0.5, 0);
    const field = (label, input, hint) => h('label.inline', { title: hint }, label, input);
    return [h('div.row',
      field('name', name), field('length cm', len, 'the whole tool, end to end'),
      field('out cm', out, 'how far the tip sticks out past the fingertips'),
      field('tip margin cm', margin, `how close the tool tip may come to the table plane (min ${T.margin_min_cm}: ≤ 0 lets a pen touch the paper). The fingertips keep their own margin.`),
      field('grip', grip, 'gripper value that holds the tool closed (0..100)'),
      h('button.primary', { onclick: () => ctx.api.cmd('tool_define', { name: name.value, length_cm: +len.value, out_cm: +out.value, margin_cm: +margin.value, grip: +grip.value }) }, 'Stage'),
      h('button', { title: 'stage the present gripper opening as the grip value', disabled: !T.defined, onclick: () => ctx.api.cmd('tool_grip_record') }, 'Use present gripper'),
      T.defined ? h('button.ghost', { onclick: () => ctx.api.cmd('tool_clear') }, 'Remove definition') : null),
    T.pending ? h('div.warn.small', { style: { marginTop: '6px' } }, 'Pending tool change: review it under Pending changes and apply.') : null,
    T.mounted && T.saved && JSON.stringify(T.saved) !== JSON.stringify(T.mounted)
      ? h('div.warn.small', { style: { marginTop: '6px' } }, 'The saved definition differs from the mounted tool: unmount and mount again to use it.') : null];
  }

  return {
    update(S) {
      const T = S.tool || {};
      renderIf(status, 'tool', { T, side: S.side?.tool && fix(S.side.tool.clearance_cm, 1), c: S.guards?.floor?.closest }, () => card(T));
      renderIf(form, 'toolform', { d: T.defined, p: T.pending, s: T.saved, m: T.mounted }, () => editor(T));
    },
  };
}
