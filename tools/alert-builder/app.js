/* Watchtower — plain-English alert builder */
'use strict';

var EXAMPLES = [
  'Alert me when a person walks near a forklift',
  'Alert me when a pedestrian steps into the road in front of the car',
  'Alert me when a truck changes lanes in dense traffic'
];

var state = {
  videos: [],
  rules: [],
  incidents: [],
  incidentsReady: false,
  backfillRuns: [],
  ruleBackfill: {},
  muted: false,
  audioCtx: null,
  logCount: 0,
  lastToast: { msg: '', at: 0 }
};

var tiles = [];
var els = {};

function $(id) { return document.getElementById(id); }

function el(tag, attrs) {
  var node = document.createElement(tag);
  var children = Array.prototype.slice.call(arguments, 2);
  if (attrs) {
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v == null ? '' : String(v);
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else if (v === false || v == null) return;
      else node.setAttribute(k, v === true ? '' : String(v));
    });
  }
  children.forEach(function (c) {
    if (c == null || c === false) return;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  });
  return node;
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

function api(path, opts) {
  opts = opts || {};
  var headers = { Accept: 'application/json' };
  var init = { method: opts.method || 'GET', headers: headers };
  if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  return fetch(window.BASE + path, init).then(function (res) {
    return res.text().then(function (text) {
      var data = null;
      if (text) {
        try { data = JSON.parse(text); }
        catch (e) { data = null; }
      }
      if (!res.ok) {
        var msg = data && data.error ? String(data.error) : ('Request failed (' + res.status + ')');
        throw new Error(msg);
      }
      return data;
    });
  }).catch(function (err) {
    if (err instanceof TypeError) throw new Error('Network error — is the backend running?');
    throw err;
  });
}

function toast(message, kind) {
  kind = kind || 'info';
  var now = Date.now();
  if (kind === 'error' && message === state.lastToast.msg && now - state.lastToast.at < 8000) return;
  state.lastToast = { msg: message, at: now };
  var node = el('div', { class: 'toast ' + kind, role: 'status', text: message });
  els.toasts.appendChild(node);
  while (els.toasts.children.length > 4) els.toasts.removeChild(els.toasts.firstChild);
  setTimeout(function () {
    node.classList.add('out');
    setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, 220);
  }, kind === 'alert' ? 5200 : 3800);
}

function unlockAudio() {
  var AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  try {
    if (!state.audioCtx) state.audioCtx = new AC();
    if (state.audioCtx.state === 'suspended') state.audioCtx.resume();
  } catch (e) { /* ignore */ }
}

function beep() {
  if (state.muted || !state.audioCtx) return;
  try {
    var ctx = state.audioCtx;
    if (ctx.state === 'suspended') return;
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.type = 'square';
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(520, ctx.currentTime + 0.12);
    gain.gain.setValueAtTime(0.04, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.18);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.18);
  } catch (e) { /* ignore */ }
}

function nowHMS() {
  var d = new Date();
  function p(n) { return String(n).padStart(2, '0'); }
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}

function fmtHMS(sec) {
  var s = Math.max(0, Math.floor(Number(sec) || 0));
  function p(n) { return String(n).padStart(2, '0'); }
  return p(Math.floor(s / 3600)) + ':' + p(Math.floor((s % 3600) / 60)) + ':' + p(s % 60);
}

function footageClock(seg) {
  if (!seg) return '—';
  var date = seg.upload_timestamp ? String(seg.upload_timestamp).slice(0, 10) : '';
  var clock = fmtHMS(seg.start_sec);
  return date ? (date + '  ' + clock) : clock;
}

function makeSpinner() {
  return el('span', { class: 'spinner', 'aria-hidden': 'true' });
}

function setBusy(btn, busy, label) {
  if (busy) {
    if (!btn.dataset.label) btn.dataset.label = btn.textContent;
    btn.disabled = true;
    btn.replaceChildren(makeSpinner(), document.createTextNode(label));
  } else {
    btn.disabled = false;
    btn.textContent = btn.dataset.label || btn.textContent;
  }
}

function logLine(parts) {
  var line = el('div', { class: 'log-line' + (parts.kind ? ' ' + parts.kind : '') });
  if (parts.time) line.appendChild(el('span', { class: 'ts', text: parts.time }));
  if (parts.mid) line.appendChild(el('span', { class: 'mid', text: parts.mid }));
  if (parts.outcome) line.appendChild(el('span', { class: parts.outcomeClass || '', text: parts.outcome }));
  if (parts.text) line.appendChild(document.createTextNode(parts.text));
  els.logBody.insertBefore(line, els.logBody.firstChild);
  state.logCount += 1;
  while (els.logBody.children.length > 200) {
    els.logBody.removeChild(els.logBody.lastChild);
    state.logCount -= 1;
  }
  els.logCount.textContent = String(Math.min(200, state.logCount));
}

function backfillSentence(bf) {
  var n = bf && bf.matched != null ? bf.matched : 0;
  var m = bf && bf.checked != null ? bf.checked : 0;
  return 'Would have fired ' + n + ' times in the archive (checked ' + m + ' clips)';
}

function persistBackfill() {
  try {
    sessionStorage.setItem('wt-backfill', JSON.stringify({
      ruleBackfill: state.ruleBackfill,
      backfillRuns: state.backfillRuns
    }));
  } catch (e) { /* private mode */ }
}

function restoreBackfill() {
  try {
    var raw = sessionStorage.getItem('wt-backfill');
    if (!raw) return;
    var data = JSON.parse(raw);
    if (data && data.ruleBackfill) state.ruleBackfill = data.ruleBackfill;
    if (data && Array.isArray(data.backfillRuns)) state.backfillRuns = data.backfillRuns;
  } catch (e) { /* ignore */ }
}

