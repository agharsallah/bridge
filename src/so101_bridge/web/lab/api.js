// API panel: what a script, a model or an LLM tool can call — every action with its parameters (from
// /<lab>/actions), the endpoints, and a live preview of /<lab>/observe.
import { h, copy } from './core.js';

export function mountApi(el, ctx) {
  const p = ctx.prefix, base = location.origin + p;
  const list = h('div'), obs = h('pre.obs.small', 'press "Preview" to fetch an observation');
  const ep = [['GET', '/state', 'everything this page draws'], ['GET', '/observe', 'compact observation: pose, fingertip, table clearance, points, cameras'],
    ['GET', '/observe?images=1', '… plus one JPEG per camera (base64), for a vision model'], ['GET', '/actions', 'the action list below, as JSON (tool definitions)'],
    ['POST', '/cmd', '{"action": name, ...params} → {ok, error?, message?, needs_confirm?}'], ['GET', '/cam/<name>.jpg?w=&q=', 'one raw frame'],
    ['GET', '/cam/<name>.mjpg?w=&q=&fps=', 'raw MJPEG stream (new frames only)']];
  el.append(
    h('p.dim.small', 'Everything on this page goes through these endpoints, so a script or an agent gets the same guards and the same staging. ',
      'Actions marked "confirm" are refused until repeated with "confirm": "yes": an agent has to ask a human first.'),
    h('table.t', ep.map(([m, path, d]) => h('tr', h('td', h('span.badge', m)), h('td.mono.small', p + path), h('td.dim.small', d),
      h('td.acts', h('button.xs', { onclick: () => copy(m === 'POST' ? `curl -s -X POST ${base}/cmd -H 'Content-Type: application/json' -d '{"action":"hold"}'` : `curl -s '${base}${path}'`, 'curl copied') }, 'curl'))))),
    h('h4.sub', 'Actions'), list,
    h('h4.sub', h('span', 'Observation '), h('button.sm', { onclick: async () => { obs.textContent = JSON.stringify(await ctx.api.get('/observe'), null, 2); } }, 'Preview'),
      h('button.sm', { onclick: async () => copy(JSON.stringify(await ctx.api.get('/observe'), null, 2), 'observation copied') }, 'Copy')), obs);

  let loaded = false;
  return {
    async update() {
      if (loaded || el.offsetParent === null) return; loaded = true;
      const acts = await ctx.api.get('/actions');
      const groups = {}; acts.forEach((a) => (groups[a.group] ||= []).push(a));
      list.replaceChildren(...Object.entries(groups).map(([g, as]) => h('div.agroup', h('div.dim.small.gname', g),
        h('table.t', as.map((a) => h('tr', h('td.mono', a.name), h('td.small', a.description),
          h('td.mono.small.dim', Object.entries(a.params).map(([k, t]) => `${k}: ${t}`).join(', ') || '—'),
          h('td', a.moves ? h('span.badge.pending', 'moves') : null, a.confirm ? h('span.badge.deleted', { title: a.confirm }, 'confirm') : null),
          h('td.acts', h('button.xs', { onclick: () => copy(JSON.stringify({ action: a.name, ...Object.fromEntries(Object.keys(a.params).map((k) => [k, null])) }), 'payload copied') }, 'payload'))))))));
    },
  };
}
