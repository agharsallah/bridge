// Video: record the cameras into the lab's folder (config: video.dir, video.raw), see exactly where the
// clip goes, play / download / delete the clips in that folder.
import { h, fix, renderIf, dialog, pref } from './core.js';

export function mountVideo(el, ctx) {
  const name = h('input', { type: 'text', placeholder: 'clip name', value: pref.get('clip', ''), maxlength: 40, style: { width: '180px' } });
  const status = h('div.recstatus'), settings = h('div'), list = h('div.scroll');
  const start = () => { pref.set('clip', name.value); ctx.api.cmd('video_start', { name: name.value || undefined }); };
  el.append(status, h('h4.sub', 'Where clips go'), settings, h('h4.sub', 'Clips in this folder'), list);

  const play = (f) => {
    const src = `${ctx.prefix}/videos/${encodeURIComponent(f.file)}`;
    dialog({ title: f.file, wide: true, ok: 'Close', cancel: 'Back',
      body: h('div', h('video', { src, controls: true, autoplay: true, style: { width: '100%', borderRadius: '8px' } }),
        h('div.small', h('a', { href: src, download: f.file }, 'Download'), h('span.dim', ` · ${ctx.S?.video?.dir}/${f.file}`))) });
  };

  function rec(v) {
    const r = v.recording;
    if (r) return h('div.row', h('span.chip.rec', h('span.dot'), h('b', 'REC'), h('span.num', `${fix(r.seconds, 0)} s · ${r.frames} frames`)),
      h('span.mono.small', `${r.dir}/${r.file}`), r.raw ? h('span.badge', 'raw') : h('span.badge', 'with overlays'),
      r.auto ? h('span.badge.pending', 'auto (routine)') : null, h('button.danger', { onclick: () => ctx.api.cmd('video_stop') }, '■ Stop'));
    return h('div.row', name, h('button.primary', { onclick: start }, '● Record'),
      h('span.dim.small', 'Clip goes to '), h('code.small', v.dir));
  }

  function conf(c) {
    const dir = h('input', { type: 'text', value: c.dir || '', style: { width: '280px' }, title: 'relative to the repository root, or absolute' });
    const raw = h('input', { type: 'checkbox', checked: !!c.raw });
    return h('div.row',
      h('label.inline', 'folder', dir), h('label.inline', raw, 'raw frames (no overlays)'),
      h('button.sm', { onclick: () => { ctx.api.cmd('config_set', { path: 'video.dir', value: dir.value.trim(), label: 'video folder' }); ctx.api.cmd('config_set', { path: 'video.raw', value: raw.checked, label: 'video raw' }, { quiet: true }); } }, 'Stage'),
      h('span.dim.small', `resolved: ${c.resolved}`));
  }

  function files(fs) {
    if (!fs.length) return h('div.empty', 'No clips in this folder yet.');
    return h('table.t', h('tr', h('th', 'clip'), h('th', 'size'), h('th', 'recorded'), h('th', '')),
      fs.map((f) => h('tr', h('td.mono.small', f.file), h('td.num', `${fix(f.bytes / 1e6, 1)} MB`), h('td.dim', f.modified),
        h('td.acts', h('button.sm.go', { onclick: () => play(f) }, '▶ Play'),
          h('button.sm', { onclick: () => window.open(`${ctx.prefix}/videos/${encodeURIComponent(f.file)}`, '_blank') }, 'Open'),
          h('button.sm.danger', { onclick: () => ctx.api.cmd('video_delete', { file: f.file }) }, 'Delete')))));
  }

  return {
    update(S) {
      const v = S.video || {}, eff = S.config?.effective?.video || {};
      renderIf(status, 'rec', { r: v.recording && { ...v.recording, seconds: Math.round(v.recording.seconds) }, dir: v.dir }, () => rec(v));
      renderIf(settings, 'conf', { dir: eff.dir, raw: eff.raw, resolved: v.dir }, conf);
      renderIf(list, 'files', v.files || [], files);
    },
  };
}