function rememberBackfill(rule, bf) {
  var hits = bf && Array.isArray(bf.hits) ? bf.hits : [];
  state.ruleBackfill[rule.id] = {
    checked: bf.checked,
    matched: bf.matched,
    sentence: backfillSentence(bf)
  };
  state.backfillRuns.push({
    ruleId: rule.id,
    ruleName: rule.name || rule.id,
    at: Date.now(),
    hits: hits
  });
  persistBackfill();
  tiles.forEach(updateJump);
}

function hitsForVideo(originalVideo) {
  var best = null;
  state.backfillRuns.forEach(function (run) {
    var relevant = (run.hits || []).filter(function (h) {
      return h && h.original_video === originalVideo && h.matched === true;
    });
    if (!relevant.length) return;
    if (!best || run.at > best.at) best = { at: run.at, hits: relevant };
  });
  return best ? best.hits : [];
}

function scopeLabel(rule) {
  var cams = rule.cameras || [];
  var locs = rule.locations || [];
  if (!cams.length && !locs.length) return ['All cameras'];
  return cams.concat(locs);
}

function yoloChips(rule) {
  var rc = rule.require_classes || {};
  var keys = Object.keys(rc);
  if (!keys.length) return ['none'];
  return keys.map(function (k) { return k + ' ≥ ' + rc[k]; });
}

function keywordChips(rule) {
  var words = rule.caption_any || [];
  if (!words.length) return ['none'];
  return words.slice();
}

function renderPreview(rule) {
  els.preview.hidden = false;
  els.preview.replaceChildren();
  els.preview.appendChild(el('p', { class: 'preview-name', text: rule.name || 'Compiled rule' }));
  function block(label, values, wide) {
    var row = el('div', { class: 'chip-row' });
    values.forEach(function (v) {
      row.appendChild(el('span', { class: 'chip' + (wide ? ' wide' : ''), text: v }));
    });
    els.preview.appendChild(el('div', { class: 'chip-block' },
      el('div', { class: 'chip-label', text: label }),
      row
    ));
  }
  block('Scope', scopeLabel(rule), false);
  block('YOLO needs', yoloChips(rule), false);
  block('Caption keywords', keywordChips(rule), false);
  block('Judge question', [rule.judge_question || '—'], true);
  block('Search query', [rule.search_query || '—'], true);
}

function renderRules() {
  els.ruleCount.textContent = String(state.rules.length);
  els.rulesList.replaceChildren();
  if (!state.rules.length) {
    els.rulesList.appendChild(el('div', { class: 'empty' },
      el('strong', { text: 'No rules yet.' }),
      document.createTextNode(' Try an example above, then Preview or Activate.')
    ));
    return;
  }
  state.rules.forEach(function (rule) {
    var sw = el('button', {
      type: 'button',
      class: 'switch',
      role: 'switch',
      'aria-checked': rule.enabled ? 'true' : 'false',
      'aria-label': (rule.enabled ? 'Disable ' : 'Enable ') + (rule.name || 'rule'),
      onclick: function () { toggleRule(rule); }
    }, el('i'));
    var bf = state.ruleBackfill[rule.id];
    var actions = el('div', { class: 'rule-actions' },
      el('button', {
        type: 'button',
        class: 'tiny',
        text: 'Re-run backfill',
        onclick: function (ev) { rerunBackfill(rule, ev.currentTarget); }
      }),
      el('button', {
        type: 'button',
        class: 'tiny danger-text',
        text: 'Delete',
        onclick: function (ev) { armDelete(rule, ev.currentTarget); }
      })
    );
    var body = el('div', {},
      el('div', { class: 'rule-name', text: rule.name || rule.id }),
      el('div', { class: 'rule-text', text: rule.text || '' }),
      actions
    );
    if (bf && bf.sentence) body.appendChild(el('p', { class: 'bf-line', text: bf.sentence }));
    var card = el('article', { class: 'rule' + (rule.enabled ? '' : ' off') }, sw, body);
    els.rulesList.appendChild(card);
  });
}

function armDelete(rule, btn) {
  if (btn.dataset.armed === '1') {
    deleteRule(rule);
    return;
  }
  btn.dataset.armed = '1';
  btn.textContent = 'Sure?';
  setTimeout(function () {
    if (btn.dataset.armed === '1') {
      btn.dataset.armed = '';
      btn.textContent = 'Delete';
    }
  }, 2500);
}

function toggleRule(rule) {
  var next = !rule.enabled;
  api('api/rules/' + encodeURIComponent(rule.id), {
    method: 'PATCH',
    body: { enabled: next }
  }).then(function (updated) {
    var i = state.rules.findIndex(function (r) { return r.id === rule.id; });
    if (i >= 0) state.rules[i] = updated && updated.id ? updated : Object.assign({}, rule, { enabled: next });
    renderRules();
  }).catch(function (err) {
    toast(err.message, 'error');
  });
}

function deleteRule(rule) {
  api('api/rules/' + encodeURIComponent(rule.id), { method: 'DELETE' }).then(function () {
    state.rules = state.rules.filter(function (r) { return r.id !== rule.id; });
    renderRules();
  }).catch(function (err) {
    toast(err.message, 'error');
  });
}

function rerunBackfill(rule, btn) {
  setBusy(btn, true, 'Running…');
  api('api/rules/' + encodeURIComponent(rule.id) + '/backfill', {
    method: 'POST',
    body: { limit: 12 }
  }).then(function (bf) {
    rememberBackfill(rule, bf);
    els.activateResult.hidden = false;
    els.activateResult.textContent = backfillSentence(bf);
    logBackfill(rule, bf);
    absorbIncidents(bf.incidents || [], false);
    refreshIncidents();
    renderRules();
  }).catch(function (err) {
    toast(err.message, 'error');
  }).then(function () {
    setBusy(btn, false);
  });
}

function logBackfill(rule, bf) {
  logLine({
    kind: 'info',
    time: nowHMS(),
    mid: '  backfill  ' + (rule.name || rule.id) + '  │ ',
    outcome: backfillSentence(bf),
    outcomeClass: 'ok'
  });
}

