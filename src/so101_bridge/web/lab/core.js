// Shared lab front-end core: DOM helper, API client (with confirm round-trip), toasts, dialogs, copy.
// Every lab page imports this; panels get a `ctx` = {api, prefix, ui} and expose update(S).

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// h('button.sm.go', {onclick, title}, 'text', child) — small hyperscript; null/false children are skipped
export function h(tag, attrs, ...kids) {
  const [name, ...cls] = tag.split('.');
  const el = document.createElement(name || 'div');
  if (cls.length) el.className = cls.join(' ');
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) { kids.unshift(attrs); attrs = null; }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) el.append(k instanceof Node ? k : String(k));
  return el;
}

export const fix = (v, n = 1) => (v == null || Number.isNaN(+v) ? '—' : (+v).toFixed(n));
export const sig = (o) => JSON.stringify(o);
export const isPose = (o) => o && typeof o === 'object' && !Array.isArray(o) && 'shoulder_pan' in o;
export const JOINTS = ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper'];
export const SHORT = { shoulder_pan: 'pan', shoulder_lift: 'lift', elbow_flex: 'elbow', wrist_flex: 'wrist', wrist_roll: 'roll', gripper: 'grip' };

// replace children only when the data signature changed and nothing inside is being edited
export function renderIf(el, key, data, build) {
  const s = sig(data);
  if (el._sig === s || el.contains(document.activeElement) && document.activeElement.matches('input,textarea,select')) return false;
  el._sig = s; el.replaceChildren(...[build(data)].flat().filter(Boolean)); return true;
}

// ------------------------------------------------------------------ toasts
export function toast(msg, kind = 'ok', ms = 3200) {
  let box = $('#toasts');
  if (!box) { box = h('div', { id: 'toasts', 'aria-live': 'polite' }); document.body.append(box); }
  const t = h('div.toast.' + kind, { role: kind === 'err' ? 'alert' : 'status' }, msg);
  box.append(t); setTimeout(() => t.classList.add('out'), ms); setTimeout(() => t.remove(), ms + 400);
}

// ------------------------------------------------------------------ dialogs (no native confirm/prompt)
export function dialog({ title, body, ok = 'OK', danger = false, cancel = 'Cancel', input, json, wide = false }) {
  return new Promise((resolve) => {
    const field = json !== undefined ? h('textarea.json', { spellcheck: false, rows: 14 }, JSON.stringify(json, null, 2))
      : input !== undefined ? h('input', { value: input, type: 'text' }) : null;
    const err = h('div.err');
    const close = (v) => { d.close(); d.remove(); resolve(v); };
    const submit = () => {
      if (json !== undefined) {
        try { close({ value: JSON.parse(field.value) }); } catch (e) { err.textContent = 'Invalid JSON: ' + e.message; }
      } else close(input !== undefined ? { value: field.value } : true);
    };
    const d = h('dialog.modal' + (wide ? '.wide' : ''), { onclose: () => resolve(null) },
      h('h3', title), body ? h('div.mbody', typeof body === 'string' ? h('p', body) : body) : null, field, err,
      h('div.mfoot', h('button', { onclick: () => close(null) }, cancel),
        h('button.' + (danger ? 'danger' : 'primary'), { onclick: submit }, ok)));
    d.addEventListener('keydown', (e) => { if (e.key === 'Enter' && field && field.tagName === 'INPUT') submit(); });
    document.body.append(d); d.showModal();
    (field || d.querySelector(danger ? '.mfoot button' : 'button.primary')).focus();   // a dangerous dialog starts on Cancel: Enter never confirms it
  });
}
export const confirmBox = (title, body, ok = 'Continue', danger = true) => dialog({ title, body, ok, danger });

// ------------------------------------------------------------------ clipboard
export async function copy(text, what = 'copied') {
  try { await navigator.clipboard.writeText(text); }
  catch { const t = h('textarea', { style: { position: 'fixed', opacity: 0 } }, text); document.body.append(t); t.select(); document.execCommand('copy'); t.remove(); }
  toast(`${what} ✓`, 'ok', 1500);
}

// ------------------------------------------------------------------ API client
export function makeApi(prefix) {
  const get = async (path, ms = 4000) => {
    const ac = new AbortController(); const t = setTimeout(() => ac.abort(), ms);
    try { const r = await fetch(prefix + path, { signal: ac.signal, cache: 'no-store' }); return await r.json(); }
    finally { clearTimeout(t); }
  };
  // cmd('jog', {joint, delta}) — shows the server's confirm question in a dialog and repeats with confirm=yes
  const cmd = async (action, args = {}, { quiet = false } = {}) => {
    let res;
    try {
      const r = await fetch(prefix + '/cmd', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, ...args }) });
      res = await r.json();
    } catch (e) { toast(`${action}: no answer from the bridge (${e.message})`, 'err'); return { ok: false, error: e.message }; }
    if (res.needs_confirm) {
      if (!(await confirmBox(action.replace(/_/g, ' '), res.needs_confirm, 'Confirm'))) return { ok: false, cancelled: true };
      return cmd(action, { ...args, confirm: 'yes' }, { quiet });
    }
    if (!res.ok) toast(`${action}: ${res.error}`, 'err', 6000);
    else if (!quiet && res.message) toast(res.message);
    api.onCommand?.(res);
    return res;
  };
  const api = { prefix, get, cmd, state: () => get('/state') };
  return api;
}

// ------------------------------------------------------------------ polling with link health
export function poll(fn, ms, onHealth) {
  let fails = 0, busy = false;
  const tick = async () => {
    if (busy || document.hidden) return; busy = true;
    try { await fn(); fails = 0; onHealth?.(true, 0); } catch { fails++; onHealth?.(false, fails); }
    finally { busy = false; }
  };
  tick(); return setInterval(tick, ms);
}

// per-viewer conveniences only (remembered tab, speed…); never state that must persist
export const pref = {
  get(k, d) { try { const v = localStorage.getItem('lab.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('lab.' + k, JSON.stringify(v)); } catch { /* private window */ } },
};
