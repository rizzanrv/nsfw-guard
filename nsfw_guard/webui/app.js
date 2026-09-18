/* nsfw guard — front-end logic (vanilla js, no network) */
'use strict';

/* marker: proves to tests and to ui_smoke.py that the script was executed */
document.documentElement.setAttribute('data-app', 'loaded');

const $ = (id) => document.getElementById(id);
const api = () => (window.pywebview && window.pywebview.api) || null;

/* diagnostics, also read by tests/ui_smoke.py */
const DIAG = { scriptLoaded: true, booted: false, polls: 0, errors: 0, lastError: null,
               modelError: false, view: 'overview' };
window.__nsfwGuard = DIAG;

const VIEW_SUB = {
  overview: 'Состояние движка, последний кадр и быстрые действия.',
  screen: 'Проверка экрана вручную, слежение по таймеру и области находок.',
  file: 'Проверка картинки из файла или буфера обмена.',
  classes: 'Какие классы считать откровенными и при какой оценке срабатывать.',
  events: 'История срабатываний и хвост журнала.',
  settings: 'Реакция на находку, фоновый режим и хранение данных.'
};

const SOURCE_LABEL = {
  screen: 'экран',
  'screen-watch': 'слежение',
  hotkey: 'горячая клавиша',
  tray: 'из трея',
  clipboard: 'буфер обмена'
};

let state = null;
let polling = false;
let lastAlertKey = '';
const lastSnap = {};        // kind -> last result, so each view shows its own frame

/* ------------------------------------------------------------------ helpers */

function toast(title, text, kind) {
  const box = document.createElement('div');
  box.className = 'toast' + (kind ? ' ' + kind : '');
  const t = document.createElement('div');
  t.className = 'toast-title';
  t.textContent = title;
  box.appendChild(t);
  if (text) {
    const b = document.createElement('div');
    b.textContent = text;
    box.appendChild(b);
  }
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

function sourceLabel(source) {
  const raw = String(source || '');
  if (raw.indexOf('file:') === 0) return raw.slice(5);
  return SOURCE_LABEL[raw] || raw || 'нет данных';
}

function kindOf(source) {
  const raw = String(source || '');
  if (raw.indexOf('file:') === 0 || raw === 'clipboard') return 'file';
  return 'screen';
}

function debounce(fn, wait) {
  let timer = null;
  return function () {
    const args = arguments;
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(null, args), wait);
  };
}

/* ------------------------------------------------------------------- views */

function gotoView(name) {
  if (!name || !VIEW_SUB[name]) return;
  DIAG.view = name;
  const items = document.querySelectorAll('.nav-item');
  for (let i = 0; i < items.length; i += 1) {
    items[i].classList.toggle('is-active', items[i].dataset.view === name);
  }
  const views = document.querySelectorAll('.view');
  for (let i = 0; i < views.length; i += 1) {
    views[i].classList.toggle('is-active', views[i].dataset.view === name);
  }
  $('view-title').textContent = document.querySelector('.nav-item[data-view="' + name + '"] span').textContent;
  $('view-sub').textContent = VIEW_SUB[name];
  const scroller = $('views');
  if (scroller) scroller.scrollTop = 0;
}

/* exposed for the tray ("Настройки" menu entry) */
window.__nsfwGuardGoto = gotoView;

async function pushSettings(patch) {
  const bridge = api();
  if (!bridge) return null;
  try {
    const settings = await bridge.update_settings(patch);
    if (state) state.settings = settings;
    return settings;
  } catch (err) {
    toast('Не удалось сохранить настройку', String(err), 'bad');
    return null;
  }
}

/* ---------------------------------------------------------------- rendering */

function renderStats(s) {
  if (s.error && !DIAG.modelError) {
    DIAG.modelError = true;
    toast('Модель не загрузилась', String(s.error), 'bad');
  }
  $('stat-frames').textContent = s.frames;
  $('stat-hits').textContent = s.hits;
  $('stat-ms').textContent = s.last && s.last.ms ? s.last.ms + ' мс' : '—';
  $('stat-watch').textContent = s.watching ? 'вкл' : 'выкл';
  $('version').textContent = s.version || '—';
  $('hotkey').textContent = s.hotkey || 'Ctrl+Alt+S';
  $('status-hotkey').textContent = s.hotkey || 'Ctrl+Alt+S';
  $('data-dir').textContent = s.data_dir || '—';
  $('log-path').textContent = 'events.log';

  $('status-watch').textContent = s.watching ? 'слежение включено' : 'слежение выключено';
  $('status-frames').textContent = s.frames + ' кадров';
  $('status-hits').textContent = s.hits + ' находок';
  $('status-tray').textContent = 'трей: ' + (s.tray ? 'работает' : 'выключен');
  $('tray-state').textContent = 'трей: ' + (s.tray ? 'работает' : 'выключен');
}

