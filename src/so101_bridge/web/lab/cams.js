// Camera panel: raw MJPEG streams from /<lab>/cam/<name>.mjpg — focus + thumbnails or grid,
// per-viewer quality, pause, fullscreen, snapshot, stale/offline overlay, auto-reconnect; which device each
// camera uses (swap, assign by hand) and what the arm-motion identity check concluded.
import { h, fix, pref, dialog, renderIf } from './core.js';

// "Innomaker-U20CAM-1080p-S1#1" -> "Innomaker #1"; an int index -> "device 1"
const short = (sel) => (typeof sel === 'number' ? `device ${sel}` : String(sel ?? '').replace(/^(.*?)[-\s].*#/, '$1 #'));
const port = (uid) => (/^0x[0-9a-f]+$/i.test(uid) ? `USB port ${uid.slice(0, 5)}` : 'built-in / virtual');
// which listed device a selector points at (same rule as camera_ids.resolve)
function pick(devs, sel) {
  if (typeof sel === 'number') return devs.find((d) => d.index === sel);
  const i = String(sel ?? '').lastIndexOf('#'); if (i < 0) return undefined;
  const model = sel.slice(0, i).toLowerCase(), n = +sel.slice(i + 1);
  return devs.filter((d) => d.name.toLowerCase().includes(model))[n];
}

const QUALITY = { low: { w: 640, q: 60 }, med: { w: 960, q: 70 }, high: { w: 1280, q: 80 }, full: { w: 1920, q: 85 } };

export function mountCams(panel, ctx) {
  const body = panel.querySelector('.pb'), tools = panel.querySelector('.ph .tools');
  let names = [], tiles = {}, info = {};
  let layout = pref.get('cam.layout', 'focus'), focus = pref.get('cam.focus', null);
  let qual = pref.get('cam.quality', 'med'), paused = pref.get('cam.paused', false), fps = 10;

  const url = (n, thumb) => {
    const q = QUALITY[thumb ? 'low' : qual];
    return `${ctx.prefix}/cam/${encodeURIComponent(n)}.mjpg?w=${q.w}&q=${q.q}&fps=${thumb ? 5 : fps}&t=${Date.now()}`;
  };
  const isThumb = (n) => layout === 'focus' && n !== focus;
  const connect = (n) => {
    const t = tiles[n]; if (!t) return;
    t.img.src = paused ? '' : url(n, isThumb(n)); t.errors = 0;
  };
  const reconnect = () => names.forEach(connect);

  function tile(n) {
    const img = h('img', { alt: `${n} camera`, decoding: 'async' });
    const tag = h('div.tag'), over = h('div.over');
    const wrap = h('div.cam', { dataset: { cam: n } }, img, over, tag,
      h('div.camtools',
        h('button.xs', { title: 'Show this camera large', onclick: () => setFocus(n) }, '⤢'),
        h('button.xs', { title: 'Full screen', onclick: () => wrap.requestFullscreen?.() }, '⛶'),
        h('button.xs', { title: 'Full-resolution snapshot (new tab)', onclick: () => window.open(`${ctx.prefix}/cam/${n}.jpg?w=1920&q=92&t=${Date.now()}`, '_blank') }, '📷')));
    img.addEventListener('error', () => {            // the stream died (daemon restart, USB drop): back off and retry
      const t = tiles[n]; if (!t || paused) return;
      t.errors = (t.errors || 0) + 1; setTimeout(() => connect(n), Math.min(10000, 1000 * t.errors));
    });
    img.addEventListener('dblclick', () => setFocus(n));
    return { wrap, img, tag, over, errors: 0 };
  }

  function arrange() {
    body.replaceChildren();
    const grid = h('div.cams.' + layout);
    if (layout === 'focus') {
      if (!names.includes(focus)) focus = names[0];
      grid.append(tiles[focus].wrap, h('div.thumbs', names.filter((n) => n !== focus).map((n) => tiles[n].wrap)));
    } else grid.append(...names.map((n) => tiles[n].wrap));
    body.append(grid); names.forEach((n) => tiles[n].wrap.classList.toggle('big', n === focus && layout === 'focus'));
    reconnect();
  }
  function setFocus(n) { focus = n; layout = 'focus'; pref.set('cam.focus', n); pref.set('cam.layout', layout); syncTools(); arrange(); }

  const sel = h('select', { title: 'Stream quality for this browser (the cameras capture at full size either way)',
    onchange: (e) => { qual = e.target.value; pref.set('cam.quality', qual); reconnect(); } },
    Object.entries(QUALITY).map(([k, v]) => h('option', { value: k }, `${k} · ${v.w}px`)));
  const lay = h('button.sm', { onclick: () => { layout = layout === 'focus' ? 'grid' : 'focus'; pref.set('cam.layout', layout); syncTools(); arrange(); } });
  const pause = h('button.sm', { onclick: () => { paused = !paused; pref.set('cam.paused', paused); syncTools(); reconnect(); } });
  function syncTools() { sel.value = qual; lay.textContent = layout === 'focus' ? 'Grid' : 'Focus'; pause.textContent = paused ? '▶ Resume streams' : '⏸ Pause'; }
  const swap = h('button.sm', { hidden: true, title: 'A replug can bring two identical cameras back in the other order: swap them (saved to settings)',
    onclick: () => ctx.api.cmd('cameras_swap', { a: names[0], b: names[1] }) }, '⇄ Swap');
  const assignBtn = h('button.sm', { title: 'choose which device each camera uses', onclick: () => assignDialog() }, 'Assign…');
  tools.append(sel, lay, pause, swap, assignBtn); syncTools();
  const note = h('div'); panel.insertBefore(note, body);      // what the identity check says (asks to pick when unsure)

  async function assignDialog() {
    const r = await ctx.api.cmd('cameras_devices', {}, { quiet: true }); if (!r.ok) return;
    const devs = r.devices || [], cams = r.cams || {};
    const rows = Object.keys(cams).map((n) => {
      const cur = pick(devs, cams[n]);
      const s = h('select', cur ? null : h('option', { value: JSON.stringify(cams[n]) }, `${short(cams[n])} (not connected)`),
        devs.map((d) => h('option', { value: JSON.stringify(d.selector), selected: d === cur }, `${d.index} · ${d.name} · ${port(d.uid)}`)));
      return { n, s, now: cur ? `now ${short(cams[n])}, ${port(cur.uid)}` : 'now not connected' };
    });
    const auto = h('input', { type: 'checkbox', checked: !!r.auto });
    const body = h('div',
      h('p.small.dim', 'Identical cameras have the same name: tell them apart by the tiles (each shows its device), or by its USB port. ',
        `"${r.on_arm}" must be the camera riding the arm.`),
      h('table.t', rows.map((x) => h('tr', h('td', h('b', x.n), x.n === r.on_arm ? h('div.dim.small', 'on the arm') : null), h('td', x.s), h('td.dim.small', x.now)))),
      h('label.inline', { style: { marginTop: '10px' } }, auto, `after a reconnect, check by arm motion that "${r.on_arm}" is the camera on the arm (swaps two identical cameras if not)`));
    if (!(await dialog({ title: 'Assign cameras', body, ok: 'Assign', wide: true }))) return;
    ctx.api.cmd('cameras_assign', { assign: Object.fromEntries(rows.map((x) => [x.n, JSON.parse(x.s.value)])), auto: auto.checked });
  }

  function identity(A) {
    const id = A.identity || {}, recent = id.t && Date.now() / 1000 - id.t < 20;
    if (id.state === 'unsure' || id.state === 'single') return h('div.recbar.row', h('span.warn', '⚠ ', id.message), h('button.sm.primary', { onclick: assignDialog }, 'Pick by hand…'));
    if (id.state === 'checking') return h('div.dim.small', { style: { margin: '0 0 8px' } }, '⟳ ', id.message);
    if (id.state === 'swapped' && recent) return h('div.small.ok', { style: { margin: '0 0 8px' } }, '⇄ ', id.message);
    return null;
  }

  return {
    reconnect,
    update(S) {
      const list = S.cameras?.list || [], A = S.cameras?.assign || {};
      renderIf(note, 'ident', { s: A.identity?.state, m: A.identity?.message, r: A.identity?.t && Date.now() / 1000 - A.identity.t < 20 }, () => identity(A));
      fps = S.cameras?.stream?.fps || 10;
      const now = list.map((c) => c.name);
      if (now.join() !== names.join()) {
        names = now; tiles = Object.fromEntries(names.map((n) => [n, tiles[n] || tile(n)]));
        swap.hidden = names.length < 2; swap.textContent = `⇄ Swap ${names[0]}/${names[1]}`;
        if (!names.length) { body.replaceChildren(h('div.empty', 'No cameras configured (settings.CAMS).')); return; }
        arrange();
      }
      for (const c of list) {
        const t = tiles[c.name]; info[c.name] = c;
        const size = c.size ? `${c.size[0]}×${c.size[1]}` : '—';
        const dev = A.cams?.[c.name] != null ? ` · ${short(A.cams[c.name])}` : '';
        t.tag.textContent = `${c.name}${dev} · ${size} · ${c.stream_fps ? fix(c.stream_fps, 0) + ' fps' : 'idle'}`;
        const msg = paused ? 'paused' : !c.live ? (c.age_s == null ? 'no frames yet' : `stale · last frame ${fix(c.age_s, 1)} s ago`) : '';
        t.over.textContent = msg; t.wrap.classList.toggle('stale', !!msg);
      }
    },
  };
}
