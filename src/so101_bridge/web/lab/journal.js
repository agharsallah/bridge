// Journal: what was done in this lab, newest first (GET /<lab>/journal). Refreshes only while visible.
import { h, renderIf, copy } from './core.js';

export function mountJournal(el, ctx) {
  let onlyErrors = false, last = 0;
  const list = h('div.scroll');
  const errBtn = h('button.sm', { onclick: () => { onlyErrors = !onlyErrors; errBtn.classList.toggle('primary', onlyErrors); last = 0; } }, 'Only refused');
  el.append(h('div.row', { style: { marginBottom: '8px' } }, errBtn,
    h('button.sm', { onclick: async () => copy(JSON.stringify(await ctx.api.get('/journal?n=500'), null, 1), 'journal copied') }, 'Copy last 500'),
    h('span.dim.small', 'Every action run from this page, a script or an agent. Kept in var/lab/<lab>.journal.jsonl; the last 10 are in /observe.')), list);

  const rows = (es) => (es.length ? h('table.t', h('tr', h('th', 'time'), h('th', 'action'), h('th', 'args'), h('th', 'result')),
    es.map((e) => h('tr', h('td.num.dim.small', e.time.slice(11)), h('td.mono', e.action),
      h('td.mono.small.dim', Object.keys(e.args || {}).length ? JSON.stringify(e.args) : '—'),
      h('td.small' + (e.ok ? '' : '.bad'), e.ok ? (e.message || 'ok') : e.error))))
    : h('div.empty', onlyErrors ? 'Nothing refused.' : 'No actions yet.'));

  return {
    async update() {
      if (el.offsetParent === null || Date.now() - last < 1500) return;
      last = Date.now();
      const es = (await ctx.api.get('/journal?n=200')).reverse().filter((e) => !onlyErrors || !e.ok);
      renderIf(list, 'j', es, rows);
    },
  };
}
