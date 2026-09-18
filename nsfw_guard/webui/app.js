/* nsfw guard — front-end logic (vanilla js, no network) */
'use strict';

/* marker: proves to tests and to ui_smoke.py that the script was executed */
document.documentElement.setAttribute('data-app', 'loaded');

const $ = (id) => document.getElementById(id);
const api = () => (window.pywebview && window.pywebview.api) || null;

/* diagnostics, also read by tests/ui_smoke.py */
const DIAG = { scriptLoaded: true, booted: false, polls: 0, errors: 0, lastError: null };
window.__nsfwGuard = DIAG;

let state = null;
let polling = false;
let lastAlertKey = '';

/* ------------------------------------------------------------------ helpers */

function toast(title, text, kind) {
  const box = document.createElement('div');
  box.className = 'toast' + (kind ? ' ' + kind : '');
  const t = document.createElement('div');
  t.className = 'toast-title';
  t.textContent = title;
  const b = document.createElement('div');
  b.textContent = text || '';
  box.appendChild(t);
  if (text) box.appendChild(b);
  $('toasts').appendChild(box);
  setTimeout(() => box.remove(), 6000);
}

function pct(value) {
  return Math.round((Number(value) || 0) * 100) + '%';
}

function classLabel(name) {
  const map = (state && state.class_labels) || {};
  return map[name] || String(name || '').replace(/_/g, ' ').toLowerCase();
}

function debounce(fn, wait) {
  let timer = null;
  return function () {
    const args = arguments;
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(null, args), wait);
  };
}

/* ------------------------------------------------------------------ settings */

async function pushSettings(patch) {
  const bridge = api();
  if (!bridge) return;
  try {
    const settings = await bridge.update_settings(patch);
    if (state) state.settings = settings;
  } catch (err) {
    toast('Не удалось сохранить настройку', String(err), 'bad');
  }
}

/* ------------------------------------------------------------------ rendering */

function renderStats(s) {
  $('stat-model').textContent = s.ready ? 'готова' : (s.error ? 'ошибка' : 'загрузка…');
  $('stat-frames').textContent = s.frames;
  $('stat-hits').textContent = s.hits;
  $('stat-ms').textContent = s.last && s.last.ms ? s.last.ms + ' мс' : '—';
  $('stat-watch').textContent = s.watching ? 'вкл' : 'выкл';
  $('stat-model').classList.toggle('busy-pulse', !s.ready && !s.error);
  $('version').textContent = s.version || '';
  $('hotkey').textContent = s.hotkey || 'Ctrl+Alt+S';
  $('data-dir').textContent = s.data_dir || '—';
  $('log-path').textContent = 'events.log';
}

function renderVerdict(last) {
  const pill = $('verdict-pill');
  const text = $('verdict-text');
  if (!last) {
    pill.className = 'pill pill-idle';
    pill.textContent = 'ожидание';
    text.textContent = 'Анализ ещё не запускался';
    return;
  }
  if (last.error) {
    pill.className = 'pill pill-bad';
    pill.textContent = 'ошибка';
    text.textContent = last.error;
    return;
  }
  if (last.verdict) {
    pill.className = 'pill pill-bad';
    pill.textContent = 'обнаружено';
    text.textContent = classLabel(last.class) + ' · ' + last.score + ' · ' + (last.ms || 0) + ' мс · проходов: ' + (last.passes || 1);
  } else {
    pill.className = 'pill pill-ok';
    pill.textContent = 'чисто';
    text.textContent = (last.source || '') + ' · ' + (last.ms || 0) + ' мс · проходов: ' + (last.passes || 1);
  }
}

function renderPreview(last) {
  const img = $('preview-img');
  const empty = $('preview-empty');
  $('preview-source').textContent = last && last.source ? last.source : 'нет данных';
  if (last && last.thumbnail) {
    img.src = last.thumbnail;
    img.hidden = false;
    empty.hidden = true;
  } else if (!last) {
    img.hidden = true;
    empty.hidden = false;
  }
}

