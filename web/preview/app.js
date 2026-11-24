/* Creator preview UI — no dependencies, no build step. All data comes from the local JSON API. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { project: null, edl: null, versions: [], renders: [], version: null, render: null };

  const PRESETS = [
    'remove the unnecessary waiting sections',
    'make the first 20s more exciting',
    'add stronger subtitles to important moments',
    'reduce background music during my commentary',
    'restore the part I deleted at 1:20',
    'fade between clips',
    'cut the last 5 seconds'
  ];

  async function api(path, options) {
    const res = await fetch(path, options);
    let body;
    try { body = await res.json(); } catch (_) { body = { ok: false, error: { message: 'Non-JSON response' } }; }
    if (!res.ok || body.ok === false) throw new Error(body.error?.message || `HTTP ${res.status}`);
    return body;
  }

  function toast(message) {
    const el = $('toast');
    el.textContent = message;
    el.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.classList.remove('show'), 3500);
  }

  function options(select, items, label, value) {
    select.innerHTML = '';
    for (const item of items) {
      const opt = document.createElement('option');
      opt.value = String(value(item));
      opt.textContent = label(item);
      select.append(opt);
    }
  }

  function setVideo() {
    if (!state.render) return;
    const params = new URLSearchParams();
    params.set('version', String(state.version));
    params.set('render', String(state.render));
    $('edited').src = `/api/video?${params}`;
    $('source').src = '/api/video/source?id=src1';
  }

  function renderTimeline() {
    const edl = state.edl;
    const root = $('timeline');
    root.innerHTML = '';
    const duration = Math.max(edl.summary.duration_seconds, 1);
    const pct = (t) => `${Math.max(0, Math.min(100, (t / duration) * 100))}%`;
    const lanes = ['video', 'removed', 'caption'];
    for (const name of lanes) {
      const lane = document.createElement('div');
      lane.className = `lane ${name}`;
      root.append(lane);
    }
    const [videoLane, removedLane, captionLane] = root.querySelectorAll('.lane');
    for (const clip of edl.edl.tracks.video) {
      const block = document.createElement('div');
      block.className = 'block clip';
      block.style.left = pct(clip.timeline_in);
      block.style.width = pct((clip.source_out - clip.source_in) / (clip.speed || 1));
      block.textContent = `${clip.clip_id} ${clip.source_in.toFixed(1)}–${clip.source_out.toFixed(1)}s`;
      block.title = `source ${clip.source_in}–${clip.source_out}s · speed ${clip.speed || 1}`;
      videoLane.append(block);
    }
    for (const range of edl.edl.removed_ranges || []) {
      const block = document.createElement('div');
      block.className = 'block removed';
      const before = (edl.edl.removed_ranges || []).filter(r => r.out <= range.in)
        .reduce((n, r) => n + (r.out - r.in), 0);
      block.style.left = pct(range.in - before);
      block.style.width = pct(range.out - range.in);
      block.textContent = `removed ${range.in}–${range.out}s`;
      block.title = `reason: ${range.reason} · restorable`;
      removedLane.append(block);
    }
    for (const cue of edl.edl.tracks.captions || []) {
      const block = document.createElement('div');
      block.className = 'block caption';
      block.style.left = pct(cue.start);
      block.style.width = pct(Math.max(0.5, cue.end - cue.start));
      block.textContent = cue.text;
      captionLane.append(block);
    }
    for (const marker of edl.edl.markers || []) {
      const tick = document.createElement('div');
      tick.className = 'marker';
      tick.style.left = pct(marker.at);
      tick.title = `${marker.label} (${marker.label_type || 'unlabelled'})`;
      root.append(tick);
    }
  }

  function renderQa(qa) {
    const root = $('qa');
    root.innerHTML = '';
    if (!qa) { root.textContent = 'No render yet.'; return; }
    for (const check of qa.checks) {
      const row = document.createElement('div');
      row.className = 'qa-row';
      const status = document.createElement('span');
      status.className = check.ok ? 'ok' : 'bad';
      status.textContent = check.ok ? '✓' : (check.severity === 'review' ? '?' : '!');
      const name = document.createElement('span');
      name.textContent = `${check.name}: `;
      const detail = document.createElement('span');
      detail.className = 'hint';
      detail.textContent = check.detail;
      row.append(status, name, detail);
      root.append(row);
    }
  }

  function renderMeta() {
    const edl = state.edl.edl;
    $('decisions').innerHTML = '';
    for (const decision of edl.provenance.decisions || []) {
      const li = document.createElement('li');
      li.textContent = `${decision.op}: ${decision.evidence || decision.intent || ''}`;
      $('decisions').append(li);
    }
    $('pending').innerHTML = '';
    api('/api/revisions').then((body) => {
      for (const rev of body.revisions) {
        const li = document.createElement('li');
        li.textContent = `${rev.id} · ${rev.intent} · ${rev.request}`;
        $('pending').append(li);
      }
    }).catch(() => {});
    $('exports').innerHTML = '';
    api('/api/exports').then((body) => {
      for (const item of body.exports) {
        const li = document.createElement('li');
        li.textContent = `v${item.timeline_version} r${item.render_number} · QA ${item.qa_ok ? 'pass' : 'issues'}`;
        $('exports').append(li);
      }
    }).catch(() => {});
  }

  async function loadQa() {
    if (!state.render) return;
    try {
      const res = await fetch(`/api/qa?version=${state.version}&render=${state.render}`);
      if (res.ok) renderQa(await res.json());
    } catch (_) { /* QA is optional until the first render */ }
  }

  async function loadTimeline() {
    state.edl = await api(`/api/timeline?version=${state.version}`);
    renderTimeline();
    renderMeta();
  }

  async function refresh() {
    try {
      state.project = await api('/api/project');
      $('project-label').textContent = `${state.project.project_id} · latest v${state.project.latest_version ?? '—'}`;
      state.versions = state.project.versions;
      state.renders = state.project.renders;
      options($('version-select'), state.versions,
        (v) => `v${v.version} (${v.duration}s, ${v.clips} clips)`, (v) => v.version);
      if (!state.versions.some(v => v.version === state.version)) {
        state.version = state.project.latest_version;
      }
      $('version-select').value = String(state.version ?? '');
      const forVersion = state.renders.filter(r => r.timeline_version === state.version);
      options($('render-select'), forVersion,
        (r) => `r${r.render_number} · QA ${r.qa_ok ? 'pass' : 'issues'}`, (r) => r.render_number);
      state.render = forVersion.length ? forVersion[forVersion.length - 1].render_number : null;
      $('render-select').value = state.render ? String(state.render) : '';
      if (state.version) await loadTimeline();
      setVideo();
      await loadQa();
    } catch (err) { toast(err.message); }
  }

  function bind() {
    $('reload-btn').onclick = () => refresh();
    $('version-select').onchange = (e) => { state.version = Number(e.target.value); refresh(); };
    $('render-select').onchange = (e) => { state.render = Number(e.target.value); setVideo(); loadQa(); };
    $('presets').innerHTML = '';
    for (const text of PRESETS) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = text;
      button.onclick = () => { $('revision-text').value = text; };
      $('presets').append(button);
    }
    $('revise-btn').onclick = async () => {
      try {
        const body = await api('/api/revisions', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ request: $('revision-text').value, version: state.version })
        });
        const out = $('revision-result');
        out.hidden = false;
        out.textContent = JSON.stringify(body.revision, null, 2);
        toast(body.question?.length ? 'Needs clarification — see result' : 'Revision saved as pending');
        renderMeta();
      } catch (err) { toast(err.message); }
    };
    $('render-btn').onclick = async () => {
      toast('Rendering… this may take a minute');
      try {
        const body = await api('/api/render', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ version: state.version })
        });
        toast(`Render v${body.timeline_version} r${body.render_number} complete · QA ${body.qa_ok ? 'pass' : 'issues'}`);
        await refresh();
      } catch (err) { toast(err.message); }
    };
    $('edited').addEventListener('timeupdate', () => {
      const source = $('source');
      if (!source.duration || !$('edited').duration || source.readyState < 1) return;
      const ratio = Math.min(1, $('edited').currentTime / $('edited').duration);
      const target = ratio * source.duration;
      if (Math.abs(source.currentTime - target) > 0.4) source.currentTime = target;
    });
  }

  bind();
  refresh();
})();
