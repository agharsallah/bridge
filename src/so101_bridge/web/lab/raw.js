// Raw state explorer: measurements, points, config (saved / effective), pending, the full /<lab>/state.
// A collapsible tree with a filter; every node can be copied as JSON or by its path, poses also as a
// goal command (var/cmd/*.json) or a Python dict. "Freeze" stops the refresh while you read or copy.
import { h, copy, isPose, pref, SHORT } from './core.js';

const VIEWS = {
  measurements: (S) => ({ present: S.present, goal: S.goal, tip: S.side?.tip, goal_tip: S.side?.goal_tip, clearance_cm: S.guards?.floor?.clearance_cm,
    joints: Object.fromEntries((S.joints || []).map((J) => [J.name, { present: J.present, goal: J.goal, load: J.load, soft: J.soft, hard: J.hard, envelope: J.envelope }])),
    mode: S.mode, torque_on: S.torque_on, estop: S.estop, routine: S.routine, time: S.time }),
  points: (S) => Object.fromEntries((S.points || []).map((p) => [p.name, p])),
  rest: (S) => S.rest,
  'config (saved)': (S) => S.config?.saved,
  'config (effective)': (S) => S.config?.effective,
  pending: (S) => S.config?.pending,
  cameras: (S) => S.cameras,
  'full state': (S) => S,
};
const py = (o) => '{' + Object.entries(o).map(([k, v]) => `"${k}": ${v}`).join(', ') + '}';

export function mountRaw(el, ctx) {
  let view = pref.get('raw.view', 'measurements'), frozen = false, filter = '', last = null, hovering = false;
  const open = new Set(pref.get('raw.open', ['$', '$.present', '$.joints']));
  const tree = h('div.tree', { onmouseenter: () => { hovering = true; }, onmouseleave: () => { hovering = false; } });
  const tabs = h('div.tabs', Object.keys(VIEWS).map((k) => h('button.sm', { dataset: { v: k }, onclick: () => { view = k; pref.set('raw.view', k); sync(); draw(true); } }, k)));
  const freeze = h('button.sm', { onclick: () => { frozen = !frozen; sync(); if (!frozen) draw(true); } });
  const search = h('input', { type: 'search', placeholder: 'filter keys…', oninput: (e) => { filter = e.target.value.toLowerCase(); draw(true); } });
  const expand = (all) => { walk(VIEWS[view](ctx.S), '$', (p) => (all ? open.add(p) : p !== '$' && open.delete(p))); pref.set('raw.open', [...open]); draw(true); };
  el.append(h('div.row', tabs), h('div.row', { style: { margin: '8px 0' } }, search, freeze,
    h('button.sm', { onclick: () => expand(true) }, 'Expand all'), h('button.sm', { onclick: () => expand(false) }, 'Collapse'),
    h('button.sm', { onclick: () => copy(JSON.stringify(VIEWS[view](ctx.S), null, 2), `${view} copied`) }, `Copy ${view}`),
    h('span.dim.small.right', 'refresh pauses while the pointer is over the tree')), tree);
  function sync() { freeze.textContent = frozen ? '▶ Live' : '⏸ Freeze'; freeze.classList.toggle('primary', frozen); tabs.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.v === view)); }

  function walk(v, path, fn) { if (v && typeof v === 'object') { fn(path); for (const [k, c] of Object.entries(v)) walk(c, `${path}.${k}`, fn); } }
  const matches = (v, key) => !filter || key.toLowerCase().includes(filter) || (v && typeof v === 'object' && Object.entries(v).some(([k, c]) => matches(c, k)));

  function node(key, v, path) {
    if (!matches(v, String(key))) return null;
    const isObj = v && typeof v === 'object', n = isObj ? Object.keys(v).length : 0;
    const acts = h('span.nacts',
      h('button.xs', { title: 'copy value as JSON', onclick: (e) => { e.stopPropagation(); copy(JSON.stringify(v, null, isObj ? 2 : 0), `${path} copied`); } }, 'json'),
      h('button.xs', { title: 'copy the path', onclick: (e) => { e.stopPropagation(); copy(path.replace(/^\$\.?/, ''), 'path copied'); } }, 'path'),
      isPose(v) ? h('button.xs', { title: 'copy as a goal command file for var/cmd/', onclick: () => copy(JSON.stringify({ action: 'goal', goal: v, speed: 4 }), 'goal command copied') }, 'cmd') : null,
      isPose(v) ? h('button.xs', { title: 'copy as a Python dict', onclick: () => copy(py(v), 'python dict copied') }, 'py') : null);
    if (!isObj) {
      const cls = v == null ? 'null' : typeof v;
      return h('div.leaf', h('span.k', key), h('span.v.' + cls, v == null ? 'null' : typeof v === 'string' ? `"${v}"` : String(v)), acts);
    }
    const isOpen = open.has(path) || !!filter;
    const head = h('div.br', { onclick: () => { open.has(path) ? open.delete(path) : open.add(path); pref.set('raw.open', [...open]); draw(true); } },
      h('span.tw', isOpen ? '▾' : '▸'), h('span.k', key), h('span.dim.small', Array.isArray(v) ? ` [${n}]` : ` {${n}}`),
      !isOpen && isPose(v) ? h('span.v.small.dim', ' ' + Object.entries(v).map(([k, x]) => `${SHORT[k] || k} ${x}`).join(' ')) : null, acts);
    return h('div.node', head, isOpen ? h('div.kids', Object.entries(v).map(([k, c]) => node(k, c, `${path}.${k}`))) : null);
  }

  function draw(force) {
    if (!ctx.S || (!force && (frozen || hovering || el.offsetParent === null))) return;   // never move a button under the cursor
    const data = VIEWS[view](ctx.S), s = JSON.stringify(data);
    if (!force && s === last) return; last = s;
    const top = tree.scrollTop;
    tree.replaceChildren(data == null ? h('div.empty', 'nothing here') : node(view, data, '$')); tree.scrollTop = top;
  }
  sync();
  return { update() { draw(false); }, redraw: () => draw(true) };
}