function renderDetections(last) {
  const list = $('det-list');
  const dets = (last && last.detections) || [];
  $('det-count').textContent = dets.length;
  list.innerHTML = '';
  if (!dets.length) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = last ? 'Ничего не найдено.' : 'Пока пусто.';
    list.appendChild(p);
    return;
  }
  const explicit = new Set((state && state.settings.explicit) || []);
  const threshold = (state && state.settings.threshold) || 0.35;
  dets.slice(0, 40).forEach((det) => {
    const bad = explicit.has(det.class) && det.score >= threshold;
    const row = document.createElement('div');
    row.className = 'det' + (bad ? ' bad' : '');
    const top = document.createElement('div');
    top.className = 'det-top';
    const name = document.createElement('span');
    name.className = 'det-name';
    name.textContent = det.class;
    const score = document.createElement('span');
    score.className = 'det-score';
    score.textContent = Number(det.score).toFixed(3);
    top.appendChild(name);
    top.appendChild(score);
    const bar = document.createElement('div');
    bar.className = 'det-bar';
    const fill = document.createElement('span');
    fill.style.width = pct(det.score);
    bar.appendChild(fill);
    row.appendChild(top);
    row.appendChild(bar);
    list.appendChild(row);
  });
}

function renderEvents(s) {
  const list = $('event-list');
  const events = s.events || [];
  list.innerHTML = '';
  if (!events.length) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = 'Пока ничего не найдено.';
    list.appendChild(p);
  } else {
    events.forEach((ev) => {
      const row = document.createElement('div');
      row.className = 'event';
      const time = document.createElement('span');
      time.className = 'event-time';
      time.textContent = ev.time;
      const cls = document.createElement('span');
      cls.className = 'event-class';
      cls.textContent = ev.class || '—';
      const src = document.createElement('span');
      src.className = 'muted';
      src.textContent = ev.source || '';
      const score = document.createElement('span');
      score.className = 'event-score';
      score.textContent = Number(ev.score || 0).toFixed(3);
      row.append(time, cls, src, score);
      list.appendChild(row);
    });
  }
  $('log').textContent = (s.log && s.log.length) ? s.log.join('\n') : '—';
}

function renderControls(s) {
  const sel = $('monitor-select');
  const monitors = s.monitors || [];
  if (document.activeElement !== sel) {
    sel.innerHTML = '';
    monitors.forEach((mon) => {
      const opt = document.createElement('option');
      opt.value = mon.index;
      opt.textContent = (mon.index === 0 ? 'все экраны' : 'экран ' + mon.index) + ' — ' + mon.width + '×' + mon.height;
      if (String(mon.index) === String(s.settings.monitor)) opt.selected = true;
      sel.appendChild(opt);
    });
  }

  if (document.activeElement !== $('threshold')) {
    $('threshold').value = s.settings.threshold;
  }
  $('threshold-value').textContent = Number(s.settings.threshold).toFixed(2);

  const map = {
    'opt-multi': 'multi', 'opt-curtain': 'curtain', 'opt-sound': 'sound',
    'opt-save': 'save', 'opt-logall': 'log_all',
  };
  Object.keys(map).forEach((id) => { $(id).checked = !!s.settings[map[id]]; });

  if (document.activeElement !== $('watch-toggle')) {
    $('watch-toggle').checked = !!s.watching;
  }
  $('interval-value').textContent = Number(s.settings.interval).toFixed(0) + ' с';

  const chips = $('class-chips');
  if (chips.childElementCount !== (s.classes || []).length) {
    chips.innerHTML = '';
    (s.classes || []).forEach((name) => {
      const btn = document.createElement('button');
      btn.className = 'chip';
      btn.dataset.cls = name;
      btn.textContent = name;
      btn.addEventListener('click', () => toggleClass(name));
      chips.appendChild(btn);
    });
  }
  const explicit = new Set(s.settings.explicit || []);
  chips.querySelectorAll('.chip').forEach((chip) => {
    chip.classList.toggle('active', explicit.has(chip.dataset.cls));
  });
}