function renderControls(s) {
  const settings = s.settings || {};
  const set = (id, value) => {
    const el = $(id);
    if (el && el.checked !== !!value) el.checked = !!value;
  };
  set('watch-top', s.watching);
  set('watch-toggle', s.watching);
  set('opt-multi', settings.multi);
  set('opt-curtain', settings.curtain);
  set('opt-curtain-2', settings.curtain);
  set('opt-sound', settings.sound);
  set('opt-save', settings.save);
  set('opt-logall', settings.log_all);
  set('opt-trayclose', settings.tray_close);
  set('opt-notify', settings.notify);
  set('opt-autostart', s.autostart);
  const autostart = $('opt-autostart');
  if (autostart) autostart.disabled = s.autostart_supported === false;

  const threshold = $('threshold');
  if (threshold && document.activeElement !== threshold) {
    threshold.value = settings.threshold;
  }
  $('threshold-value').textContent = Number(settings.threshold || 0).toFixed(2);

  const interval = $('interval-select');
  if (interval && document.activeElement !== interval) {
    const value = String(settings.interval || 3);
    const found = Array.prototype.some.call(interval.options, (o) => o.value === value);
    interval.value = found ? value : '3';
  }

  const monitor = $('monitor-select');
  if (monitor && !monitor.options.length && s.monitors && s.monitors.length) {
    s.monitors.forEach((m, i) => {
      const option = document.createElement('option');
      option.value = String(i);
      option.textContent = i === 0
        ? 'Все мониторы (' + m.width + '×' + m.height + ')'
        : 'Монитор ' + i + ' (' + m.width + '×' + m.height + ')';
      monitor.appendChild(option);
    });
  }
  if (monitor && document.activeElement !== monitor && monitor.options.length) {
    monitor.value = String(settings.monitor || 1);
  }
}

function renderChips(s) {
  const box = $('class-chips');
  if (!box || box.childElementCount) return;
  const chosen = {};
  (s.settings && s.settings.explicit ? s.settings.explicit : []).forEach((c) => { chosen[c] = true; });
  (s.classes || []).forEach((name) => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'chip class-chip' + (chosen[name] ? ' is-on' : '');
    chip.dataset.class = name;
    chip.title = name;
    const dot = document.createElement('span');
    dot.className = 'class-dot';
    const text = document.createElement('span');
    text.textContent = classLabel(name);
    chip.appendChild(dot);
    chip.appendChild(text);
    chip.addEventListener('click', () => toggleClass(name));
    box.appendChild(chip);
  });
}

function renderChipStates(s) {
  const chosen = {};
  (s.settings && s.settings.explicit ? s.settings.explicit : []).forEach((c) => { chosen[c] = true; });
  const chips = document.querySelectorAll('#class-chips .class-chip');
  for (let i = 0; i < chips.length; i += 1) {
    chips[i].classList.toggle('is-on', !!chosen[chips[i].dataset.class]);
  }
}

async function toggleClass(name) {
  if (!state) return;
  const current = (state.settings.explicit || []).slice();
  const index = current.indexOf(name);
  if (index === -1) {
    current.push(name);
  } else if (current.length > 1) {
    current.splice(index, 1);
  } else {
    toast('Нужен хотя бы один класс', 'Иначе ни одно срабатывание не сработает.', 'warn');
    return;
  }
  state.settings.explicit = current;
  renderChipStates(state);
  await pushSettings({ explicit: current });
}

function detRow(det, explicitSet, threshold) {
  const row = document.createElement('div');
  const bad = explicitSet[det.class] && Number(det.score) >= threshold;
  row.className = 'det' + (bad ? ' is-bad' : '');
  const name = document.createElement('span');
  name.className = 'det-name';
  name.textContent = classLabel(det.class);
  name.title = det.class;
  const bar = document.createElement('span');
  bar.className = 'det-bar';
  const fill = document.createElement('i');
  fill.style.width = Math.max(3, Math.min(100, Math.round(Number(det.score || 0) * 100))) + '%';
  bar.appendChild(fill);
  const score = document.createElement('span');
  score.className = 'det-score num';
  score.textContent = Number(det.score || 0).toFixed(2);
  row.appendChild(name);
  row.appendChild(bar);
  row.appendChild(score);
  return row;
}