function loadRules() {
  return api('api/rules').then(function (rules) {
    state.rules = Array.isArray(rules) ? rules : [];
    renderRules();
  });
}

function previewRule() {
  var text = els.ruleText.value.trim();
  if (!text) { toast('Type a rule first.', 'error'); return; }
  setBusy(els.previewBtn, true, 'Compiling…');
  els.activateBtn.disabled = true;
  api('api/rules/preview', { method: 'POST', body: { text: text } }).then(function (rule) {
    renderPreview(rule || {});
  }).catch(function (err) {
    toast(err.message, 'error');
  }).then(function () {
    setBusy(els.previewBtn, false);
    els.activateBtn.disabled = false;
  });
}

function activateRule() {
  var text = els.ruleText.value.trim();
  if (!text) { toast('Type a rule first.', 'error'); return; }
  setBusy(els.activateBtn, true, 'Compiling…');
  els.previewBtn.disabled = true;
  var saved = null;
  api('api/rules', { method: 'POST', body: { text: text } }).then(function (rule) {
    saved = rule;
    return loadRules().catch(function () {});
  }).then(function () {
    if (!saved || !saved.id) throw new Error('Activate did not return a rule id');
    setBusy(els.activateBtn, true, 'Searching archive…');
    return api('api/rules/' + encodeURIComponent(saved.id) + '/backfill', {
      method: 'POST',
      body: { limit: 12 }
    });
  }).then(function (bf) {
    rememberBackfill(saved, bf);
    els.activateResult.hidden = false;
    els.activateResult.textContent = backfillSentence(bf);
    logBackfill(saved, bf);
    absorbIncidents(bf.incidents || [], false);
    refreshIncidents();
    return loadRules();
  }).catch(function (err) {
    toast(err.message, 'error');
    loadRules().catch(function () {});
  }).then(function () {
    setBusy(els.activateBtn, false);
    els.previewBtn.disabled = false;
  });
}

var incidentEpoch = 0;

function absorbIncidents(items, notify) {
  if (!items || !items.length) return;
  var prev = {};
  state.incidents.forEach(function (inc) { prev[inc.id] = true; });
  var fresh = [];
  var byId = {};
  state.incidents.forEach(function (inc) { byId[inc.id] = inc; });
  items.forEach(function (inc) {
    if (!inc || !inc.id) return;
    if (!prev[inc.id]) fresh.push(inc);
    byId[inc.id] = inc;
  });
  var rest = state.incidents.filter(function (inc) {
    return !fresh.some(function (f) { return f.id === inc.id; });
  }).map(function (inc) { return byId[inc.id]; });
  state.incidents = fresh.map(function (inc) { return byId[inc.id]; }).concat(rest);
  renderIncidents(notify ? fresh : []);
  if (notify) notifyLive(fresh);
}

function notifyLive(fresh) {
  var live = fresh.filter(function (inc) { return inc.mode === 'live'; });
  if (!live.length) return;
  live.forEach(function (inc) {
    toast('SEV ' + inc.severity + ' · ' + (inc.rule_name || 'Rule') + ' · ' + (inc.camera_id || 'camera'), 'alert');
  });
  beep();
}

function renderIncidents(fresh) {
  var freshIds = {};
  (fresh || []).forEach(function (inc) {
    if (inc.mode === 'live') freshIds[inc.id] = true;
  });
  els.incCount.textContent = String(state.incidents.length);
  els.incidents.replaceChildren();
  if (!state.incidents.length) {
    els.incidents.appendChild(el('div', { class: 'empty', text: 'All quiet. Activate a rule and press play.' }));
    return;
  }
  state.incidents.forEach(function (inc) {
    var sev = Math.min(5, Math.max(1, Number(inc.severity) || 1));
    var tick = null;
    if (inc.notified === true) tick = el('span', { class: 'tick yes', title: 'Webhook delivered', text: '✓' });
    else if (inc.notified === false) tick = el('span', { class: 'tick no', title: 'Webhook failed', text: '✗' });
    var mode = String(inc.mode || '').toLowerCase() === 'backfill' ? 'backfill' : 'live';
    var tags = el('div', { class: 'inc-tags' },
      el('span', { class: 'tag ' + mode, text: mode === 'backfill' ? 'BACKFILL' : 'LIVE' })
    );
    if (tick) tags.appendChild(tick);
    tags.appendChild(el('span', { class: 'inc-time', text: footageClock(inc) }));
    var body = el('div', { class: 'inc-body' },
      el('div', { class: 'inc-top' },
        el('span', { class: 'inc-name', text: inc.rule_name || 'Rule' }),
        el('span', { class: 'sev-num', text: 'SEV ' + sev })
      ),
      el('div', { class: 'inc-cam', text: (inc.camera_id || 'camera') + (inc.location ? ' · ' + inc.location : '') }),
      el('div', { class: 'inc-reason', text: inc.reason || '' }),
      tags
    );
    var card = el('button', {
      type: 'button',
      class: 'incident sev-' + sev + (freshIds[inc.id] ? ' enter' : ''),
      onclick: function () { openModal(inc); }
    }, el('span', { class: 'strip', 'aria-hidden': 'true' }), body);
    els.incidents.appendChild(card);
  });
}

function refreshIncidents() {
  var epoch = incidentEpoch;
  return api('api/incidents').then(function (list) {
    if (epoch !== incidentEpoch) return;
    var items = Array.isArray(list) ? list : [];
    var prev = {};
    state.incidents.forEach(function (inc) { if (inc && inc.id) prev[inc.id] = true; });
    var notify = state.incidentsReady;
    state.incidentsReady = true;
    state.incidents = items;
    var fresh = [];
    items.forEach(function (inc) {
      if (inc && inc.id && !prev[inc.id]) fresh.push(inc);
    });
    renderIncidents(notify ? fresh : []);
    if (notify) notifyLive(fresh);
  }).catch(function (err) {
    toast(err.message, 'error');
  });
}