async function toggleClass(name) {
  const current = new Set((state && state.settings.explicit) || []);
  if (current.has(name)) current.delete(name); else current.add(name);
  await pushSettings({ explicit: Array.from(current) });
}

function alertIfNew(s) {
  const last = s.last;
  if (!last || last.error || !last.verdict) return;
  const key = (last.time || '') + '|' + (last.source || '') + '|' + (last.class || '');
  if (key === lastAlertKey) return;
  lastAlertKey = key;
  toast('Обнаружено: ' + classLabel(last.class), 'Оценка ' + last.score + ' · ' + (last.source || ''), 'bad');
}

/* ------------------------------------------------------------------- actions */

async function runScreen() {
  const bridge = api();
  if (!bridge) return;
  $('preview-empty').classList.add('busy');
  try {
    const res = await bridge.analyse_screen();
    if (res && res.error) toast('Ошибка анализа', res.error, 'bad');
  } catch (err) {
    toast('Ошибка анализа', String(err), 'bad');
  }
}

async function runClipboard() {
  const bridge = api();
  if (!bridge) return;
  try {
    const res = await bridge.analyse_clipboard();
    if (res && res.error) toast('Буфер обмена', res.error, 'bad');
  } catch (err) {
    toast('Ошибка', String(err), 'bad');
  }
}

async function pickFile() {
  const bridge = api();
  if (!bridge) return;
  try {
    const res = await bridge.pick_file();
    if (res && res.error) toast('Файл', res.error, 'bad');
  } catch (err) {
    toast('Ошибка', String(err), 'bad');
  }
}

function analyseFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const res = await api().analyse_file(file.name, reader.result);
      if (res && res.error) toast('Файл', res.error, 'bad');
    } catch (err) {
      toast('Ошибка', String(err), 'bad');
    }
  };
  reader.readAsDataURL(file);
}

/* ---------------------------------------------------------------- bindings */