function renderDetections(containerId, last) {
  const box = $(containerId);
  if (!box) return;
  box.textContent = '';
  const dets = (last && last.detections) || [];
  if (!dets.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = last ? 'Детекций нет.' : 'Пока нечего показывать.';
    box.appendChild(empty);
    return;
  }
  const settings = (state && state.settings) || {};
  const threshold = Number(settings.threshold || 0.35);
  const explicitSet = {};
  (settings.explicit || []).forEach((c) => { explicitSet[c] = true; });
  dets.slice()
    .sort((a, b) => Number(b.score || 0) - Number(a.score || 0))
    .slice(0, 12)
    .forEach((det) => box.appendChild(detRow(det, explicitSet, threshold)));
}

function renderVerdict(pillId, textId, last, idleText) {
  const pill = $(pillId);
  const text = $(textId);
  if (!pill || !text) return;
  if (!last) {
    pill.className = 'chip chip-idle';
    pill.textContent = 'ожидание';
    text.textContent = idleText || 'Анализ ещё не запускался';
    return;
  }
  if (last.error) {
    pill.className = 'chip chip-bad';
    pill.textContent = 'ошибка';
    text.textContent = last.error;
    return;
  }
  const meta = (last.ms || 0) + ' мс · проходов: ' + (last.passes || 1);
  if (last.verdict) {
    pill.className = 'chip chip-bad';
    pill.textContent = 'обнаружено';
    text.textContent = classLabel(last.class) + ' · ' + Number(last.score || 0).toFixed(2) + ' · ' + meta;
  } else {
    pill.className = 'chip chip-ok';
    pill.textContent = 'чисто';
    text.textContent = 'Максимум ' + Number(last.score || 0).toFixed(2) + ' · ' + meta;
  }
}

function showFrame(imgId, emptyId, last, force) {
  const img = $(imgId);
  const empty = emptyId ? $(emptyId) : null;
  if (!img) return;
  if (last && last.thumbnail) {
    img.src = last.thumbnail;
    img.hidden = false;
    if (empty) empty.hidden = true;
  } else if (force) {
    img.hidden = true;
    img.removeAttribute('src');
    if (empty) empty.hidden = false;
  }
}

function renderEvents(s) {
  const events = s.events || [];
  const fill = (boxId, limit) => {
    const box = $(boxId);
    if (!box) return;
    box.textContent = '';
    if (!events.length) {
      const empty = document.createElement('p');
      empty.className = 'empty';
      empty.textContent = 'Пока ничего не найдено.';
      box.appendChild(empty);
      return;
    }
    events.slice(0, limit).forEach((ev) => {
      const row = document.createElement('div');
      row.className = 'event';
      const time = document.createElement('span');
      time.className = 'event-time num';
      time.textContent = ev.time || '';
      const source = document.createElement('span');
      source.className = 'event-source';
      source.textContent = sourceLabel(ev.source);
      source.title = String(ev.source || '');
      const cls = document.createElement('span');
      cls.className = 'event-class';
      cls.textContent = classLabel(ev.class);
      const score = document.createElement('span');
      score.className = 'event-score num';
      score.textContent = Number(ev.score || 0).toFixed(2);
      row.appendChild(time);
      row.appendChild(source);
      row.appendChild(cls);
      row.appendChild(score);
      box.appendChild(row);
    });
  };
  fill('events-mini', 4);
  fill('event-list', 30);

  const log = $('log');
  if (log) {
    const lines = s.log || [];
    log.textContent = lines.length ? lines.join('\n') : '—';
  }
}