function clearIncidents() {
  els.clearBtn.disabled = true;
  api('api/incidents', { method: 'DELETE' }).then(function () {
    incidentEpoch += 1;
    state.incidents = [];
    state.incidentsReady = true;
    renderIncidents([]);
    closeModal();
    logLine({ kind: 'info', time: nowHMS(), mid: '  incidents cleared' });
  }).catch(function (err) {
    toast(err.message, 'error');
  }).then(function () {
    els.clearBtn.disabled = false;
  });
}

function renderMarkdown(src) {
  var lines = esc(src || '').replace(/\r\n/g, '\n').split('\n');
  var html = '';
  var inList = false;
  function inline(s) { return s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'); }
  function closeList() { if (inList) { html += '</ul>'; inList = false; } }
  lines.forEach(function (line) {
    var h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      closeList();
      html += '<h' + h[1].length + '>' + inline(h[2]) + '</h' + h[1].length + '>';
      return;
    }
    var b = /^\s*-\s+(.*)$/.exec(line);
    if (b) {
      if (!inList) { html += '<ul>'; inList = true; }
      html += '<li>' + inline(b[1]) + '</li>';
      return;
    }
    closeList();
    if (line.trim() === '') return;
    html += '<p>' + inline(line) + '</p>';
  });
  closeList();
  return html || '<p>No report.</p>';
}

function fillCounts(node, counts) {
  node.replaceChildren();
  var obj = counts || {};
  Object.keys(obj).sort().forEach(function (k) {
    node.appendChild(el('span', { class: 'yolo', text: k + ' ' + obj[k] }));
  });
}

function openModal(inc) {
  els.modal.hidden = false;
  els.modalTitle.textContent = inc.rule_name || 'Incident';
  var sev = inc.severity != null ? inc.severity : '—';
  var conf = (typeof inc.confidence === 'number') ? inc.confidence.toFixed(2) : '—';
  els.modalKicker.textContent = 'SEV ' + sev + '  ·  ' + conf + ' confidence';
  els.modalMeta.replaceChildren();
  [
    (inc.camera_id || 'camera') + (inc.location ? ' · ' + inc.location : ''),
    footageClock(inc),
    String(inc.mode || '').toLowerCase() === 'backfill' ? 'BACKFILL' : 'LIVE'
  ].forEach(function (t) {
    els.modalMeta.appendChild(el('span', { text: t }));
  });
  els.modalReport.innerHTML = renderMarkdown(inc.report || '');
  els.modalCaption.textContent = inc.caption || 'No caption.';
  fillCounts(els.modalCounts, inc.object_counts);
  var video = els.modalVideo;
  var fallback = els.modalFallback;
  fallback.hidden = true;
  video.hidden = false;
  if (!inc.source) {
    showModalFallback(inc);
    return;
  }
  var token = (state.modalToken = (state.modalToken || 0) + 1);
  video.onerror = function () {
    if (state.modalToken !== token) return;
    showModalFallback(inc);
  };
  video.src = window.BASE + 'api/stream?source=' + encodeURIComponent(inc.source);
  var play = video.play();
  if (play && play.catch) play.catch(function () {});
}

function showModalFallback(inc) {
  els.modalVideo.hidden = true;
  els.modalFallback.hidden = false;
  els.modalFallback.replaceChildren(
    el('div', { class: 'kicker', text: 'Stream unavailable' }),
    el('p', { class: 'big', text: inc.caption || 'No caption for this clip.' })
  );
}

function closeModal() {
  if (els.modal.hidden) return;
  els.modal.hidden = true;
  state.modalToken = (state.modalToken || 0) + 1;
  try {
    els.modalVideo.pause();
    els.modalVideo.removeAttribute('src');
    els.modalVideo.load();
  } catch (e) { /* ignore */ }
}

function renderStatus(status) {
  els.statusPills.replaceChildren();
  if (!status) {
    els.statusPills.appendChild(el('span', { class: 'pill', text: 'Status unavailable' }));
    return;
  }
  if (status.mock) els.statusPills.appendChild(el('span', { class: 'pill mock', text: 'MOCK' }));
  var model = status.llm_model ? String(status.llm_model) : '—';
  var modelPill = el('span', { class: 'pill model', title: model },
    el('span', { class: 'dot' }),
    document.createTextNode('LLM '),
    el('b', { text: model })
  );
  els.statusPills.appendChild(modelPill);
  var hookOn = !!status.webhook_configured;
  els.statusPills.appendChild(el('span', { class: 'pill ' + (hookOn ? 'on' : 'off') },
    el('span', { class: 'dot' }),
    document.createTextNode(hookOn ? 'Webhook on' : 'Webhook off')
  ));
  var traceOn = !!status.tracing;
  els.statusPills.appendChild(el('span', { class: 'pill ' + (traceOn ? 'on' : 'off') },
    el('span', { class: 'dot' }),
    document.createTextNode(traceOn ? 'Tracing on' : 'Tracing off')
  ));
}

function pollStatus() {
  api('api/status').then(renderStatus).catch(function () {
    renderStatus(null);
  });
}

function groupVideos(videos) {
  var groups = [];
  var index = {};
  videos.forEach(function (v) {
    var label = (v.camera_id || 'unknown') + ' · ' + (v.location || 'unknown');
    if (!index[label]) {
      index[label] = [];
      groups.push({ label: label, items: index[label] });
    }
    index[label].push(v);
  });
  return groups;
}

function pickDefaults(videos) {
  var prefs = ['warehouse', 'pie', 'i24'];
  var used = {};
  var picks = [];
  prefs.forEach(function (pref) {
    var found = videos.find(function (v) {
      return !used[v.original_video] && String(v.camera_id || '').toLowerCase().indexOf(pref) !== -1;
    });
    if (found) {
      picks.push(found);
      used[found.original_video] = true;
    }
  });
  videos.forEach(function (v) {
    if (picks.length >= 3) return;
    if (used[v.original_video]) return;
    if (picks.some(function (p) { return p.camera_id === v.camera_id; })) return;
    picks.push(v);
    used[v.original_video] = true;
  });
  videos.forEach(function (v) {
    if (picks.length >= 3) return;
    if (used[v.original_video]) return;
    picks.push(v);
    used[v.original_video] = true;
  });
  var n = 0;
  while (picks.length < 3 && videos.length) {
    picks.push(videos[n % videos.length]);
    n += 1;
  }
  return picks.slice(0, 3);
}

