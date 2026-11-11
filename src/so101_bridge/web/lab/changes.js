// Pending changes: every staged edit with a readable diff; edit its value, apply or discard it
// one by one or all together. Nothing reaches config/<lab>.json before "Apply".
import { h, fix, renderIf, dialog, confirmBox, isPose, JOINTS, SHORT } from './core.js';

const poseOf = (v) => (isPose(v) ? v : isPose(v?.pose) ? v.pose : null);
const short = (v) => { const s = JSON.stringify(v); return s == null ? '∅' : s.length > 90 ? s.slice(0, 87) + '…' : s; };

function diff(c) {
  const a = poseOf(c.before), b = c.op === 'delete' ? null : poseOf(c.value);
  if (a || b) {
    const js = JOINTS.filter((j) => (a && a[j] != null) || (b && b[j] != null));
    const t = h('table.t.diff', h('tr', h('th', ''), js.map((j) => h('th', SHORT[j]))),
      a ? h('tr', h('td.dim', 'before'), js.map((j) => h('td.num.dim', fix(a[j], 1)))) : null,
      b ? h('tr', h('td.dim', 'after'), js.map((j) => h('td.num', fix(b[j], 1)))) : null,
      a && b ? h('tr', h('td.dim', 'Δ'), js.map((j) => { const d = (b[j] ?? 0) - (a[j] ?? 0); return h('td.num' + (Math.abs(d) > 5 ? '.warn' : ''), a[j] == null || b[j] == null ? '—' : (d > 0 ? '+' : '') + fix(d, 1)); })) : null);
    const meta = ['role', 'note'].filter((k) => c.value && typeof c.value === 'object' && (c.before?.[k] ?? '') !== (c.value?.[k] ?? ''))
      .map((k) => h('div.small', h('span.dim', k + ': '), h('s.dim', c.before?.[k] || '∅'), ' → ', c.value[k] || '∅'));
    return [t, ...meta];
  }
  return h('div.mono.small.kvdiff', c.is_new ? null : h('div', h('span.dim', 'before '), short(c.before)),
    h('div', h('span.dim', c.op === 'delete' ? 'after  ' : 'after  '), c.op === 'delete' ? h('span.bad', 'deleted') : short(c.value)));
}

export function mountChanges(panel, ctx) {
  const body = panel.querySelector('.pb'), count = panel.querySelector('.count'), foot = panel.querySelector('.pfoot');
  const applyAll = h('button.primary', { onclick: () => ctx.api.cmd('changes_apply') }, 'Apply all');
  const discardAll = h('button', {
    onclick: async () => { if (await confirmBox('Discard all changes', 'Drop every pending change? The saved config stays as it is.', 'Discard all')) ctx.api.cmd('changes_discard'); },
  }, 'Discard all');
  const file = h('span.dim.small.grow');
  foot.append(file, discardAll, applyAll);

  const edit = async (c) => {
    const r = await dialog({ title: `Edit ${c.path}`, body: c.label || 'Pending value (JSON).', json: c.value, ok: 'Update', wide: true });
    if (r) ctx.api.cmd('change_edit', { id: c.id, value: r.value });
  };

  function list({ pending, stale }) {
    if (!pending.length) return h('div.empty', 'No pending changes. Points, rest, envelope and settings you edit are staged here first.');
    return pending.map((c) => h('div.change' + (stale.includes(c.id) ? '.stale' : ''),
      h('div.row', h('span.badge.' + (c.is_new ? 'new' : c.op), c.is_new ? 'new' : c.op === 'delete' ? 'delete' : 'edit'),
        h('code', c.path), h('span.dim.small.grow', c.label), h('span.faint.small', c.time)),
      stale.includes(c.id) ? h('div.warn.small', 'The saved file changed since this was staged: check the values before applying.') : null,
      diff(c),
      h('div.row.cacts', c.op !== 'delete' ? h('button.xs', { onclick: () => edit(c) }, 'Edit value') : null,
        h('button.xs', { onclick: () => ctx.api.cmd('changes_discard', { ids: [c.id] }) }, 'Discard'),
        h('button.xs.go', { onclick: () => ctx.api.cmd('changes_apply', { ids: [c.id] }) }, 'Apply this'))));
  }

  return {
    update(S) {
      const cfg = S.config || { pending: [], stale: [] };
      const n = cfg.pending.length;
      count.textContent = n ? String(n) : ''; panel.classList.toggle('has-pending', n > 0);
      applyAll.disabled = discardAll.disabled = !n;
      file.textContent = `saves to ${cfg.file || '?'}`;
      renderIf(body, 'chg', { pending: cfg.pending, stale: cfg.stale || [] }, list);
    },
  };
}
