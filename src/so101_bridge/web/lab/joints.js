// Joints & jog: per joint the present/goal values, a range track (motor sweep / soft limit / lab envelope /
// taught points / present / goal), jog buttons, an absolute target and the load against its guard.
import { h, fix, pref, SHORT, dialog } from './core.js';

const COLOR = { shoulder_pan: 'var(--pan)', shoulder_lift: 'var(--lift)', elbow_flex: 'var(--elbow)', wrist_flex: 'var(--wristf)', wrist_roll: 'var(--wristr)', gripper: 'var(--grip)' };
const SPEEDS = [1, 2, 4, 6, 8, 12], STEPS = [0.5, 1, 2, 5];

export function mountJoints(panel, ctx) {
  const body = panel.querySelector('.pb'), tools = panel.querySelector('.ph .tools');
  let speed = pref.get('speed', null), step = pref.get('step', null), rows = {}, built = '';
  const spSel = h('select', { title: 'Speed of every move started from this page (°/s)', onchange: (e) => { speed = +e.target.value; pref.set('speed', speed); } },
    SPEEDS.map((v) => h('option', { value: v }, `${v} °/s`)));
  const stSel = h('select', { title: 'Jog step', onchange: (e) => { step = +e.target.value; pref.set('step', step); } },
    STEPS.map((v) => h('option', { value: v }, `step ${v}°`)));
  const poseBtn = h('button.sm', { title: 'Type or paste a joint pose (JSON) and go there', onclick: () => gotoPose() }, 'Go to pose…');
  tools.append(h('label.inline', 'speed', spSel), h('label.inline', 'jog', stSel), poseBtn);
  ctx.speed = () => speed;

  async function gotoPose() {
    const r = await dialog({ title: 'Go to pose', body: 'Joint angles in degrees (any subset). Every guard applies; poses beyond the soft limits use the lab envelope.', json: ctx.S?.present || {}, ok: 'Move', wide: true });
    if (r) ctx.api.cmd('goto_pose', { pose: r.value, speed });
  }
  const jog = (j, k) => ctx.api.cmd('jog', { joint: j, delta: k * step, speed }, { quiet: true });

  function row(j) {
    const val = h('b.num.val'), goal = h('span.num.dim.goalv'), track = h('div.track'), load = h('div.load', h('i'));
    const abs = h('input', { type: 'number', step: 0.5, title: 'absolute target (°), Enter to go',
      onkeydown: (e) => { if (e.key === 'Enter') go(); } });
    const go = () => { if (abs.value !== '') ctx.api.cmd('goto_pose', { pose: { [j]: +abs.value }, speed }); };
    const jb = (k, t) => h('button.sm', { onclick: () => jog(j, k), title: `${k > 0 ? '+' : '−'}${Math.abs(k)} × step` }, t);
    const el = h('div.jrow', { style: { '--jc': COLOR[j] } },
      h('div.jname', h('span.sw'), SHORT[j], h('div.faint.small', j)),
      h('div.jval', val, goal),
      track,
      h('div.jjog', jb(-5, '−5'), jb(-1, '−'), jb(1, '+'), jb(5, '+5')),
      h('div.jabs', abs, h('button.sm', { onclick: go }, 'Go')),
      load);
    return { el, val, goal, track, load, abs };
  }

  function drawTrack(r, J, marks) {
    const key = JSON.stringify([J.hard, J.soft, J.envelope, fix(J.present, 1), fix(J.goal, 1), marks]);
    if (r.sig === key) return; r.sig = key;
    const ranges = [J.hard, J.soft, J.envelope].filter(Boolean), vals = [J.present, J.goal, ...marks].filter((v) => v != null);
    if (!ranges.length && !vals.length) { r.track.replaceChildren(); return; }
    const lo = Math.min(...ranges.map((x) => x[0]), ...vals) - 2, hi = Math.max(...ranges.map((x) => x[1]), ...vals) + 2;
    const pct = (v) => `${((v - lo) / (hi - lo)) * 100}%`, span = (a, cls, t) => h('i.' + cls, { title: t, style: { left: pct(a[0]), width: `calc(${pct(a[1])} - ${pct(a[0])})` } });
    r.track.replaceChildren(...[
      J.hard ? span(J.hard, 'hard', `motor sweep ${fix(J.hard[0])}…${fix(J.hard[1])}° (hard ceiling)`) : null,
      J.soft ? span(J.soft, 'soft', `soft limit ${fix(J.soft[0])}…${fix(J.soft[1])}°`) : null,
      J.envelope ? span(J.envelope, 'env', `lab envelope ${fix(J.envelope[0])}…${fix(J.envelope[1])}°`) : null,
      ...marks.map((m) => h('i.mark', { style: { left: pct(m) }, title: `taught point ${fix(m)}°` })),
      J.goal != null ? h('i.goal', { style: { left: pct(J.goal) }, title: `goal ${fix(J.goal)}°` }) : null,
      J.present != null ? h('i.now', { style: { left: pct(J.present) }, title: `present ${fix(J.present)}°` }) : null,
      h('span.tl', fix(lo + 2, 0)), h('span.tr', fix(hi - 2, 0))].filter(Boolean));
  }

  return {
    update(S) {
      if (speed == null) speed = S.config?.effective?.motion?.speed || 4;
      if (step == null) step = S.config?.effective?.motion?.jog_step || 1;
      spSel.value = speed; stSel.value = step;
      const names = (S.joints || []).map((j) => j.name).join();
      if (names !== built) { built = names; rows = {}; body.replaceChildren(...S.joints.map((J) => (rows[J.name] = row(J.name)).el)); }
      const pts = (S.points || []).filter((p) => p.status !== 'deleted');
      for (const J of S.joints || []) {
        const r = rows[J.name];
        r.val.textContent = fix(J.present, 1) + (J.name === 'gripper' ? '' : '°');
        r.goal.textContent = J.goal != null && Math.abs(J.goal - (J.present ?? J.goal)) > 0.3 ? `→ ${fix(J.goal, 1)}` : '';
        if (document.activeElement !== r.abs && r.abs.placeholder !== fix(J.present, 1)) r.abs.placeholder = fix(J.present, 1);
        drawTrack(r, J, pts.map((p) => p.pose?.[J.name]).filter((v) => v != null));
        const f = J.load != null && J.load_limit ? Math.min(1, Math.abs(J.load) / J.load_limit) : 0;
        r.load.firstChild.style.width = `${f * 100}%`; r.load.className = 'load' + (f > 0.85 ? ' bad' : f > 0.6 ? ' warn' : '');
        if (J.name === 'gripper') {                                   // a mounted tool locks the gripper (the loop enforces it too)
          const T = S.tool?.mounted;
          r.el.querySelectorAll('.jjog button, .jabs button, .jabs input').forEach((b) => { b.disabled = !!T; });
          if (T) { r.goal.textContent = `🔒 ${T.name}`; r.goal.title = `locked on the ${T.name} (unmount it in the Tool tab)`; }
        }
        r.load.title = J.load_limit ? `load ${J.load ?? '—'} / guard ${J.load_limit}` : 'no load guard';
      }
    },
  };
}