function fillSelect(select, videos, selected) {
  select.replaceChildren();
  groupVideos(videos).forEach(function (g) {
    var og = el('optgroup', { label: g.label });
    g.items.forEach(function (v) {
      var name = v.filename || v.camera_id || v.original_video;
      var opt = el('option', {
        value: v.original_video,
        text: name + (v.total_segments != null ? ' · ' + v.total_segments + ' seg' : ''),
        title: v.original_video
      });
      og.appendChild(opt);
    });
    select.appendChild(og);
  });
  if (selected) select.value = selected;
}

function buildTiles() {
  els.wall.replaceChildren();
  tiles = [];
  for (var i = 0; i < 3; i++) tiles.push(createTile(i));
}

function createTile(index) {
  var videoEl = el('video', { muted: true, autoplay: true, playsinline: true, loop: true });
  videoEl.muted = true;
  videoEl.playsInline = true;
  var fallback = el('div', { class: 'fallback', hidden: true });
  var camId = el('div', { class: 'cam-id' });
  var camLoc = el('div', { class: 'cam-loc' });
  var clock = el('div', { class: 'ov ov-clock' });
  var phase = el('div', { class: 'ov phase-tag', hidden: true });
  var yolo = el('div', { class: 'ov ov-yolo' });
  var ticker = el('div', { class: 'ov ticker' });
  var badge = el('div', { class: 'sev-badge' });
  var banner = el('div', { class: 'rule-banner' });
  var plate = el('div', { class: 'alert-plate', hidden: true }, badge, banner);
  var screen = el('div', { class: 'screen' },
    videoEl, fallback,
    el('div', { class: 'ov ov-cam' }, camId, camLoc),
    clock, phase, yolo, ticker, plate
  );
  var select = el('select', { class: 'vsel', 'aria-label': 'Camera ' + (index + 1) });
  var playBtn = el('button', { type: 'button', class: 'tiny play', text: 'Play' });
  var stepBtn = el('button', { type: 'button', class: 'tiny', text: 'Step' });
  var jumpBtn = el('button', { type: 'button', class: 'tiny jump', text: 'Jump to next event', disabled: true });
  var start = el('input', { class: 'start', type: 'number', min: '0', step: '1', value: '0', 'aria-label': 'Start segment' });
  var pace = el('select', { class: 'pace', 'aria-label': 'Seconds per segment' });
  [2, 3, 4, 6].forEach(function (n) {
    var opt = el('option', { value: String(n), text: String(n) });
    if (n === 4) opt.selected = true;
    pace.appendChild(opt);
  });
  var controls = el('div', { class: 'controls' },
    select,
    el('div', { class: 'ctrl-row' }, playBtn, stepBtn),
    jumpBtn,
    el('div', { class: 'ctrl-row' },
      el('label', {}, 'Start', start),
      el('label', {}, 'Sec/seg', pace)
    )
  );
  jumpBtn.classList.add('jump-full');
  var root = el('article', { class: 'tile idle' }, screen, controls);
  els.wall.appendChild(root);
  var tile = {
    index: index,
    root: root,
    videoEl: videoEl,
    fallback: fallback,
    camId: camId,
    camLoc: camLoc,
    clock: clock,
    phaseTag: phase,
    yolo: yolo,
    ticker: ticker,
    plate: plate,
    badge: badge,
    banner: banner,
    select: select,
    playBtn: playBtn,
    stepBtn: stepBtn,
    jumpBtn: jumpBtn,
    startInput: start,
    pace: pace,
    video: null,
    segments: [],
    pos: 0,
    seconds: 4,
    playing: false,
    phase: 'idle',
    alert: null,
    gen: 0,
    loadGen: 0,
    inFlight: null,
    loopRunning: false,
    streamToken: 0,
    wake: null
  };
  playBtn.addEventListener('click', function () { toggleTile(tile); });
  stepBtn.addEventListener('click', function () { stepTile(tile); });
  jumpBtn.addEventListener('click', function () { jumpTile(tile); });
  select.addEventListener('change', function () { onSelectVideo(tile); });
  start.addEventListener('change', function () { applyStart(tile); });
  pace.addEventListener('change', function () { tile.seconds = Number(pace.value) || 4; });
  return tile;
}

function showWallEmpty(message) {
  els.wall.hidden = true;
  els.wallEmpty.hidden = false;
  els.wallEmpty.textContent = message;
}

function showWall() {
  els.wall.hidden = false;
  els.wallEmpty.hidden = true;
}

function loadVideos() {
  return api('api/videos').then(function (videos) {
    state.videos = Array.isArray(videos) ? videos : [];
    if (!state.videos.length) {
      showWallEmpty('No footage in the index. The archive may be empty, or the backend is down.');
      return;
    }
    showWall();
    var picks = pickDefaults(state.videos);
    tiles.forEach(function (tile, i) {
      var pick = picks[i];
      fillSelect(tile.select, state.videos, pick ? pick.original_video : '');
      if (pick) loadSegments(tile, pick.original_video, false);
    });
  }).catch(function (err) {
    showWallEmpty('No footage in the index. The archive may be empty, or the backend is down. ' + err.message);
    toast(err.message, 'error');
  });
}

function onSelectVideo(tile) {
  var id = tile.select.value;
  if (!id) return;
  tile.gen += 1;
  wake(tile);
  loadSegments(tile, id, tile.playing);
}