function bind() {
  $('btn-check').addEventListener('click', runScreen);
  $('btn-file').addEventListener('click', pickFile);
  $('btn-file-2').addEventListener('click', pickFile);
  $('btn-clipboard').addEventListener('click', runClipboard);

  $('watch-toggle').addEventListener('change', async (ev) => {
    const bridge = api();
    if (!bridge) return;
    try {
      if (ev.target.checked) {
        await bridge.start_watch();
        toast('Слежение включено', 'интервал ' + Number(state.settings.interval).toFixed(0) + ' с', 'ok');
      } else {
        await bridge.stop_watch();
        toast('Слежение остановлено', '');
      }
    } catch (err) {
      toast('Ошибка', String(err), 'bad');
    }
  });

  const stepInterval = async (delta) => {
    const value = Math.min(120, Math.max(1, Number((state && state.settings.interval) || 3) + delta));
    $('interval-value').textContent = value + ' с';
    await pushSettings({ interval: value });
  };
  $('interval-minus').addEventListener('click', () => stepInterval(-1));
  $('interval-plus').addEventListener('click', () => stepInterval(1));

  $('monitor-select').addEventListener('change', (ev) => pushSettings({ monitor: Number(ev.target.value) }));

  $('threshold').addEventListener('input', (ev) => {
    $('threshold-value').textContent = Number(ev.target.value).toFixed(2);
  });
  $('threshold').addEventListener('change', (ev) => pushSettings({ threshold: Number(ev.target.value) }));

  const toggles = {
    'opt-multi': 'multi', 'opt-curtain': 'curtain', 'opt-sound': 'sound',
    'opt-save': 'save', 'opt-logall': 'log_all',
  };
  Object.keys(toggles).forEach((id) => {
    $(id).addEventListener('change', (ev) => pushSettings({ [toggles[id]]: ev.target.checked }));
  });

  $('preset-exposed').addEventListener('click', () => {
    const skip = ['FEET_EXPOSED', 'ARMPITS_EXPOSED', 'BELLY_EXPOSED'];
    const classes = (state.classes || []).filter((n) => n.endsWith('EXPOSED') && skip.indexOf(n) === -1);
    pushSettings({ explicit: classes });
  });
  $('preset-default').addEventListener('click', () => pushSettings({ explicit: state.default_explicit }));
  $('preset-all').addEventListener('click', () => pushSettings({ explicit: state.classes }));
  $('btn-open-dir').addEventListener('click', () => { const bridge = api(); if (bridge) bridge.open_data_dir(); });

  const stop = (ev) => { ev.preventDefault(); ev.stopPropagation(); };
  const drop = $('drop');
  ['dragenter', 'dragover'].forEach((name) => drop.addEventListener(name, (ev) => { stop(ev); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((name) => drop.addEventListener(name, (ev) => { stop(ev); drop.classList.remove('over'); }));
  drop.addEventListener('drop', (ev) => analyseFile(ev.dataTransfer.files[0]));

  document.addEventListener('dragover', stop);
  document.addEventListener('drop', (ev) => {
    stop(ev);
    const file = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
    if (file) analyseFile(file);
  });

  document.addEventListener('paste', (ev) => {
    const items = ev.clipboardData && ev.clipboardData.items;
    if (items) {
      for (let i = 0; i < items.length; i += 1) {
        if (items[i].type && items[i].type.indexOf('image') === 0) {
          analyseFile(items[i].getAsFile());
          return;
        }
      }
    }
    runClipboard();
  });
}

/* ------------------------------------------------------------------ polling */

let pollErrors = 0;

async function poll() {
  const bridge = api();
  if (!bridge || polling) return;
  polling = true;
  try {
    const snapshot = await bridge.get_state();
    pollErrors = 0;
    DIAG.polls += 1;
    DIAG.lastError = null;
    if (snapshot && snapshot.bridge_error && !state) {
      toast('Ошибка моста', String(snapshot.bridge_error).split('\n').slice(-4).join(' '), 'bad');
    }
    state = snapshot;
    renderStats(snapshot);
    renderControls(snapshot);
    renderVerdict(snapshot.last);
    renderPreview(snapshot.last);
    renderDetections(snapshot.last);
    renderEvents(snapshot);
    alertIfNew(snapshot);
  } catch (err) {
    pollErrors += 1;
    DIAG.errors += 1;
    DIAG.lastError = String(err);
    $('stat-model').textContent = 'нет связи';
    $('stat-model').classList.add('busy-pulse');
    if (pollErrors === 3) {
      toast('Мост не отвечает', String(err), 'bad');
    }
  }
  polling = false;
}

function boot() {
  if (DIAG.booted) return;
  DIAG.booted = true;
  if (window.history && 'scrollRestoration' in window.history) {
    window.history.scrollRestoration = 'manual';
  }
  bind();
  window.scrollTo(0, 0);
  setTimeout(() => window.scrollTo(0, 0), 250);
  window.addEventListener('load', () => window.scrollTo(0, 0));
  poll();
  setInterval(poll, 1200);
}

/* The bridge object is injected asynchronously: wait for the event, but also
   poll for it in case the event name differs between pywebview versions. */
function waitForBridge() {
  if (api()) {
    boot();
    return;
  }
  window.addEventListener('pywebviewready', () => boot());
  document.addEventListener('pywebviewready', () => boot());
  let tries = 0;
  const tick = () => {
    if (api()) {
      boot();
      return;
    }
    tries += 1;
    if (tries > 40) {
      DIAG.lastError = 'bridge never appeared';
      $('stat-model').textContent = 'нет движка';
      toast('Нет связи с движком', 'мост pywebview не появился, перезапустите приложение', 'bad');
      return;
    }
    setTimeout(tick, 250);
  };
  setTimeout(tick, 250);
}

waitForBridge();