function renderLast(s) {
  const last = s.last;
  $('last-source').textContent = last ? sourceLabel(last.source) + ' · ' + (last.time || '') : 'нет данных';

  renderVerdict('verdict-pill', 'verdict-text', last, 'Анализ ещё не запускался');
  renderDetections('dets-overview', last);
  showFrame('preview-img', 'preview-empty', last, false);

  if (last) {
    const kind = kindOf(last.source);
    const stamp = [last.time, last.source, last.score, last.ms, last.verdict].join('|');
    if (lastSnap[kind] !== stamp) {
      lastSnap[kind] = stamp;
      lastSnap[kind + ':data'] = last;
    }
  }
  const screenLast = lastSnap['screen:data'];
  const fileLast = lastSnap['file:data'];
  renderVerdict('verdict-pill-screen', 'verdict-text-screen', screenLast, 'Экран ещё не проверялся');
  renderDetections('dets-screen', screenLast);
  showFrame('preview-img-screen', 'preview-empty-screen', screenLast, true);
  renderVerdict('verdict-pill-file', 'verdict-text-file', fileLast, 'Файл ещё не проверялся');
  renderDetections('dets-file', fileLast);
  const fileBox = $('file-preview-box');
  if (fileBox) fileBox.hidden = !(fileLast && fileLast.thumbnail);
  showFrame('preview-img-file', null, fileLast, true);
}

function alertIfNew(s) {
  const last = s.last;
  if (!last || !last.verdict) {
    lastAlertKey = '';
    return;
  }
  const key = [last.time, last.class, last.score, last.source].join('|');
  if (key === lastAlertKey) return;
  lastAlertKey = key;
  toast('Срабатывание: ' + classLabel(last.class),
        sourceLabel(last.source) + ' · ' + Number(last.score || 0).toFixed(2) + ' · ' + (last.ms || 0) + ' мс',
        'bad');
}

/* ------------------------------------------------------------------ actions */

async function withProgress(label, task) {
  toast(label, '', '');
  try {
    return await task();
  } catch (err) {
    toast('Ошибка', String(err), 'bad');
    return null;
  }
}

async function analyseScreen() {
  const bridge = api();
  if (!bridge) return;
  const monitor = $('monitor-select');
  const value = monitor && monitor.value !== '' ? Number(monitor.value) : null;
  await withProgress('Проверяю экран…', () => bridge.analyse_screen(value));
  await poll();
}

async function analyseClipboard() {
  const bridge = api();
  if (!bridge) return;
  const result = await withProgress('Читаю буфер обмена…', () => bridge.analyse_clipboard());
  if (result && result.error) toast('Буфер обмена', result.error, 'warn');
  await poll();
}

async function analyseFile(file) {
  const bridge = api();
  if (!bridge || !file) return;
  const reader = new FileReader();
  const data = await new Promise((resolve) => {
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
    reader.onerror = () => resolve(null);
    reader.readAsDataURL(file);
  });
  if (!data) {
    toast('Не удалось прочитать файл', String(file.name || ''), 'bad');
    return;
  }
  const result = await withProgress('Проверяю файл…', () => bridge.analyse_file(file.name || 'upload', data));
  if (result && result.error) toast('Файл', result.error, 'warn');
  gotoView('file');
  await poll();
}

async function pickFile() {
  const bridge = api();
  if (!bridge) return;
  const result = await bridge.pick_file();
  if (result && result.error && !result.cancelled) toast('Файл', result.error, 'warn');
  gotoView('file');
  await poll();
}

async function toggleWatch(value) {
  const bridge = api();
  if (!bridge) return;
  if (value === undefined) value = !(state && state.watching);
  await withProgress(value ? 'Включаю слежение…' : 'Останавливаю слежение…',
                     () => (value ? bridge.start_watch() : bridge.stop_watch()));
  await poll();
}

function setPreset(names) {
  if (!state) return;
  state.settings.explicit = names.slice();
  renderChipStates(state);
  pushSettings({ explicit: names });
}

/* ------------------------------------------------------------------ binding */