function loadSegments(tile, originalVideo, keepPlaying) {
  var gen = (tile.loadGen += 1);
  tile.video = state.videos.find(function (v) { return v.original_video === originalVideo; }) || null;
  tile.segments = [];
  tile.camId.textContent = tile.video ? (tile.video.camera_id || '') : '';
  tile.camLoc.textContent = 'Loading segments…';
  return api('api/segments?original_video=' + encodeURIComponent(originalVideo)).then(function (segs) {
    if (gen !== tile.loadGen) return;
    tile.segments = (Array.isArray(segs) ? segs : []).slice().sort(function (a, b) {
      return (a.segment_number || 0) - (b.segment_number || 0);
    });
    if (!tile.segments.length) {
      tile.camLoc.textContent = 'No segments';
      showFallback(tile, null, 'This clip has no segments.');
      updateJump(tile);
      return;
    }
    var want = Number(tile.startInput.value);
    var idx = startIndex(tile, want);
    if (idx < 0) idx = 0;
    tile.pos = idx;
    tile.startInput.value = String(startValue(tile, idx));
    display(tile);
    updateJump(tile);
    if (keepPlaying) ensureLoop(tile);
  }).catch(function (err) {
    if (gen !== tile.loadGen) return;
    toast(err.message, 'error');
    showFallback(tile, null, 'Could not load segments. ' + err.message);
  });
}

function currentSeg(tile) {
  return tile.segments[tile.pos] || null;
}

function display(tile) {
  var seg = currentSeg(tile);
  var video = tile.video;
  tile.camId.textContent = (seg && seg.camera_id) || (video && video.camera_id) || '—';
  tile.camLoc.textContent = (seg && seg.location) || (video && video.location) || '';
  tile.clock.textContent = footageClock(seg);
  paintCounts(tile, seg && seg.object_counts);
  tile.ticker.textContent = (seg && seg.caption) || '';
  setStream(tile, seg);
  paintTile(tile);
  updateJump(tile);
}

function paintCounts(tile, counts) {
  tile.yolo.replaceChildren();
  var obj = counts || {};
  Object.keys(obj).sort().forEach(function (k) {
    tile.yolo.appendChild(el('span', { class: 'yolo', text: k + ' ' + obj[k] }));
  });
}

function setStream(tile, seg) {
  var video = tile.videoEl;
  if (!seg || !seg.source) {
    video.removeAttribute('data-stream');
    showFallback(tile, seg, (seg && seg.caption) || 'No caption for this segment.');
    return;
  }
  var url = window.BASE + 'api/stream?source=' + encodeURIComponent(seg.source);
  if (video.getAttribute('data-stream') === url && !video.hidden) return;
  var token = (tile.streamToken += 1);
  video.setAttribute('data-stream', url);
  tile.fallback.hidden = true;
  video.hidden = false;
  tile.ticker.hidden = false;
  video.onerror = function () {
    if (tile.streamToken !== token) return;
    showFallback(tile, seg, seg.caption || 'No caption for this segment.');
  };
  video.onloadeddata = function () {
    if (tile.streamToken !== token) return;
    tile.fallback.hidden = true;
    video.hidden = false;
    tile.ticker.hidden = false;
  };
  video.src = url;
  var play = video.play();
  if (play && play.catch) play.catch(function () {});
}

function showFallback(tile, seg, caption) {
  tile.videoEl.hidden = true;
  tile.fallback.hidden = false;
  tile.ticker.hidden = true;
  tile.fallback.replaceChildren(
    el('div', { class: 'kicker', text: 'Caption feed' }),
    el('p', { class: 'big', text: caption || (seg && seg.caption) || 'No caption for this segment.' })
  );
}

function paintPlay(tile) {
  tile.playBtn.textContent = tile.playing ? 'Pause' : 'Play';
  tile.playBtn.setAttribute('aria-pressed', tile.playing ? 'true' : 'false');
}

function paintTile(tile) {
  var alertOn = tile.alert && Date.now() < tile.alert.until;
  tile.root.classList.remove('idle', 'checking', 'judging', 'alert');
  var phase = alertOn ? 'alert' : tile.phase;
  tile.root.classList.add(phase);
  tile.plate.hidden = !alertOn;
  if (alertOn) {
    tile.badge.textContent = 'SEV ' + tile.alert.severity;
    tile.banner.textContent = tile.alert.ruleName || '';
  }
  if (phase === 'checking') {
    tile.phaseTag.hidden = false;
    tile.phaseTag.textContent = 'CHECKING';
  } else if (phase === 'judging') {
    tile.phaseTag.hidden = false;
    tile.phaseTag.textContent = 'JUDGING';
  } else {
    tile.phaseTag.hidden = true;
  }
}

function armAlert(tile, severity, ruleName) {
  tile.alert = { severity: severity, ruleName: ruleName, until: Date.now() + 6000 };
  clearTimeout(tile.alertTimer);
  tile.alertTimer = setTimeout(function () { paintTile(tile); }, 6100);
  paintTile(tile);
}

function applyStart(tile) {
  if (!tile.segments.length) return;
  var n = Number(tile.startInput.value);
  var idx = startIndex(tile, n);
  if (idx < 0) {
    toast('No segment ' + tile.startInput.value + ' on this camera.', 'error');
    var cur = currentSeg(tile);
    tile.startInput.value = String(startValue(tile, tile.pos));
    return;
  }
  tile.pos = idx;
  tile.gen += 1;
  display(tile);
  wake(tile);
}

function startIndex(tile, n) {
  if (isFeed(tile.video)) return Number.isInteger(n) && n >= 0 && n < tile.segments.length ? n : -1;
  return tile.segments.findIndex(function (s) { return s.segment_number === n; });
}

function startValue(tile, idx) {
  if (isFeed(tile.video)) return idx;
  var seg = tile.segments[idx];
  return seg ? seg.segment_number : 0;
}

function isFeed(video) {
  return !!video && String(video.original_video || '').indexOf('camera:') === 0;
}

