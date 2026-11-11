// Lab page wiring. A page declares its panels in HTML (data-panel="name"); startLab mounts each one from
// the registry, polls /<lab>/state and hands every panel the same snapshot. A lab adds its own panels
// without touching HTML through its `ui` module (export const panels = [{id, title, mount}]) — docs/lab.md.
import { $, $$, h, makeApi, poll, pref, toast } from './core.js';
import { mountTopbar } from './topbar.js';
import { mountCams } from './cams.js';
import { mountSide } from './side.js';
import { mountJoints } from './joints.js';
import { mountGuards } from './guards.js';
import { mountChanges } from './changes.js';
import { mountPoints } from './points.js';
import { mountEnvelope } from './envelope.js';
import { mountVideo } from './video.js';
import { mountRaw } from './raw.js';
import { mountApi } from './api.js';
import { mountTool } from './tool.js';
import { mountJournal } from './journal.js';

export const PANELS = { cams: mountCams, side: mountSide, joints: mountJoints, guards: mountGuards, changes: mountChanges,
  points: mountPoints, envelope: mountEnvelope, tool: mountTool, video: mountVideo, raw: mountRaw, api: mountApi, journal: mountJournal };

export function startLab({ prefix = location.pathname.replace(/\/$/, ''), panels = {}, every = 400 } = {}) {
  const api = makeApi(prefix);
  const ctx = { api, prefix, S: null };
  const registry = { ...PANELS, ...panels };
  const mounted = [];

  // tabs: <div class=tabs><button data-tab=x>  +  <div class=tabpane data-pane=x>
  const openTab = (name, remember = true) => {
    $$('[data-tab]').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
    $$('[data-pane]').forEach((p) => { p.hidden = p.dataset.pane !== name; });
    if (remember) pref.set(prefix + '.tab', name);
    mounted.forEach((m) => m.update && ctx.S && m.update(ctx.S));
  };
  const mountOne = (el, mount) => {
    try { const m = mount(el, ctx); if (m) { mounted.push(m); if (ctx.S) m.update?.(ctx.S); } } catch (e) { console.error(el.dataset.panel, e); }
  };
  // a lab's own panel as an extra workbench tab (what the `ui` module's panels become)
  ctx.addTab = (id, title, mount, { first = false } = {}) => {
    const tabs = $('.workbench .tabs'), wb = $('.workbench');
    const btn = h('button', { dataset: { tab: id }, onclick: () => openTab(id) }, title);
    const pane = h('div.tabpane', { dataset: { pane: id, panel: id }, hidden: true });
    if (first) tabs.prepend(btn); else tabs.append(btn);
    wb.append(pane); mountOne(pane, mount); return pane;
  };
  $$('[data-tab]').forEach((b) => b.addEventListener('click', () => openTab(b.dataset.tab)));
  ctx.openTab = (name) => { openTab(name); $(`[data-pane="${name}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); };
  ctx.focusPanel = (name) => $(`[data-panel="${name}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const top = mountTopbar($('header.top'), ctx);
  for (const el of $$('[data-panel]')) {
    const mount = registry[el.dataset.panel];
    if (!mount) { console.warn('no panel', el.dataset.panel); continue; }
    mountOne(el, mount);
  }
  const firstTab = $('[data-tab]')?.dataset.tab;
  if (firstTab) openTab(pref.get(prefix + '.tab', firstTab), false);
  navLinks(prefix);

  let extended = false;
  async function extend(info) {                         // once: the lab's title and its own `ui` module
    extended = true;
    document.title = `SO-101 ${info.title}`;
    const t = $('.brand small'); if (t) t.textContent = info.title;
    if (!info.module) return;
    try {
      const mod = await import(info.module);
      for (const p of mod.panels || []) ctx.addTab(p.id, p.title, p.mount, p);
      mod.init?.(ctx);
      openTab(pref.get(prefix + '.tab', (mod.panels || [])[0]?.id || firstTab), false);
    } catch (e) { console.error(e); toast(`${info.module}: ${e.message}`, 'err', 8000); }
  }

  api.onCommand = () => setTimeout(tick, 120);         // show the effect of a click without waiting a full period
  let wasDown = false;
  async function tick() {
    const S = await api.state(); ctx.S = S;
    if (!extended && S.lab) extend(S.lab);
    top.update(S);
    for (const m of mounted) { try { m.update?.(S); } catch (e) { console.error(e); } }   // one broken panel never blanks the page
  }
  poll(tick, every, (ok, fails) => {
    top.health(ok, fails);
    if (!ok && fails === 3) { wasDown = true; toast('Lost the bridge: retrying…', 'err'); }
    if (ok && wasDown) { wasDown = false; toast('Bridge is back'); mounted.forEach((m) => m.reconnect?.()); }
  });
  return ctx;
}

// every registered lab gets a link next to the fixed pages
async function navLinks(prefix) {
  const nav = $('nav.pages'); if (!nav) return;
  try {
    const labs = await (await fetch('/labs')).json();
    for (const l of labs) if (!nav.querySelector(`a[href="${l.prefix}"]`)) nav.append(h('a', { href: l.prefix, title: l.description || l.title }, l.name));
  } catch { /* the fixed links still work */ }
  $$('nav.pages a').forEach((a) => a.classList.toggle('on', a.getAttribute('href') === prefix));
}
