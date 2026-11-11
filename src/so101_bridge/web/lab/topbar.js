// Top bar: status chips and the safety cluster (STOP / RESUME / HOLD | torque on / free-drive). Esc = STOP.
import { h, fix } from './core.js';

export function mountTopbar(bar, ctx) {
  const chips = bar.querySelector('.chips'), safety = bar.querySelector('.safety');
  const chip = (title) => { const v = h('b'); const c = h('span.chip', { title }, h('span.dot'), v); c.v = v; chips.append(c); return c; };
  const link = chip('connection to the bridge'), mode = chip('control loop mode'), torque = chip('motor torque');
  const run = chip('routine / move in progress'), clr = chip('fingertip clearance above the table guard');
  const tl = chip('tool held in the gripper — click for the tool tab'); tl.onclick = () => ctx.openTab?.('tool');
  const pend = chip('pending changes — click to review'); pend.classList.add('click'); pend.onclick = () => ctx.focusPanel?.('changes');
  const rec = chip('video recording — click for the video tab'); rec.classList.add('click'); rec.onclick = () => ctx.openTab?.('video');
  const set = (c, text, cls, show = true) => { c.v.textContent = text; c.className = 'chip ' + (cls || '') + (c.onclick ? ' click' : ''); c.hidden = !show; };

  const stop = h('button.stop', { title: 'Emergency stop (Esc): freeze, abort, ignore commands until RESUME', onclick: () => ctx.api.cmd('stop') }, '■ STOP');
  const resume = h('button.go', { onclick: () => ctx.api.cmd('resume') }, 'Resume');
  const hold = h('button', { title: 'abort what runs and hold here', onclick: () => ctx.api.cmd('hold') }, 'Hold');
  const engage = h('button', { title: 'torque on, holding the present pose', onclick: () => ctx.api.cmd('engage') }, 'Torque on');
  const release = h('button.danger', { title: 'torque off: you hold the arm (asks first)', onclick: () => ctx.api.cmd('release') }, 'Free-drive…');
  safety.append(stop, resume, hold, h('span.sep'), engage, release);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !document.querySelector('dialog[open]')) { e.preventDefault(); ctx.api.cmd('stop'); } });

  return {
    health(ok, fails) { set(link, ok ? 'live' : fails > 2 ? 'offline' : 'retrying', ok ? 'ok' : fails > 2 ? 'bad' : 'warn'); document.body.classList.toggle('stale', !ok && fails > 2); },
    update(S) {
      document.body.classList.toggle('estop', !!S.estop);
      set(mode, S.estop ? 'ESTOP' : S.mode || '—', S.estop || S.mode === 'stalled' ? 'bad' : S.mode === 'moving' ? 'warn' : 'ok');
      set(torque, S.torque_on ? 'torque on' : 'free-drive', S.torque_on ? 'ok' : 'warn');
      set(run, S.routine || 'idle', S.busy ? 'warn' : '', S.busy || (S.routine && S.routine !== 'idle'));
      const c = S.guards?.floor?.clearance_cm;
      set(clr, c == null ? 'no table model' : `clear ${fix(c, 1)} cm`, c == null ? 'bad' : c <= 0 ? 'bad' : c < 2 ? 'warn' : 'ok');
      const T = S.tool?.mounted; set(tl, T ? `${T.name} · ${fix(T.out_cm, 1)} cm out` : '', 'tool', !!T);
      const n = S.config?.pending?.length || 0; set(pend, `${n} pending`, 'warn', n > 0);
      const r = S.video?.recording; set(rec, r ? `REC ${fix(r.seconds, 0)} s` : '', 'rec', !!r);
      resume.disabled = !S.estop; release.disabled = !S.torque_on; engage.disabled = !!S.torque_on && !S.estop;
    },
  };
}