function matchedHitKeys() {
  var keys = {};
  state.backfillRuns.forEach(function (run) {
    (run.hits || []).forEach(function (h) {
      if (h && h.matched === true) keys[h.original_video + '#' + h.segment_number] = true;
    });
  });
  return keys;
}

function nextEventPos(tile) {
  if (!tile.video || !tile.segments.length) return null;
  var keys = matchedHitKeys();
  var fallback = tile.video.original_video;
  var idxs = [];
  tile.segments.forEach(function (s, i) {
    if (keys[(s.original_video || fallback) + '#' + s.segment_number]) idxs.push(i);
  });
  if (!idxs.length) return null;
  var target = idxs.find(function (i) { return i - 2 > tile.pos; });
  if (target == null) target = idxs[0];
  return Math.max(0, target - 2);
}

function updateJump(tile) {
  var pos = nextEventPos(tile);
  tile.jumpBtn.disabled = pos == null;
}

function jumpTile(tile) {
  var idx = nextEventPos(tile);
  if (idx == null) return;
  tile.pos = idx;
  tile.gen += 1;
  display(tile);
  wake(tile);
  if (tile.playing) ensureLoop(tile);
}

function stepTile(tile) {
  if (!tile.segments.length) return;
  tile.pos = (tile.pos + 1) % tile.segments.length;
  tile.gen += 1;
  display(tile);
  wake(tile);
  if (tile.playing) ensureLoop(tile);
  else stepOnce(tile, tile.gen, false);
}

function toggleTile(tile) {
  if (tile.playing) pauseTile(tile);
  else playTile(tile);
}

function playTile(tile) {
  if (!tile.segments.length) {
    toast('No segments loaded for this camera.', 'error');
    return;
  }
  tile.playing = true;
  paintPlay(tile);
  ensureLoop(tile);
}

function pauseTile(tile) {
  tile.playing = false;
  paintPlay(tile);
  wake(tile);
}

function toggleAll() {
  var any = tiles.some(function (t) { return t.playing; });
  tiles.forEach(function (t) { if (any) pauseTile(t); else playTile(t); });
}

function wake(tile) {
  if (tile.wake) tile.wake();
}

function sleep(tile, ms) {
  return new Promise(function (resolve) {
    var timer = setTimeout(function () {
      tile.wake = null;
      resolve();
    }, ms);
    tile.wake = function () {
      clearTimeout(timer);
      tile.wake = null;
      resolve();
    };
  });
}

function ensureLoop(tile) {
  wake(tile);
  if (tile.loopRunning) return;
  tile.loopRunning = true;
  runLoop(tile).catch(function (err) {
    toast(err.message || 'Replay error', 'error');
    tile.playing = false;
    paintPlay(tile);
  }).then(function () {
    tile.loopRunning = false;
    if (tile.playing && tile.segments.length) ensureLoop(tile);
  });
}

async function runLoop(tile) {
  while (tile.playing) {
    var gen = tile.gen;
    await stepOnce(tile, gen, true);
    if (!tile.playing) return;
    if (tile.gen !== gen) continue;
    if (!tile.segments.length) return;
    tile.pos = (tile.pos + 1) % tile.segments.length;
  }
}

async function stepOnce(tile, gen, dwell) {
  var seg = currentSeg(tile);
  if (!seg) return;
  var loadGen = tile.loadGen;
  display(tile);
  var t0 = performance.now();
  if (tile.gen === gen) {
    tile.phase = 'checking';
    paintTile(tile);
  }
  var original = seg.original_video || (tile.video && tile.video.original_video);
  if (!original || seg.segment_number == null) {
    if (tile.gen === gen) {
      tile.phase = 'idle';
      paintTile(tile);
    }
    return;
  }
  var pending = null;
  try {
    if (tile.inFlight) {
      try { await tile.inFlight; } catch (e) { /* the owner of that request reports the error */ }
    }
    if (tile.gen !== gen || tile.loadGen !== loadGen) return;
    if (tile.video && seg.original_video && !isFeed(tile.video) && tile.video.original_video !== seg.original_video) return;
    pending = api('api/evaluate', {
      method: 'POST',
      body: { original_video: original, segment_number: seg.segment_number }
    });
    tile.inFlight = pending;
    var data = await pending;
    if (tile.inFlight === pending) tile.inFlight = null;
    applyEvalSideEffects(seg, data);
    if (tile.gen === gen && tile.loadGen === loadGen) applyEvalVisual(tile, data);
  } catch (err) {
    if (pending && tile.inFlight === pending) tile.inFlight = null;
    toast(err.message || 'Evaluate failed', 'error');
    if (tile.gen === gen) {
      tile.phase = 'idle';
      paintTile(tile);
    }
  }
  if (!dwell || tile.gen !== gen || !tile.playing) return;
  var left = tile.seconds * 1000 - (performance.now() - t0);
  if (left > 0) await sleep(tile, left);
}

function applyEvalSideEffects(seg, data) {
  data = data || {};
  var results = Array.isArray(data.results) ? data.results : [];
  var cam = (data.segment && data.segment.camera_id) || seg.camera_id || 'camera';
  var n = seg.segment_number;
  var time = nowHMS();
  results.forEach(function (res) {
    var name = res.rule_name || res.rule_id || 'rule';
    var mid = '  seg ' + n + '  ' + cam + '  │ ' + name + ' │ ';
    if (!res.prefilter_pass) {
      logLine({
        time: time,
        mid: mid,
        outcome: 'filter ✗ ' + (res.prefilter_reason || 'filtered out'),
        outcomeClass: 'bad'
      });
      return;
    }
    var verdict = res.verdict;
    if (verdict && verdict.match) {
      var conf = typeof verdict.confidence === 'number' ? verdict.confidence.toFixed(2) : '—';
      logLine({
        kind: 'match',
        time: time,
        mid: mid,
        outcome: 'judge ✓ MATCH sev ' + verdict.severity + ' (' + conf + ') — ' + (verdict.reason || ''),
        outcomeClass: 'ok'
      });
      return;
    }
    var why = (verdict && verdict.reason) || res.prefilter_reason || '';
    logLine({
      time: time,
      mid: mid,
      outcome: 'judge ✗ no match — ' + why,
      outcomeClass: 'bad'
    });
  });
  absorbIncidents(data.incidents || [], true);
  refreshIncidents();
}

