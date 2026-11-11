// Guards: what the control loop enforces on every move from this page, read-only, plus recent guard events.
import { h, fix, renderIf, SHORT } from './core.js';

const tile = (label, value, cls, hint) => h('div.gtile.' + (cls || 'neutral'), { title: hint || '' }, h('div.dim.small', label), h('b.num', value));

export function mountGuards(panel) {
  const body = panel.querySelector('.pb');
  function build({ g, joints }) {
    const f = g.floor, clr = f.clearance_cm;
    const loads = (joints || []).filter((J) => J.load_limit);
    return [
      h('div.gtiles',
        tile('ESTOP', g.estop ? 'SET' : 'clear', g.estop ? 'bad' : 'ok', 'touch ESTOP / the STOP button / Esc'),
        tile('table guard', f.fitted ? `${fix(f.margin_cm, 1)} cm` : 'NOT FITTED', f.fitted ? 'ok' : 'bad',
          f.fitted ? `floor model: ${f.contacts} contacts, rms ${fix(f.rms_mm, 1)} mm. Moves are scaled back so the tips stay above this; below ${fix(f.freeze_cm, 1)} cm the arm freezes.` : 'Without the floor model there is no table guard. Fit it on the control dashboard.'),
        tile('clearance' + (f.closest ? ` (${f.closest})` : ''), clr == null ? '—' : `${fix(clr, 1)} cm`, clr == null ? 'neutral' : clr <= 0 ? 'bad' : clr < 2 ? 'warn' : 'ok', 'how far the guarded point closest to the table is above its margin (fingertips, or a mounted tool tip / back end)'),
        g.tool ? tile('tool', `${g.tool.name} · tip ≥ ${fix(g.tool.margin_cm, 1)} cm`, 'ok', `${g.tool.length_cm} cm long, ${g.tool.out_cm} cm out. Its tip keeps its own margin, its back end and the fingertips keep ${fix(f.margin_cm, 1)} cm; the gripper is locked.`) : null,
        tile('step cap', `${fix(g.step_cap_deg, 0)}°`, 'neutral', 'no single goal moves a joint further than this from where it is'),
        tile('stall', `${fix(g.stall.deg, 0)}° / ${fix(g.stall.sec, 1)} s`, 'neutral', 'lagging the command by more than this for longer stops the move'),
        tile('loop', g.loop_hz ? `${fix(g.loop_hz, 0)} Hz` : '—', g.loop_hz && g.loop_hz < 20 ? 'warn' : 'neutral', 'control loop rate')),
      loads.length ? h('div.loads', loads.map((J) => {
        const fr = J.load == null ? 0 : Math.min(1, Math.abs(J.load) / J.load_limit);
        return h('div.lrow', h('span.small', SHORT[J.name]), h('div.load' + (fr > 0.85 ? '.bad' : fr > 0.6 ? '.warn' : ''), h('i', { style: { width: `${fr * 100}%` } })),
          h('span.num.small.dim', `${J.load ?? '—'} / ${J.load_limit}`));
      })) : null,
      h('div.events', g.events.length ? g.events.slice().reverse().slice(0, 10).map((e) => h('div.ev',
        h('span.num.faint', e.time || ''), h('span.badge' + (/FREEZE|STALL|LOAD/.test(e.kind) ? '.deleted' : '.pending'), e.kind), h('span.small', e.msg)))
        : h('div.dim.small', 'No guard events since the bridge started.')),
    ];
  }
  return { update(S) { if (S.guards) renderIf(body, 'g', { g: { ...S.guards, loop_hz: Math.round(S.guards.loop_hz || 0) }, joints: S.joints?.map((J) => ({ ...J, present: 0, goal: 0 })) }, build); } };
}
