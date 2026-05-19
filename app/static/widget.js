/**
 * AMO CRM Voice Widget — embeddable voice agent for any website.
 *
 * Usage:
 *   <script src="https://YOUR_DOMAIN/widget.js" data-widget-token="wgt_..."></script>
 *
 * Optional attributes:
 *   data-api-base   — override API base URL (defaults to same origin as the script)
 */
(function () {
  'use strict';

  // ── Bootstrap ──────────────────────────────────────────────────────────────
  var script = document.currentScript ||
    document.querySelector('script[data-widget-token]');
  if (!script) return;

  var WIDGET_TOKEN = script.getAttribute('data-widget-token') || '';
  var API_BASE = (script.getAttribute('data-api-base') || script.src.replace(/\/widget\.js.*$/, '')).replace(/\/$/, '');

  if (!WIDGET_TOKEN) {
    console.warn('[AMO Widget] data-widget-token is required');
    return;
  }

  var SAMPLE_RATE = 16000;
  var CHUNK_SIZE = 4096;

  // ── State machine ──────────────────────────────────────────────────────────
  var STATES = {
    IDLE: 'idle',
    REQUESTING_MIC: 'requesting_mic',
    LOADING: 'loading',
    CONNECTING: 'connecting',
    ACTIVE: 'active',
    ENDING: 'ending',
    ENDED: 'ended',
    LEAD_CAPTURE: 'lead_capture',
    DONE: 'done',
  };

  var state = STATES.IDLE;
  var config = null;
  var session = null;
  var ws = null;
  var audioCtx = null;
  var stream = null;
  var processor = null;
  var playbackCursor = 0;
  var inboundCarry = null; // Uint8Array carry for odd-byte PCM frames
  var panelOpen = false;

  // ── Audio utilities (ported from BrowserCallPage.tsx) ─────────────────────
  function downsampleBuffer(buffer, inputRate, outputRate) {
    if (inputRate === outputRate) return buffer;
    var ratio = inputRate / outputRate;
    var newLength = Math.round(buffer.length / ratio);
    var result = new Float32Array(newLength);
    var offsetResult = 0;
    var offsetBuffer = 0;
    while (offsetResult < result.length) {
      var nextOffset = Math.round((offsetResult + 1) * ratio);
      var accum = 0, count = 0;
      for (var i = offsetBuffer; i < nextOffset && i < buffer.length; i++) {
        accum += buffer[i];
        count++;
      }
      result[offsetResult] = count ? accum / count : 0;
      offsetResult++;
      offsetBuffer = nextOffset;
    }
    return result;
  }

  function floatTo16BitPCM(floatBuffer) {
    var result = new Int16Array(floatBuffer.length);
    for (var i = 0; i < floatBuffer.length; i++) {
      var sample = Math.max(-1, Math.min(1, floatBuffer[i]));
      result[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return result;
  }

  function int16ToFloat32(buffer) {
    var int16 = new Int16Array(buffer);
    var float32 = new Float32Array(int16.length);
    for (var i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 0x7fff;
    }
    return float32;
  }

  function resampleFloat32Linear(buffer, inputRate, outputRate) {
    if (inputRate === outputRate) return buffer;
    var ratio = inputRate / outputRate;
    var outLen = Math.max(1, Math.round(buffer.length / ratio));
    var output = new Float32Array(outLen);
    for (var i = 0; i < outLen; i++) {
      var pos = i * ratio;
      var left = Math.floor(pos);
      var right = Math.min(left + 1, buffer.length - 1);
      var frac = pos - left;
      var ls = buffer[left] || 0;
      var rs = buffer[right] !== undefined ? buffer[right] : ls;
      output[i] = ls + (rs - ls) * frac;
    }
    return output;
  }

  // ── Playback ───────────────────────────────────────────────────────────────
  var pendingChunks = [];
  var STARTUP_FLUSH = 1024;
  var STEADY_FLUSH = 3072;
  var flushed = false;

  function schedulePcm16(pcm16Buffer) {
    if (!audioCtx) return;
    var float32 = int16ToFloat32(pcm16Buffer);
    var playbackRate = audioCtx.sampleRate;
    var resampled = playbackRate !== SAMPLE_RATE
      ? resampleFloat32Linear(float32, SAMPLE_RATE, playbackRate)
      : float32;

    var audioBuffer = audioCtx.createBuffer(1, resampled.length, playbackRate);
    audioBuffer.copyToChannel(resampled, 0);
    var source = audioCtx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioCtx.destination);

    var now = audioCtx.currentTime;
    var start = Math.max(now, playbackCursor);
    source.start(start);
    playbackCursor = start + audioBuffer.duration;
  }

  function handleInboundAudio(arrayBuffer) {
    // Handle odd-byte carry from previous frame
    var incoming;
    if (inboundCarry && inboundCarry.byteLength > 0) {
      var merged = new Uint8Array(inboundCarry.byteLength + arrayBuffer.byteLength);
      merged.set(inboundCarry, 0);
      merged.set(new Uint8Array(arrayBuffer), inboundCarry.byteLength);
      incoming = merged.buffer;
      inboundCarry = null;
    } else {
      incoming = arrayBuffer;
    }

    // Ensure even byte length for Int16Array
    if (incoming.byteLength % 2 !== 0) {
      inboundCarry = new Uint8Array(incoming.slice(incoming.byteLength - 1));
      incoming = incoming.slice(0, incoming.byteLength - 1);
    }

    if (incoming.byteLength === 0) return;

    pendingChunks.push(incoming);
    var total = pendingChunks.reduce(function (s, c) { return s + c.byteLength; }, 0);
    var threshold = flushed ? STEADY_FLUSH : STARTUP_FLUSH;

    if (total >= threshold) {
      flushed = true;
      pendingChunks.forEach(function (chunk) { schedulePcm16(chunk); });
      pendingChunks = [];
    }
  }

  function cancelPendingPlayback() {
    pendingChunks = [];
    flushed = false;
    if (audioCtx) {
      playbackCursor = audioCtx.currentTime;
    }
  }

  // ── Microphone capture ─────────────────────────────────────────────────────
  function startMicCapture() {
    if (!audioCtx || !stream || !ws) return;
    var source = audioCtx.createMediaStreamSource(stream);
    processor = audioCtx.createScriptProcessor(CHUNK_SIZE, 1, 1);
    processor.onaudioprocess = function (e) {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      var input = e.inputBuffer.getChannelData(0);
      var downsampled = downsampleBuffer(input, audioCtx.sampleRate, SAMPLE_RATE);
      var pcm16 = floatTo16BitPCM(downsampled);
      ws.send(pcm16.buffer);
    };
    source.connect(processor);
    processor.connect(audioCtx.destination);
  }

  // ── WebSocket ──────────────────────────────────────────────────────────────
  function handleWsMessage(event) {
    if (event.data instanceof ArrayBuffer) {
      handleInboundAudio(event.data);
      return;
    }
    try {
      var msg = JSON.parse(event.data);
      if (msg.type === 'ready') {
        setState(STATES.ACTIVE);
        startMicCapture();
        setStatus('Звонок активен');
      } else if (msg.type === 'interrupted') {
        cancelPendingPlayback();
      } else if (msg.type === 'call_ended') {
        handleCallEnd();
      } else if (msg.type === 'transcript') {
        appendTranscript(msg.role, msg.text);
      }
    } catch (e) { /* ignore malformed frames */ }
  }

  // ── Call flow ──────────────────────────────────────────────────────────────
  async function startCall() {
    setState(STATES.REQUESTING_MIC);
    setStatus('Запрашиваем доступ к микрофону…');

    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    } catch (err) {
      setStatus('Нет доступа к микрофону');
      setState(STATES.IDLE);
      return;
    }

    setState(STATES.LOADING);
    setStatus('Создаём сессию…');

    var sessRes;
    try {
      sessRes = await fetch(API_BASE + '/public/widget/' + WIDGET_TOKEN + '/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!sessRes.ok) throw new Error('HTTP ' + sessRes.status);
      session = await sessRes.json();
    } catch (err) {
      setStatus('Не удалось создать сессию');
      setState(STATES.IDLE);
      stopStream();
      return;
    }

    setState(STATES.CONNECTING);
    setStatus('Подключаемся…');

    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') {
      await audioCtx.resume();
    }
    playbackCursor = audioCtx.currentTime;
    inboundCarry = null;
    pendingChunks = [];
    flushed = false;

    ws = new WebSocket(session.websocket_url);
    ws.binaryType = 'arraybuffer';
    ws.onmessage = handleWsMessage;
    ws.onerror = function () {
      setStatus('Ошибка соединения');
      handleCallEnd();
    };
    ws.onclose = function () {
      if (state === STATES.ACTIVE || state === STATES.CONNECTING) {
        handleCallEnd();
      }
    };
  }

  function stopCall() {
    if (state === STATES.IDLE || state === STATES.ENDING || state === STATES.ENDED) return;
    setState(STATES.ENDING);
    setStatus('Завершаем звонок…');
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.close();
    } else {
      handleCallEnd();
    }
  }

  function handleCallEnd() {
    teardown();
    setState(STATES.ENDED);
    setStatus('Звонок завершён');

    if (config && config.lead_capture_fields) {
      showLeadForm();
    } else {
      setState(STATES.DONE);
      showIdleButton();
    }
  }

  function teardown() {
    if (processor) {
      try { processor.disconnect(); } catch (e) {}
      processor = null;
    }
    if (stream) {
      stream.getTracks().forEach(function (t) { t.stop(); });
      stream = null;
    }
    if (audioCtx) {
      audioCtx.close().catch(function () {});
      audioCtx = null;
    }
    if (ws && ws.readyState < WebSocket.CLOSING) {
      ws.close();
    }
    ws = null;
    pendingChunks = [];
    flushed = false;
  }

  // ── Transcript ────────────────────────────────────────────────────────────
  function appendTranscript(role, text) {
    var list = shadow.getElementById('amo-transcript-list');
    if (!list) return;
    var bubble = document.createElement('div');
    bubble.className = 'amo-bubble amo-bubble--' + (role === 'assistant' ? 'ai' : 'user');
    bubble.textContent = text;
    list.appendChild(bubble);
    list.scrollTop = list.scrollHeight;
  }

  // ── Lead form ─────────────────────────────────────────────────────────────
  function showLeadForm() {
    setState(STATES.LEAD_CAPTURE);
    var panel = shadow.getElementById('amo-panel');
    if (!panel) return;
    var fields = config.lead_capture_fields || {};
    var html = '<div class="amo-lead-form"><p class="amo-lead-title">Оставьте контакт</p>';
    if (fields.name) html += '<input class="amo-input" id="amo-lead-name" type="text" placeholder="Ваше имя" />';
    if (fields.email) html += '<input class="amo-input" id="amo-lead-email" type="email" placeholder="Email" />';
    if (fields.phone) html += '<input class="amo-input" id="amo-lead-phone" type="tel" placeholder="Телефон" />';
    html += '<button class="amo-btn amo-btn--primary" id="amo-lead-submit">Отправить</button></div>';
    panel.insertAdjacentHTML('beforeend', html);
    var btn = shadow.getElementById('amo-lead-submit');
    if (btn) btn.addEventListener('click', submitLead);
  }

  async function submitLead() {
    var payload = { call_id: session ? session.call_id : null };
    var nameEl = shadow.getElementById('amo-lead-name');
    var emailEl = shadow.getElementById('amo-lead-email');
    var phoneEl = shadow.getElementById('amo-lead-phone');
    if (nameEl) payload.name = nameEl.value;
    if (emailEl) payload.email = emailEl.value;
    if (phoneEl) payload.phone = phoneEl.value;

    try {
      await fetch(API_BASE + '/public/widget/' + WIDGET_TOKEN + '/lead', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (e) { /* fail silently */ }

    setState(STATES.DONE);
    showIdleButton();
  }

  // ── UI helpers ─────────────────────────────────────────────────────────────
  function setState(s) {
    state = s;
    updateUI();
  }

  function setStatus(text) {
    var el = shadow.getElementById('amo-status');
    if (el) el.textContent = text;
  }

  function showIdleButton() {
    var panel = shadow.getElementById('amo-panel');
    if (panel) panel.style.display = 'none';
    panelOpen = false;
    updateUI();
  }

  function updateUI() {
    var btn = shadow.getElementById('amo-fab');
    var stopBtn = shadow.getElementById('amo-stop-btn');
    if (!btn) return;

    var isActive = state === STATES.ACTIVE;
    var isBusy = state === STATES.REQUESTING_MIC || state === STATES.LOADING || state === STATES.CONNECTING || state === STATES.ENDING;

    btn.className = 'amo-fab' + (isActive ? ' amo-fab--active' : '') + (isBusy ? ' amo-fab--busy' : '');

    if (stopBtn) {
      stopBtn.style.display = (isActive || isBusy) ? 'block' : 'none';
    }
  }

  // ── Shadow DOM + CSS ───────────────────────────────────────────────────────
  var host = document.createElement('div');
  host.id = 'amo-voice-widget-host';
  host.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;';
  document.body.appendChild(host);

  var shadow = host.attachShadow({ mode: 'open' });

  var pos = (config && config.custom_styles && config.custom_styles.position) || 'bottom-right';
  var color = (config && config.custom_styles && config.custom_styles.color) || '#7c3aed';

  shadow.innerHTML =
    '<style>' +
    ':host{all:initial;font-family:system-ui,sans-serif;}' +
    '.amo-fab{position:fixed;' + (pos === 'bottom-left' ? 'left:24px;' : 'right:24px;') + 'bottom:24px;' +
      'width:56px;height:56px;border-radius:50%;background:' + color + ';color:#fff;' +
      'border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;' +
      'box-shadow:0 4px 14px rgba(0,0,0,.25);transition:transform .2s;pointer-events:all;}' +
    '.amo-fab:hover{transform:scale(1.08);}' +
    '.amo-fab--active{animation:amo-pulse 1.5s infinite;}' +
    '.amo-fab--busy{opacity:.7;cursor:wait;}' +
    '@keyframes amo-pulse{0%,100%{box-shadow:0 0 0 0 ' + color + '66;}50%{box-shadow:0 0 0 10px transparent;}}' +
    '.amo-panel{position:fixed;' + (pos === 'bottom-left' ? 'left:24px;' : 'right:24px;') + 'bottom:90px;' +
      'width:300px;background:#fff;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.18);' +
      'overflow:hidden;display:none;pointer-events:all;flex-direction:column;}' +
    '.amo-panel-header{background:' + color + ';color:#fff;padding:12px 16px;display:flex;align-items:center;justify-content:space-between;}' +
    '.amo-panel-title{font-weight:600;font-size:14px;}' +
    '.amo-status{font-size:12px;opacity:.85;margin-top:2px;}' +
    '.amo-stop-btn{background:rgba(255,255,255,.25);border:none;color:#fff;' +
      'padding:4px 10px;border-radius:20px;font-size:12px;cursor:pointer;}' +
    '.amo-stop-btn:hover{background:rgba(255,255,255,.4);}' +
    '.amo-transcript{max-height:200px;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px;}' +
    '.amo-bubble{padding:8px 12px;border-radius:12px;font-size:13px;line-height:1.4;max-width:90%;}' +
    '.amo-bubble--ai{background:#f0ebff;color:#2d1b69;align-self:flex-start;}' +
    '.amo-bubble--user{background:#7c3aed;color:#fff;align-self:flex-end;}' +
    '.amo-lead-form{padding:16px;display:flex;flex-direction:column;gap:10px;}' +
    '.amo-lead-title{font-size:14px;font-weight:600;color:#1a1a1a;margin:0;}' +
    '.amo-input{border:1px solid #ddd;border-radius:8px;padding:8px 12px;font-size:13px;outline:none;}' +
    '.amo-input:focus{border-color:' + color + ';}' +
    '.amo-btn--primary{background:' + color + ';color:#fff;border:none;border-radius:8px;' +
      'padding:10px;font-size:14px;font-weight:600;cursor:pointer;}' +
    '.amo-btn--primary:hover{opacity:.9;}' +
    '</style>' +
    '<button class="amo-fab" id="amo-fab" title="Голосовой помощник">' +
      '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M12 2a3 3 0 0 1 3 3v7a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z"/>' +
        '<path d="M19 10v2a7 7 0 0 1-14 0v-2"/>' +
        '<line x1="12" y1="19" x2="12" y2="23"/>' +
        '<line x1="8" y1="23" x2="16" y2="23"/>' +
      '</svg>' +
    '</button>' +
    '<div class="amo-panel" id="amo-panel">' +
      '<div class="amo-panel-header">' +
        '<div>' +
          '<div class="amo-panel-title" id="amo-agent-name">Голосовой помощник</div>' +
          '<div class="amo-status" id="amo-status">Нажмите, чтобы позвонить</div>' +
        '</div>' +
        '<button class="amo-stop-btn" id="amo-stop-btn" style="display:none">Завершить</button>' +
      '</div>' +
      '<div class="amo-transcript" id="amo-transcript-list"></div>' +
    '</div>';

  // ── Wire up events ─────────────────────────────────────────────────────────
  var fab = shadow.getElementById('amo-fab');
  var panel = shadow.getElementById('amo-panel');
  var stopBtn = shadow.getElementById('amo-stop-btn');

  fab.addEventListener('click', function () {
    if (state === STATES.IDLE || state === STATES.DONE) {
      panelOpen = true;
      panel.style.display = 'flex';
      startCall();
    }
  });

  stopBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    stopCall();
  });

  // ── Load config and update UI ──────────────────────────────────────────────
  fetch(API_BASE + '/public/widget/' + WIDGET_TOKEN + '/config')
    .then(function (r) { return r.json(); })
    .then(function (cfg) {
      config = cfg;
      var nameEl = shadow.getElementById('amo-agent-name');
      if (nameEl && cfg.agent_name) nameEl.textContent = cfg.agent_name;

      // Apply custom color from config
      if (cfg.custom_styles && cfg.custom_styles.color) {
        var c = cfg.custom_styles.color;
        var styleEl = shadow.querySelector('style');
        if (styleEl) {
          styleEl.textContent = styleEl.textContent.replace(new RegExp(color.replace('#', '\\#'), 'g'), c);
        }
        color = c;
      }
    })
    .catch(function () {
      console.warn('[AMO Widget] Failed to load widget config');
    });

})();