function applyEvalVisual(tile, data) {
  data = data || {};
  if (data.segment) {
    paintCounts(tile, data.segment.object_counts || (currentSeg(tile) && currentSeg(tile).object_counts));
    if (data.segment.caption && tile.ticker && !tile.ticker.hidden) tile.ticker.textContent = data.segment.caption;
    if (data.segment.caption && !tile.fallback.hidden) {
      var big = tile.fallback.querySelector('.big');
      if (big) big.textContent = data.segment.caption;
    }
  }
  var results = Array.isArray(data.results) ? data.results : [];
  var matches = results.filter(function (r) { return r.verdict && r.verdict.match; });
  if (matches.length) {
    matches.sort(function (a, b) { return (b.verdict.severity || 0) - (a.verdict.severity || 0); });
    var top = matches[0];
    tile.phase = 'idle';
    armAlert(tile, top.verdict.severity || 1, top.rule_name || 'Alert');
    return;
  }
  var passed = results.some(function (r) { return r.prefilter_pass; });
  tile.phase = passed ? 'judging' : 'idle';
  paintTile(tile);
}

function resetTile(tile) {
  tile.alert = null;
  clearTimeout(tile.alertTimer);
  tile.phase = 'idle';
  tile.gen += 1;
  wake(tile);
  if (!tile.segments.length) {
    paintTile(tile);
    return;
  }
  var n = Number(tile.startInput.value);
  var idx = startIndex(tile, n);
  if (idx < 0) idx = 0;
  tile.pos = idx;
  display(tile);
  paintTile(tile);
}

async function demoReset() {
  els.resetBtn.disabled = true;
  try {
    await api('api/incidents', { method: 'DELETE' });
    incidentEpoch += 1;
    state.incidents = [];
    state.incidentsReady = true;
    renderIncidents([]);
    closeModal();
    logLine({
      kind: 'info',
      time: nowHMS(),
      mid: '  demo reset  │ incidents cleared, tiles returned to start segment'
    });
  } catch (err) {
    toast(err.message, 'error');
  }
  tiles.forEach(resetTile);
  tiles.forEach(function (tile) { if (tile.playing) ensureLoop(tile); });
  els.resetBtn.disabled = false;
}

function bindChrome() {
  EXAMPLES.forEach(function (text) {
    els.examples.appendChild(el('button', {
      type: 'button',
      class: 'example',
      text: text,
      onclick: function () {
        els.ruleText.value = text;
        els.ruleText.focus();
      }
    }));
  });
  els.previewBtn.addEventListener('click', previewRule);
  els.activateBtn.addEventListener('click', activateRule);
  els.clearBtn.addEventListener('click', clearIncidents);
  els.resetBtn.addEventListener('click', function () { demoReset(); });
  els.muteBtn.addEventListener('click', function () {
    unlockAudio();
    state.muted = !state.muted;
    els.muteBtn.textContent = state.muted ? 'Muted' : 'Mute';
    els.muteBtn.setAttribute('aria-pressed', state.muted ? 'true' : 'false');
  });
  els.logToggle.addEventListener('click', function () {
    var collapsed = els.log.classList.toggle('collapsed');
    els.logToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    els.logBody.hidden = collapsed;
  });
  els.modalClose.addEventListener('click', closeModal);
  els.modal.addEventListener('click', function (ev) {
    if (ev.target && ev.target.dataset && ev.target.dataset.close) closeModal();
  });
  els.ruleText.addEventListener('keydown', function (ev) {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') {
      ev.preventDefault();
      activateRule();
    }
  });
  document.addEventListener('pointerdown', unlockAudio);
  document.addEventListener('keydown', function (ev) {
    unlockAudio();
    if (ev.key === 'Escape') closeModal();
    var typing = ev.target && ev.target.closest && ev.target.closest('textarea, input, select');
    if (typing) return;
    if (ev.key === ' ' || ev.code === 'Space') {
      if (ev.target && ev.target.closest && ev.target.closest('button, a')) return;
      ev.preventDefault();
      toggleAll();
    } else if (ev.key === 'r' || ev.key === 'R') {
      ev.preventDefault();
      els.ruleText.focus();
    }
  });
}

function boot() {
  els = {
    statusPills: $('status-pills'),
    muteBtn: $('mute-btn'),
    resetBtn: $('reset-btn'),
    ruleText: $('rule-text'),
    examples: $('examples'),
    previewBtn: $('preview-btn'),
    activateBtn: $('activate-btn'),
    activateResult: $('activate-result'),
    preview: $('preview'),
    ruleCount: $('rule-count'),
    rulesList: $('rules-list'),
    wall: $('wall'),
    wallEmpty: $('wall-empty'),
    incCount: $('inc-count'),
    clearBtn: $('clear-btn'),
    incidents: $('incidents'),
    log: $('log'),
    logToggle: $('log-toggle'),
    logCount: $('log-count'),
    logBody: $('log-body'),
    toasts: $('toasts'),
    modal: $('modal'),
    modalClose: $('modal-close'),
    modalVideo: $('modal-video'),
    modalFallback: $('modal-fallback'),
    modalKicker: $('modal-kicker'),
    modalTitle: $('modal-title'),
    modalMeta: $('modal-meta'),
    modalReport: $('modal-report'),
    modalCaption: $('modal-caption'),
    modalCounts: $('modal-counts')
  };
  bindChrome();
  buildTiles();
  restoreBackfill();
  renderRules();
  renderIncidents([]);
  pollStatus();
  setInterval(pollStatus, 30000);
  loadVideos();
  loadRules().catch(function (err) { toast(err.message, 'error'); renderRules(); });
  refreshIncidents();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