function bind() {
  document.querySelectorAll('.nav-item').forEach((el) => {
    el.addEventListener('click', () => gotoView(el.dataset.view));
  });
  document.querySelectorAll('[data-goto]').forEach((el) => {
    el.addEventListener('click', () => gotoView(el.dataset.goto));
  });

  $('quick-check').addEventListener('click', analyseScreen);
  $('btn-check-now').addEventListener('click', analyseScreen);
  $('btn-clipboard').addEventListener('click', analyseClipboard);
  $('btn-file').addEventListener('click', pickFile);
  $('btn-open-dir').addEventListener('click', () => { const b = api(); if (b) b.open_data_dir(); });
  $('btn-quit').addEventListener('click', () => { const b = api(); if (b) b.quit(); });
  $('btn-reset').addEventListener('click', async () => {
    const b = api();
    if (!b) return;
    await b.reset_settings();
    toast('Настройки сброшены', 'Вернулись значения по умолчанию.', 'ok');
    await poll();
  });

  const hide = () => {
    const b = api();
    if (b) b.hide_window();
  };
  $('btn-hide').addEventListener('click', () => { toast('Свёрнуто в трей', 'Приложение продолжает работать.', 'ok'); hide(); });
  $('btn-hide-2').addEventListener('click', () => { toast('Свёрнуто в трей', 'Приложение продолжает работать.', 'ok'); hide(); });

  ['watch-top', 'watch-toggle'].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener('change', () => toggleWatch(el.checked));
  });

  const threshold = $('threshold');
  const pushThreshold = debounce(() => pushSettings({ threshold: Number(threshold.value) }), 200);
  threshold.addEventListener('input', () => {
    $('threshold-value').textContent = Number(threshold.value).toFixed(2);
    pushThreshold();
  });

  const monitor = $('monitor-select');
  if (monitor) monitor.addEventListener('change', () => pushSettings({ monitor: Number(monitor.value) }));
  const interval = $('interval-select');
  if (interval) interval.addEventListener('change', () => pushSettings({ interval: Number(interval.value) }));

  const toggles = [
    ['opt-multi', 'multi'], ['opt-curtain', 'curtain'], ['opt-sound', 'sound'],
    ['opt-save', 'save'], ['opt-logall', 'log_all'], ['opt-trayclose', 'tray_close'],
    ['opt-notify', 'notify']
  ];
  toggles.forEach((pair) => {
    const el = $(pair[0]);
    if (!el) return;
    el.addEventListener('change', () => pushSettings({ [pair[1]]: el.checked }));
  });
  const curtain2 = $('opt-curtain-2');
  if (curtain2) {
    curtain2.addEventListener('change', () => {
      pushSettings({ curtain: curtain2.checked });
      const el = $('opt-curtain');
      if (el) el.checked = curtain2.checked;
    });
  }

  const autostart = $('opt-autostart');
  if (autostart) {
    autostart.addEventListener('change', async () => {
      const bridge = api();
      if (!bridge) return;
      const result = await bridge.set_autostart(autostart.checked);
      const enabled = !!(result && result.autostart);
      autostart.checked = enabled;
      toast(enabled ? 'Автозапуск включён' : 'Автозапуск выключен',
            enabled ? 'Старт при входе в Windows — сразу в трее.' : 'Запись из автозагрузки удалена.',
            enabled ? 'ok' : '');
      await poll();
    });
  }

  $('preset-exposed').addEventListener('click', () => {
    const all = (state && state.classes) || [];
    setPreset(all.filter((name) => /_EXPOSED$/.test(name)));
  });
  $('preset-default').addEventListener('click', () => setPreset((state && state.default_explicit) || []));
  $('preset-all').addEventListener('click', () => setPreset((state && state.classes) || []));

  bindDropZone();
  bindPaste();
}

function bindDropZone() {
  const drop = $('drop');
  if (!drop) return;
  const stop = (ev) => { ev.preventDefault(); ev.stopPropagation(); };
  ['dragenter', 'dragover'].forEach((name) => drop.addEventListener(name, (ev) => {
    stop(ev);
    drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((name) => drop.addEventListener(name, (ev) => {
    stop(ev);
    drop.classList.remove('over');
  }));
  drop.addEventListener('drop', (ev) => analyseFile(ev.dataTransfer.files[0]));
}

function bindPaste() {
  document.addEventListener('dragover', (ev) => ev.preventDefault());
  document.addEventListener('drop', (ev) => {
    ev.preventDefault();
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
    analyseClipboard();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'F5') analyseScreen();
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
    renderChips(snapshot);
    renderChipStates(snapshot);
    renderLast(snapshot);
    renderEvents(snapshot);
    alertIfNew(snapshot);
  } catch (err) {
    pollErrors += 1;
    DIAG.errors += 1;
    DIAG.lastError = String(err);
    if (pollErrors === 3) toast('Мост не отвечает', String(err), 'bad');
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
  gotoView(DIAG.view);
  window.scrollTo(0, 0);
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
      toast('Нет связи с движком', 'мост pywebview не появился, перезапустите приложение', 'bad');
      return;
    }
    setTimeout(tick, 250);
  };
  setTimeout(tick, 250);
}

waitForBridge();
