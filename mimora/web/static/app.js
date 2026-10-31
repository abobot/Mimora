/* Mimora 网页版 - 前端逻辑
 *
 * 流程：加载设置 → 生成短语 → 播放参考音频 → 浏览器录音(getUserMedia)
 *      → 上传 WAV 给后端评分 → 显示反馈 → 下一句。
 *
 * 录音在浏览器侧完成（Web Audio API），后端只负责合成参考音与评分。
 * 所有音频上传为 16-bit PCM WAV；录音采样率统一为 16 kHz 以匹配引擎。
 *
 * v2（完整功能）：模式导航（练习/听写/专项/弱点/历史/统计/设置），
 * 每个模式一个面板；听写与专项在浏览器侧录音、上传评分，其余为数据视图。
 */

"use strict";

/* ---------- API ---------- */

const API = {
  async get(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    return r.json();
  },
  async post(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    return r.json();
  },
  async put(url, body) {
    const r = await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    return r.json();
  },
  async del(url) {
    const r = await fetch(url, { method: "DELETE" });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    return r.json();
  },
  async health() { return this.get("/api/health"); },
  async settings() { return this.get("/api/settings"); },
  async generatePhrase(sourceText, phraseSource) {
    return this.post("/api/phrase", {
      source_text: sourceText, length: "full", phrase_source: phraseSource,
    });
  },
  async standalonePhrase(phrase) {
    return this.post("/api/phrase/standalone", { phrase });
  },
  async score(wavBlob, phrase, sampleRate) {
    const fd = new FormData();
    fd.append("file", wavBlob, "take.wav");
    fd.append("phrase", phrase);
    fd.append("sample_rate", String(sampleRate));
    const r = await fetch("/api/score", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    return r.json();
  },
  async sessionStats() { return this.get("/api/stats/session"); },
  async historyTakes() { return this.get("/api/stats/history?lookback=50"); },
  async saveSetting(key, value) { return this.post("/api/settings", { key, value }); },
  async sourceText() { return this.get("/api/source-text"); },
  async saveSourceText(text) { return this.put("/api/source-text", { text }); },
  async weakness(range) { return this.post("/api/weakness", { range }); },
  async history() { return this.get("/api/history"); },
  async dashboard() { return this.get("/api/dashboard"); },
  async deleteTake(sid, tid) { return this.del(`/api/takes/${sid}/${tid}`); },
  async deleteSession(sid) { return this.del(`/api/sessions/${sid}`); },
  async clearHistory() { return this.del("/api/history"); },
  async exportHistory() { return this.get("/api/export"); },
  async dictationStart(sourceText, randomOrder) {
    return this.post("/api/dictation/start", { source_text: sourceText, random_order: randomOrder });
  },
  async dictationSubmit(userText) { return this.post("/api/dictation/submit", { user_text: userText }); },
  async dictationSkip() { return this.post("/api/dictation/skip"); },
  async dictationPrev() { return this.post("/api/dictation/previous"); },
  async dictationFinish() { return this.post("/api/dictation/finish"); },
  async drillStart(target, sourceText) {
    return this.post("/api/drill/start", { target, source_text: sourceText });
  },
  async drillNext() { return this.post("/api/drill/next"); },
  async drillPrev() { return this.post("/api/drill/previous"); },
  async drillTake(phrase, score) { return this.post("/api/drill/take", { phrase, score }); },
  async drillFinish() { return this.post("/api/drill/finish"); },
  async takeAudio(sessionId, takeId) {
    return this.get(`/api/takes/${sessionId}/${takeId}/audio`);
  },
};

const el = (id) => document.getElementById(id);

const state = {
  ready: false,
  settings: null,
  mode: "practice",
  // 练习
  phrase: "",
  translation: "",
  referenceWav: null, // { audioBuffer, sampleRate }
  referenceSrc: null,
  userBlob: null,
  userSrc: null,
  recording: false,
  mediaStream: null,
  chunks: [],
  sampleRate: 16000,
  phraseSource: "llm",
  sourceText: "",
  // 听写
  dict: { active: false, phrase: "", slots: [] },
  dictWav: null,
  // 专项
  drill: { active: false, phrase: "", target: null },
  drillWav: null,
};

/* ---------- 音频工具 ---------- */

function wavToWavBlob(float32, sampleRate) {
  return new Promise((resolve) => {
    const ctx = new OfflineAudioContext(1, Math.max(1, Math.ceil(float32.length * 16000 / sampleRate)), 16000);
    const src = ctx.createBufferSource();
    const buf = ctx.createBuffer(1, float32.length, sampleRate);
    buf.getChannelData(0).set(float32);
    src.buffer = buf;
    src.connect(ctx.destination);
    src.start();
    ctx.startRendering().then((rendered) => {
      const samples = rendered.getChannelData(0);
      const buffer = new ArrayBuffer(44 + samples.length * 2);
      const view = new DataView(buffer);
      writeWavHeader(view, samples.length, 16000);
      let offset = 44;
      for (let i = 0; i < samples.length; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
        offset += 2;
      }
      resolve(new Blob([view], { type: "audio/wav" }));
    });
  });
}

function writeWavHeader(view, dataLength, sampleRate) {
  view.setUint32(0, 0x46464952, true); // "RIFF"
  view.setUint32(4, 36 + dataLength * 2, true);
  view.setUint32(8, 0x45564157, true); // "WAVE"
  view.setUint32(12, 0x20746d66, true); // "fmt "
  view.setUint32(16, 16, true);         // PCM chunk size
  view.setUint16(20, 1, true);          // PCM
  view.setUint16(22, 1, true);          // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);          // block align
  view.setUint16(34, 16, true);         // bits per sample
  view.setUint32(36, 0x61746164, true); // "data"
  view.setUint32(40, dataLength * 2, true);
}

function base64ToWav(base64, sampleRate) {
  const bin = atob(base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: "audio/wav" });
}

/* ---------- 状态更新 ---------- */

function setBanner(text, cls) {
  const b = el("status-banner");
  b.textContent = text;
  b.className = "status-banner " + (cls || "");
}

function setControlsReady(ready, recording) {
  const rec = !!recording;
  el("btn-play-ref").disabled = !ready || rec;
  el("btn-play-slow").disabled = !ready || rec;
  el("btn-next").disabled = !ready || rec;
  // 录音按钮在 ready 时始终可用：同一个按钮在录音期间是“停止”，
  // 若按 state.recording 禁用，录音一开始按钮就被锁死，无法停止。
  el("btn-record").disabled = !ready;
  el("btn-play-user").disabled = !state.userBlob || rec;
}

function setDictationReady(active) {
  el("btn-dict-play").disabled = !active;
  el("btn-dict-play-slow").disabled = !active;
  el("btn-dict-submit").disabled = !active;
  el("btn-dict-skip").disabled = !active;
  el("btn-dict-prev").disabled = !active;
}

function setDrillReady(active) {
  el("btn-drill-play").disabled = !active;
  el("btn-drill-play-slow").disabled = !active;
  el("btn-drill-next").disabled = !active;
  el("btn-drill-record").disabled = !active;
  el("btn-drill-play-user").disabled = !active || !state.drillWav;
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function scoreClass(score, threshold) {
  return score >= (threshold || 70) ? "pass" : "fail";
}

function fmtTime(ts) {
  return (ts || "").replace("T", " ").slice(5, 16);
}

/* ---------- 模式切换 ---------- */

function switchMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
  });
  document.querySelectorAll(".panel").forEach((p) => {
    p.classList.toggle("active", p.id === "panel-" + mode);
  });
  // 进入某个面板时按需加载数据
  if (mode === "weakness") loadWeakness();
  if (mode === "history") loadHistoryFull();
  if (mode === "stats") loadDashboard();
  if (mode === "settings") renderSettingsForm();
}

/* ---------- 练习：反馈 ---------- */

function showFeedback(result) {
  const scoreEl = el("feedback-score");
  scoreEl.textContent = result.score > 0 ? Math.round(result.score) : "—";
  scoreEl.className = "feedback-score " + scoreClass(result.score, state.settings?.score_threshold);
  el("feedback-grade").textContent = result.grade ? `等级 ${result.grade}` : "";
  el("feedback-text").textContent = result.feedback || result.transcription || "";
  const we = el("word-errors");
  we.innerHTML = "";
  (result.word_errors || []).forEach((w) => {
    const span = document.createElement("span");
    span.className = "we";
    span.textContent = typeof w === "string" ? w : (w.word || w.expected || JSON.stringify(w));
    we.appendChild(span);
  });
  renderWordFeedback(result.reference_words || []);
  renderAxisLines(result);
  renderWorkonBadges(result.weak_phonemes || []);
  // Underline bad words in the phrase itself (desktop mark_miss_words parity).
  renderPhraseWords(state.phrase, (result.reference_words || []));
  const s = el("session-summary");
  s.textContent = `本次：${result.session_takes} 次 · 均 ${result.session_avg} · 最佳 ${result.session_best}`;
  renderAttemptDots(result.session_takes || 0, result.session_avg);
}

// Progress-ring parity: a row of dots, one per take in this session. The latest
// take is highlighted; the row is a compact stand-in for the desktop ring.
function renderAttemptDots(count, avg) {
  const box = el("attempt-dots");
  box.innerHTML = "";
  if (!count) { box.style.display = "none"; return; }
  box.style.display = "";
  const max = 12;
  const n = Math.min(count, max);
  for (let i = 0; i < n; i++) {
    const dot = document.createElement("span");
    dot.className = "adot";
    if (i === n - 1) dot.classList.add("cur");
    box.appendChild(dot);
  }
  if (count > max) {
    const more = document.createElement("span");
    more.className = "amore";
    more.textContent = `+${count - max}`;
    box.appendChild(more);
  }
}

// Render the phrase as clickable word spans. Clicking a word hears it slowly.
// Miss words (level bad) are underlined, mirroring desktop mark_miss_words.
function renderPhraseWords(phrase, reference_words) {
  const box = el("phrase-text");
  box.innerHTML = "";
  const words = (phrase || "").split(/(\s+)/).filter(Boolean);
  // Map each phrase word to its quality level from reference_words (which are
  // in the same order, one entry per target word).
  const levels = parseWordLevels(reference_words);
  let li = 0;
  words.forEach((w) => {
    if (/^\s+$/.test(w)) {
      box.appendChild(document.createTextNode(w));
      return;
    }
    const span = document.createElement("span");
    span.className = "pw";
    span.textContent = w;
    const level = li < levels.length ? levels[li].level : null;
    if (level === "bad") span.classList.add("miss");
    if (level) li++;
    span.title = level ? `${w} — ${level}` : w;
    span.addEventListener("click", () => speakWord(w));
    box.appendChild(span);
  });
}

// Desktop hero parity: per-word "traffic light" strip under the phrase. Each
// cell is one target word coloured by its quality level (phoneme engine:
// level good/ok/bad; acoustic engine: correct bool). Click hears that word.
function parseWordLevels(reference_words) {
  const out = [];
  (reference_words || []).forEach((w) => {
    const word = w.word || "";
    let level = w.level;
    if (level !== "good" && level !== "ok" && level !== "bad") {
      level = w.correct === false ? "bad" : "good";
    }
    out.push({ word, level });
  });
  return out;
}

function renderWordFeedback(reference_words) {
  const strip = el("word-feedback");
  strip.innerHTML = "";
  const cells = parseWordLevels(reference_words);
  if (!cells.length) { strip.style.display = "none"; return; }
  strip.style.display = "";
  cells.forEach((c) => {
    const cell = document.createElement("span");
    cell.className = "wf " + c.level;
    cell.textContent = c.word;
    cell.title = `${c.word} — ${c.level}`;
    cell.addEventListener("click", () => { if (c.word) speakWord(c.word); });
    strip.appendChild(cell);
  });
}

// Fluency / shadowing axis lines under the verdict (desktop hero parity).
function renderAxisLines(result) {
  const fl = el("fluency-line");
  if (result.fluency_axis != null && result.fluency_axis >= 0) {
    const verdict = result.speed_label ? " · " + result.speed_label : "";
    fl.textContent = `流利度 ${Math.round(result.fluency_axis)}${verdict}`;
    fl.classList.remove("hidden");
  } else {
    fl.classList.add("hidden");
  }
  const sh = el("shadowing-line");
  if (result.shadowing_axis != null && result.shadowing_axis >= 0) {
    let txt = `跟读 ${Math.round(result.shadowing_axis)}`;
    if (result.delay_sec) txt += ` · ${(+result.delay_sec).toFixed(2)}s`;
    if (result.overlap) txt += ` · ${Math.round(result.overlap * 100)}%`;
    sh.textContent = txt;
    sh.classList.remove("hidden");
  } else {
    sh.classList.add("hidden");
  }
}

// WORK ON phoneme badges. A badge with a known example word (from settings.
// phoneme_examples) speaks it on click; an unknown phoneme stays a plain badge.
function renderWorkonBadges(weak_phonemes) {
  const row = el("workon-row");
  row.innerHTML = "";
  const examples = state.settings?.phoneme_examples || {};
  (weak_phonemes || []).forEach((p) => {
    const phoneme = typeof p === "string" ? p : (p.phoneme || "");
    if (!phoneme) return;
    const badge = document.createElement("span");
    const example = examples[phoneme] || "";
    badge.className = "workon-badge" + (example ? "" : " no-example");
    badge.textContent = `/${phoneme}/`;
    badge.title = example ? `as in '${example}'` : "";
    if (example) badge.addEventListener("click", () => speakWord(example));
    row.appendChild(badge);
  });
  if ((weak_phonemes || []).length) {
    const lbl = document.createElement("span");
    lbl.className = "workon-caption";
    lbl.textContent = "WORK ON";
    row.prepend(lbl);
  }
}
/* ---------- 录音（练习 + 听写/专项共用） ---------- */

async function ensureMic() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("浏览器不支持 getUserMedia（请使用较新的 Chrome/Edge）");
  }
  state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  return state.mediaStream;
}

async function startRecording(mode) {
  if (state.recording) return;
  try {
    const stream = await ensureMic();
    const ctx = new AudioContext();
    const source = ctx.createMediaStreamSource(stream);
    const processor = ctx.createScriptProcessor(4096, 1, 1);
    state.chunks = [];
    state._ctx = ctx;
    state._processor = processor;
    state._stream = stream;
    state._sampleRate = ctx.sampleRate;
    let level = 0;
    processor.onaudioprocess = (e) => {
      const ch = e.inputBuffer.getChannelData(0);
      state.chunks.push(new Float32Array(ch));
      let sum = 0;
      for (let i = 0; i < ch.length; i++) sum += ch[i] * ch[i];
      level = Math.sqrt(sum / ch.length);
      const bar = mode === "drill" ? el("rec-level-bar-drill") : el("rec-level-bar");
      if (bar) bar.style.width = Math.min(100, level * 400) + "%";
      const out = e.outputBuffer.getChannelData(0);
      out.fill(0);
    };
    source.connect(processor);
    processor.connect(ctx.destination);
    state.recording = true;
    state.recMode = mode;
    const btn = mode === "drill" ? el("btn-drill-record") : el("btn-record");
    btn.textContent = "■ 停止";
    btn.classList.add("recording");
    if (mode === "drill") { setDrillReady(true); setDrillLocked(true); }
    else { setControlsReady(true, true); }
    startTimer(mode);
  } catch (err) {
    setBanner("录音启动失败: " + err.message, "error");
  }
}

function stopRecording() {
  if (!state.recording) return;
  state.recording = false;
  const mode = state.recMode || "practice";
  const btn = mode === "drill" ? el("btn-drill-record") : el("btn-record");
  btn.textContent = "● 开始录音";
  btn.classList.remove("recording");
  stopTimer();
  state._processor?.disconnect();
  state._ctx?.close();
  state._stream?.getTracks().forEach((t) => t.stop());
  processPcm(state.chunks, state._sampleRate || 16000, mode);
}

function processPcm(chunks, sampleRate, mode) {
  if (!chunks.length) {
    setBanner("没有采到音频", "error");
    if (mode === "drill") setDrillReady(state.drill.active);
    else setControlsReady(true, false);
    return;
  }
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const flat = new Float32Array(total);
  let off = 0;
  for (const c of chunks) { flat.set(c, off); off += c.length; }
  wavToWavBlob(flat, sampleRate).then(async (wavBlob) => {
    if (mode === "drill") {
      state.drillWav = wavBlob;
      setDrillReady(state.drill.active);
    } else {
      state.userBlob = wavBlob;
      el("btn-play-user").disabled = false;
    }
    await uploadAndScore(wavBlob, mode);
  }).catch((err) => {
    setBanner("录音处理失败: " + err.message, "error");
    if (mode === "drill") setDrillReady(state.drill.active);
    else setControlsReady(true, false);
  });
}

async function uploadAndScore(wavBlob, mode) {
  if (mode === "drill") {
    if (!state.drill.phrase) return;
    setBanner("评分中…", "loading");
    try {
      const result = await API.score(wavBlob, state.drill.phrase, state.sampleRate);
      // 记录到专项引擎
      const d = await API.drillTake(state.drill.phrase, result.score);
      renderDrillState(d);
      renderDrillFeedback(result);
      setBanner("已评分", "ready");
    } catch (err) {
      setBanner("评分失败: " + err.message, "error");
    } finally {
      setDrillReady(state.drill.active);
    }
    return;
  }
  // 练习模式
  if (!state.phrase) return;
  setBanner("评分中…", "loading");
  try {
    const result = await API.score(wavBlob, state.phrase, state.sampleRate);
    showFeedback(result);
    setBanner("已评分", "ready");
    loadHistory();
  } catch (err) {
    setBanner("评分失败: " + err.message, "error");
  } finally {
    setControlsReady(true, false);
  }
}

/* ---------- 计时器 ---------- */

let timerInterval = null;
let timerSec = 0;
function startTimer(mode) {
  timerSec = 0;
  const t = mode === "drill" ? el("rec-timer-drill") : el("rec-timer");
  if (!t) return;
  t.textContent = "0:00";
  timerInterval = setInterval(() => {
    timerSec++;
    const m = Math.floor(timerSec / 60), s = timerSec % 60;
    t.textContent = `${m}:${s.toString().padStart(2, "0")}`;
  }, 1000);
}
function stopTimer() { clearInterval(timerInterval); }

/* ---------- 播放 ---------- */

async function loadReferenceAudio(base64, sampleRate) {
  const blob = base64ToWav(base64, sampleRate);
  const arrayBuffer = await blob.arrayBuffer();
  const ctx = new AudioContext();
  const buf = await ctx.decodeAudioData(arrayBuffer);
  ctx.close();
  return buf;
}

function playBuffer(buf, slow) {
  stopPlayback();
  const src = new AudioContext();
  const node = src.createBufferSource();
  node.buffer = buf;
  node.playbackRate.value = slow ? 0.7 : 1.0;
  node.connect(src.destination);
  node.start();
  state.referenceSrc = { ctx: src, node };
}

function stopPlayback() {
  if (state.referenceSrc) {
    try { state.referenceSrc.node.stop(); state.referenceSrc.ctx.close(); } catch (e) {}
    state.referenceSrc = null;
  }
}

function playReference(slow) {
  if (!state.referenceWav) return;
  playBuffer(state.referenceWav, slow);
}

// Speak a single word slowly (desktop hero: clicking a phrase word or a word
// cell hears that word). The desktop synthesizes the word through TTS at the
// reference speed; the web has no per-word reference audio, so it synthesizes
// the word on demand via the standalone endpoint and plays it at 0.7x.
async function speakWord(word) {
  if (!word) return;
  setBanner(`播放 ${word}…`, "loading");
  try {
    const ref = await API.standalonePhrase(word);
    const buf = await loadReferenceAudio(ref.reference_wav_base64, ref.reference_sr);
    playBuffer(buf, true);
    setBanner("就绪", "ready");
  } catch (err) {
    setBanner("播放失败: " + err.message, "error");
  }
}

function playUser() {
  if (!state.userBlob) return;
  stopUser();
  const url = URL.createObjectURL(state.userBlob);
  const a = new Audio(url);
  a.play();
  state.userSrc = a;
}
function stopUser() { if (state.userSrc) { state.userSrc.pause(); } }

/* ---------- 练习：短语 ---------- */

async function generatePhrase() {
  stopPlayback();
  setBanner("生成短语…", "loading");
  el("phrase-text").textContent = "…";
  el("translation").textContent = "";
  try {
    const data = await API.generatePhrase(state.sourceText, state.phraseSource);
    state.phrase = data.phrase;
    state.translation = data.translation || "";
    state.referenceWav = null;
    renderPhraseWords(data.phrase, []);
    el("translation").textContent = data.translation || "";
    el("voice-label").textContent = data.voice || "";
    state.referenceWav = await loadReferenceAudio(data.reference_wav_base64, data.reference_sr);
    setControlsReady(true);
    setBanner("就绪", "ready");
    if (state.settings?.auto_play_phrase) playReference(false);
    el("feedback-score").textContent = "—";
    el("feedback-grade").textContent = "";
    el("feedback-text").textContent = "";
    el("word-errors").innerHTML = "";
  } catch (err) {
    setBanner("生成失败: " + err.message, "error");
    el("phrase-text").textContent = "生成失败，请重试";
  }
}

/* ---------- 练习：历史 ---------- */

async function loadHistory() {
  try {
    const data = await API.historyTakes();
    const list = el("history-list");
    list.innerHTML = "";
    (data.takes || []).slice().reverse().forEach((t) => {
      const row = document.createElement("div");
      row.className = "history-row";
      const score = document.createElement("span");
      score.className = "hscore " + scoreClass(t.score, state.settings?.score_threshold);
      score.textContent = Math.round(t.score);
      const phrase = document.createElement("span");
      phrase.className = "hphrase";
      phrase.textContent = t.phrase;
      const time = document.createElement("span");
      time.className = "htime";
      time.textContent = fmtTime(t.timestamp);
      row.appendChild(score); row.appendChild(phrase); row.appendChild(time);
      list.appendChild(row);
    });
  } catch (e) { /* 历史加载失败不阻塞练习 */ }
}

/* ---------- 听写 ---------- */

function dictSlotsFor(phrase) {
  // 后端用 reference_words 分词；这里简单按空格切分以生成输入框。
  const words = (phrase || "").trim().split(/\s+/).filter(Boolean);
  return words;
}

async function startDictation() {
  try {
    const data = await API.dictationStart(state.sourceText);
    state.dict.active = true;
    state.dict.phrase = data.phrase;
    renderDictation(data);
    setBanner("听写已开始", "ready");
  } catch (err) {
    setBanner("听写启动失败: " + err.message, "error");
  }
}

function renderDictation(data) {
  el("dictation-phrase").textContent = data.phrase ? "…" : "—";
  state.dict.phrase = data.phrase;
  // 播放参考（合成并返回 WAV）
  if (data.phrase) {
    API.standalonePhrase(data.phrase).then(async (ref) => {
      state.dictWav = await loadReferenceAudio(ref.reference_wav_base64, ref.reference_sr);
      if (state.settings?.auto_play_phrase) playBuffer(state.dictWav, false);
    }).catch((e) => setBanner("参考音合成失败: " + e.message, "error"));
  }
  // 槽位
  const slots = dictSlotsFor(data.phrase);
  const container = el("dictation-slots");
  container.innerHTML = "";
  slots.forEach(() => {
    const input = document.createElement("input");
    input.className = "dict-slot";
    container.appendChild(input);
  });
  el("dictation-progress").textContent =
    data.progress ? `第 ${data.progress.current}/${data.progress.total} 句 · 通过 ${data.progress.passed} · 连对 ${data.progress.streak}` : "";
  setDictationReady(true);
  el("dictation-feedback").textContent = "";
  el("dictation-result").innerHTML = "";
}

function collectDictSlots() {
  return Array.from(el("dictation-slots").querySelectorAll(".dict-slot"))
    .map((i) => i.value.trim()).filter(Boolean).join(" ");
}

async function submitDictation() {
  if (!state.dict.active) return;
  const userText = collectDictSlots();
  if (!userText) { setBanner("请先输入听写内容", "error"); return; }
  try {
    const data = await API.dictationSubmit(userText);
    renderDictationFeedback(data);
    if (data.progress?.done) {
      const fin = await API.dictationFinish();
      renderDictationOutcome(fin.outcome);
      state.dict.active = false;
      setDictationReady(false);
      setBanner("听写完成", "ready");
    } else if (data.phrase) {
      // 进入下一句
      state.dict.phrase = data.phrase;
      renderDictation(data);
    }
  } catch (err) {
    setBanner("提交失败: " + err.message, "error");
  }
}

function renderDictationFeedback(data) {
  el("dictation-feedback").textContent = `得分 ${data.score ?? "—"} · ${data.alignment || ""}`;
  const container = el("dictation-result");
  container.innerHTML = "";
  (data.status || []).forEach((w) => {
    const span = document.createElement("span");
    span.className = "we " + (w.state === "correct" ? "ok" : "bad");
    span.textContent = `${w.word}:${w.state}`;
    container.appendChild(span);
  });
}

function renderDictationOutcome(outcome) {
  el("dictation-outcome").textContent = outcome
    ? `完成 ${outcome.total_attempts} 次 · 通过 ${outcome.total_passed} · 平均 ${outcome.avg_score} · ${outcome.mastered ? "已掌握 ✔" : "未掌握"}`
    : "";
}

async function skipDictation() {
  if (!state.dict.active) return;
  try {
    const data = await API.dictationSkip();
    renderDictation(data);
  } catch (e) { setBanner("跳过失败: " + e.message, "error"); }
}

async function prevDictation() {
  if (!state.dict.active) return;
  try {
    const data = await API.dictationPrev();
    renderDictation(data);
  } catch (e) { setBanner("上一句失败: " + e.message, "error"); }
}

async function finishDictation() {
  if (!state.dict.active) return;
  try {
    const fin = await API.dictationFinish();
    renderDictationOutcome(fin.outcome);
    state.dict.active = false;
    setDictationReady(false);
    setBanner("听写已结束", "ready");
  } catch (e) { setBanner("结束失败: " + e.message, "error"); }
}

function playDictation(slow) {
  if (state.dictWav) playBuffer(state.dictWav, slow);
}

/* ---------- 专项（drill） ---------- */

async function startDrill() {
  const raw = el("drill-target-input").value.trim();
  if (!raw) { setBanner("请输入目标词或音素", "error"); return; }
  // 目标类型推断：单个 IPA 音素（含非 ASCII）→ phoneme；否则 word。
  const target = /[^\x00-\x7F]/.test(raw) && raw.length <= 3
    ? { type: "phoneme", value: raw }
    : { type: "word", value: raw };
  try {
    const data = await API.drillStart(target, state.sourceText);
    state.drill.active = true;
    state.drill.target = target;
    renderDrill(data);
    setBanner("专项已开始", "ready");
  } catch (err) {
    setBanner("专项启动失败: " + err.message, "error");
  }
}

function renderDrill(data) {
  state.drill.phrase = data.phrase;
  el("drill-phrase").textContent = data.phrase || "—";
  el("drill-target").textContent = data.fallback_reason
    ? `目标：${state.drill.target?.value || ""}（回退：${data.fallback_message || ""}）`
    : `目标：${state.drill.target?.value || ""}`;
  el("drill-fallback").textContent = data.fallback_message || "";
  if (data.phrase) {
    API.standalonePhrase(data.phrase).then(async (ref) => {
      state.drillWav = await loadReferenceAudio(ref.reference_wav_base64, ref.reference_sr);
      setDrillReady(state.drill.active);
      if (state.settings?.auto_play_phrase) playBuffer(state.drillWav, false);
    }).catch((e) => setBanner("参考音合成失败: " + e.message, "error"));
  }
  renderDrillProgress(data.progress);
  el("drill-feedback").textContent = "";
}

function renderDrillProgress(progress) {
  el("drill-progress").textContent = progress
    ? `第 ${progress.current}/${progress.total} 次 · 通过 ${progress.passed} · 连对 ${progress.streak}`
    : "";
  el("drill-scores").innerHTML = "";
  if (progress && progress.scores && progress.scores.length) {
    progress.scores.slice().reverse().forEach((s, i) => {
      const span = document.createElement("span");
      span.className = "drill-score " + scoreClass(s, progress.threshold);
      span.textContent = Math.round(s);
      el("drill-scores").appendChild(span);
    });
  }
}

function renderDrillFeedback(result) {
  el("drill-feedback").textContent = result.feedback || result.transcription || "";
}

async function nextDrill() {
  if (!state.drill.active) return;
  try {
    const data = await API.drillNext();
    renderDrill(data);
  } catch (e) { setBanner("下一句失败: " + e.message, "error"); }
}

async function prevDrill() {
  if (!state.drill.active) return;
  try {
    const data = await API.drillPrev();
    renderDrill(data);
  } catch (e) { setBanner("上一句失败: " + e.message, "error"); }
}

async function finishDrill() {
  if (!state.drill.active) return;
  try {
    const fin = await API.drillFinish();
    renderDrillOutcome(fin.outcome);
    state.drill.active = false;
    setDrillReady(false);
    setBanner("专项已结束", "ready");
  } catch (e) { setBanner("结束失败: " + e.message, "error"); }
}

function renderDrillOutcome(outcome) {
  el("drill-outcome").textContent = outcome
    ? `完成 ${outcome.total_attempts} 次 · 通过 ${outcome.total_passed} · 平均 ${outcome.avg_score} · ${outcome.mastered ? "已掌握 ✔" : "未掌握"}`
    : "";
}

function setDrillLocked(locked) {
  el("btn-drill-play").disabled = locked;
  el("btn-drill-play-slow").disabled = locked;
  el("btn-drill-next").disabled = locked;
  el("btn-drill-record").disabled = locked;
  el("btn-drill-play-user").disabled = locked || !state.drillWav;
}

/* ---------- 弱点 ---------- */

const RANGE_LABELS = {
  last_7d: "近 7 天", last_14d: "近 14 天", last_30d: "近 30 天", last_60d: "近 60 天",
  last_30: "最近 30 次", last_100: "最近 100 次", last_300: "最近 300 次",
};

async function loadWeakness() {
  const range = el("weakness-range").value || "last_30d";
  try {
    const data = await API.weakness(range);
    // 填充范围选项（首次）
    if (!el("weakness-range").dataset.filled) {
      (data.range_options || []).forEach((o) => {
        const opt = document.createElement("option");
        opt.value = o.key;
        opt.textContent = RANGE_LABELS[o.key] || o.label;
        el("weakness-range").appendChild(opt);
      });
      el("weakness-range").dataset.filled = "1";
      el("weakness-range").value = data.range_key || "last_30d";
    }
    renderSuggestions(data.suggestions);
    renderWeakWords(data.words);
    renderWeakPhonemes(data.phonemes);
    el("weakness-suggestions").dataset.wpm = data.recent_wpm;
  } catch (err) {
    setBanner("弱点加载失败: " + err.message, "error");
  }
}

function renderSuggestions(suggestions) {
  const container = el("weakness-suggestions");
  container.innerHTML = "";
  (suggestions || []).forEach((s) => {
    const card = document.createElement("div");
    card.className = "suggestion-card";
    card.innerHTML = `<div class="s-title">${esc(s.title)}</div><div class="s-body">${esc(s.body)}</div>`;
    const btn = document.createElement("button");
    btn.className = "btn small";
    btn.textContent = "专项练习";
    btn.addEventListener("click", () => {
      el("drill-target-input").value = s.target?.value || "";
      switchMode("drill");
      startDrill();
    });
    card.appendChild(btn);
    container.appendChild(card);
  });
}

function renderWeakWords(words) {
  const tbody = el("weakness-words");
  tbody.innerHTML = "";
  (words || []).forEach((w) => {
    const tr = document.createElement("tr");
    const status = { need_practice: "需练习", watch: "关注", improving: "进步", other: "" }[w.status] || w.status;
    tr.innerHTML = `<td>${esc(w.word)}</td><td>${esc(status)}</td><td>${w.count}</td><td>${(w.error_rate * 100).toFixed(0)}%</td>`;
    const td = document.createElement("td");
    const btn = document.createElement("button");
    btn.className = "btn small";
    btn.textContent = "专项";
    btn.addEventListener("click", () => {
      el("drill-target-input").value = w.word;
      switchMode("drill");
      startDrill();
    });
    td.appendChild(btn);
    tr.appendChild(td);
    tbody.appendChild(tr);
  });
}

function renderWeakPhonemes(phonemes) {
  const container = el("weakness-phonemes");
  container.innerHTML = "";
  (phonemes || []).forEach((p) => {
    const cell = document.createElement("div");
    const sev = p.severity_avg || 0;
    const cls = sev > 0.5 ? "bad" : sev > 0.25 ? "warn" : "ok";
    cell.className = "phoneme-cell " + cls;
    cell.innerHTML = `<span class="p-ipa">/${esc(p.ipa)}/</span><span class="p-count">${p.count}×</span><span class="p-score">${(p.avg_score * 100).toFixed(0)}%</span>`;
    const btn = document.createElement("button");
    btn.className = "btn small";
    btn.textContent = "专项";
    btn.addEventListener("click", () => {
      el("drill-target-input").value = p.ipa;
      switchMode("drill");
      startDrill();
    });
    cell.appendChild(btn);
    container.appendChild(cell);
  });
}

/* ---------- 历史浏览 ---------- */

async function loadHistoryFull() {
  try {
    const data = await API.history();
    const container = el("history-full");
    container.innerHTML = "";
    const sessions = (data.sessions || []).slice().reverse();
    sessions.forEach((s) => {
      const block = document.createElement("div");
      block.className = "history-block";
      const head = document.createElement("div");
      head.className = "history-block-head";
      const takes = (s.takes || []);
      const avg = s.totals?.avg_best_score;
      head.innerHTML = `<span class="hs-id">${esc(s.id || "")}</span><span>${esc(s.started_at || "")}</span><span class="hs-takes">${takes.length} 次</span><span class="hs-avg">均 ${avg != null ? Math.round(avg) : "—"}</span>`;
      const delBtn = document.createElement("button");
      delBtn.className = "btn small danger";
      delBtn.textContent = "删除会话";
      delBtn.addEventListener("click", async () => {
        if (!confirm("删除该会话及其所有记录？")) return;
        await API.deleteSession(s.id);
        loadHistoryFull();
      });
      head.appendChild(delBtn);
      block.appendChild(head);
      const rev = takes.slice().reverse();
      // Trend arrows compare each take to the previous take of the same phrase.
      const prevByPhrase = {};
      rev.forEach((t) => {
        const row = document.createElement("div");
        row.className = "history-row";
        const phrase = (t.phrase || "").trim();
        const prev = prevByPhrase[phrase];
        let trend = "·", trendCls = "";
        if (prev == null) { trend = "·"; trendCls = ""; }
        else if (t.score > prev) { trend = "▲"; trendCls = "up"; }
        else if (t.score < prev) { trend = "▼"; trendCls = "down"; }
        else { trend = "–"; trendCls = ""; }
        prevByPhrase[phrase] = t.score;
        // Score chip (grade if present, else numeric).
        const chipText = t.grade || Math.round(t.score);
        row.innerHTML = `<span class="hscore ${scoreClass(t.score, state.settings?.score_threshold)}">${esc(String(chipText))}</span>` +
          `<span class="htrend ${trendCls}">${trend}</span>` +
          `<span class="hphrase">${esc(phrase)}</span>` +
          `<span class="htime">${esc(fmtTime(t.timestamp))}</span>`;
        // Error-word summary (first 3 words).
        const ews = (t.error_words || []).slice(0, 3).map((w) =>
          typeof w === "string" ? w : (w.word || "")).filter(Boolean).join(", ");
        if (ews) {
          const ew = document.createElement("span");
          ew.className = "hew";
          ew.textContent = `错:${ews}`;
          ew.title = ews;
          row.appendChild(ew);
        }
        // Play recording (only when audio is stored, like the desktop).
        if (t.audio_files && (data.history_mode || state.settings?.history_mode) === "with_audio") {
          const p = document.createElement("button");
          p.className = "btn small";
          p.textContent = "▶";
          p.title = "播放录音";
          p.addEventListener("click", () => playTakeAudio(s.id, t.id));
          row.appendChild(p);
        }
        // Re-drill (desktop: first error word, else phrase free-form).
        const rd = document.createElement("button");
        rd.className = "btn small";
        rd.textContent = "专项";
        rd.title = "对该句最弱词做专项练习";
        rd.addEventListener("click", () => startDrillFromTake(t));
        row.appendChild(rd);
        const d = document.createElement("button");
        d.className = "hdanger";
        d.textContent = "✕";
        d.title = "删除该条记录";
        d.addEventListener("click", async () => {
          if (!confirm("删除该条记录？")) return;
          await API.deleteTake(s.id, t.id);
          loadHistoryFull();
        });
        row.appendChild(d);
        block.appendChild(row);
      });
      container.appendChild(block);
    });
    if (data.drill_sessions?.length) {
      const h = document.createElement("h3");
      h.textContent = "专项记录";
      container.appendChild(h);
      data.drill_sessions.slice().reverse().forEach((d) => {
        const row = document.createElement("div");
        row.className = "history-row";
        row.innerHTML = `<span class="hphrase">专项:${esc(d.target?.value || "")}</span><span class="htime">${esc(d.started_at || "")}</span><span class="hscore">${d.avg_score ?? "—"}</span>`;
        container.appendChild(row);
      });
    }
    if (data.dictation_sessions?.length) {
      const h = document.createElement("h3");
      h.textContent = "听写记录";
      container.appendChild(h);
      data.dictation_sessions.slice().reverse().forEach((d) => {
        const row = document.createElement("div");
        row.className = "history-row";
        row.innerHTML = `<span class="hphrase">听写:${d.attempts ?? 0} 次</span><span class="htime">${esc(d.started_at || "")}</span><span class="hscore">${d.avg_score ?? "—"}</span>`;
        container.appendChild(row);
      });
    }
  } catch (err) {
    setBanner("历史加载失败: " + err.message, "error");
  }
}

// Play a stored take's recording (desktop history Play ▶). The server returns
// the WAV bytes as base64; build a Blob and play it.
async function playTakeAudio(sessionId, takeId) {
  try {
    const data = await API.takeAudio(sessionId, takeId);
    if (!data || !data.wav_base64) { setBanner("无录音可播放", "error"); return; }
    const bytes = atob(data.wav_base64);
    const arr = new Uint8Array(bytes.length);
    for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
    stopUser();
    const url = URL.createObjectURL(new Blob([arr], { type: "audio/wav" }));
    const a = new Audio(url);
    a.play();
    state.userSrc = a;
  } catch (err) {
    setBanner("播放失败: " + err.message, "error");
  }
}

// Start a drill from a past take's weakest word (desktop Re-drill). Falls back
// to a phoneme (first phoneme error) or a free-form drill of the phrase.
function startDrillFromTake(take) {
  const ews = take.error_words || [];
  if (ews.length) {
    const word = typeof ews[0] === "string" ? ews[0] : (ews[0].word || "");
    if (word) { startDrillWithTarget({ type: "word", value: word }); return; }
  }
  const ps = take.phoneme_scores || [];
  if (ps.length) {
    const phoneme = typeof ps[0] === "string" ? ps[0] : (ps[0].ipa || "");
    if (phoneme) { startDrillWithTarget({ type: "phoneme", value: phoneme }); return; }
  }
  const phrase = (take.phrase || "").trim() || "The thought is a thought.";
  startDrillWithTarget({ type: "free_form", value: phrase });
}

function startDrillWithTarget(target) {
  el("drill-target-input").value = target.value || "";
  state.drill.target = target;
  switchMode("drill");
  startDrill();
}

async function exportHistory() {
  try {
    const data = await API.exportHistory();
    const blob = new Blob([data.content], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = data.filename;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    setBanner("导出失败: " + err.message, "error");
  }
}

async function clearHistory() {
  if (!confirm("清空所有历史记录？此操作不可撤销。")) return;
  if (!confirm("再次确认：清空全部历史？")) return;
  try {
    await API.clearHistory();
    loadHistoryFull();
    setBanner("历史已清空", "ready");
  } catch (err) {
    setBanner("清空失败: " + err.message, "error");
  }
}

/* ---------- 统计看板 ---------- */

function fmtSec(sec) {
  sec = Number(sec) || 0;
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return `${h}h ${m}m`;
}

async function loadDashboard() {
  try {
    const data = await API.dashboard();
    const c = data.cards;
    el("stats-cards").innerHTML = `
      <div class="stat-card"><span class="s-label">总时长</span><span class="s-value">${fmtSec(c.total_time_sec)}</span></div>
      <div class="stat-card"><span class="s-label">会话</span><span class="s-value">${c.sessions}</span></div>
      <div class="stat-card"><span class="s-label">次数</span><span class="s-value">${c.takes}</span></div>
      <div class="stat-card"><span class="s-label">短语</span><span class="s-value">${c.unique_phrases}</span></div>
      <div class="stat-card"><span class="s-label">均分</span><span class="s-value">${c.avg_score}</span></div>
      <div class="stat-card"><span class="s-label">最佳</span><span class="s-value">${c.best_score}</span></div>
      <div class="stat-card"><span class="s-label">连续达标</span><span class="s-value">${c.streak} 天</span></div>`;
    renderLineChart(el("stats-daily"), data.daily_avg);
    renderBars(el("stats-dist"), data.distribution);
    renderDurationTable(el("stats-duration"), data.duration_rows);
    renderActivityHeatmap(el("stats-activity"), data.activity);
    const recent = el("stats-recent");
    recent.innerHTML = "";
    (data.recent_sessions || []).forEach((s) => {
      const row = document.createElement("div");
      row.className = "history-row";
      row.innerHTML = `<span class="hphrase">${esc(s.id || "")}</span><span class="htime">${esc(s.started_at || "")}</span><span class="hscore">${s.takes} 次</span><span>${fmtSec(s.duration_sec)}</span>`;
      recent.appendChild(row);
    });
  } catch (err) {
    setBanner("统计加载失败: " + err.message, "error");
  }
}

function renderLineChart(container, points) {
  container.innerHTML = "";
  const vals = (points || []).map((p) => p.score).filter((v) => v != null);
  if (!vals.length) { container.textContent = "暂无数据"; return; }
  const max = 100;
  const w = container.clientWidth || 500, h = 120;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "100%"); svg.setAttribute("height", h); svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const pts = (points || []).map((p, i) => {
    const x = (i / Math.max(1, (points.length - 1))) * (w - 10) + 5;
    const y = h - 10 - (p.score != null ? (p.score / max) : 0) * (h - 20);
    return [x, y, p.score];
  });
  let path = "";
  pts.forEach(([x, y], i) => { path += (i ? " L" : "M") + x + " " + y; });
  const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
  line.setAttribute("d", path);
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", "var(--accent)");
  line.setAttribute("stroke-width", "2");
  svg.appendChild(line);
  container.appendChild(svg);
}

function renderBars(container, dist) {
  container.innerHTML = "";
  const counts = dist?.counts || [];
  const max = Math.max(1, ...counts);
  const w = container.clientWidth || 500;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "100%"); svg.setAttribute("height", "120"); svg.setAttribute("viewBox", `0 0 ${w} 120`);
  const labels = dist?.buckets || [];
  counts.forEach((c, i) => {
    const bw = (w / counts.length) * 0.6;
    const x = (i + 0.2) * (w / counts.length);
    const bh = (c / max) * 100;
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", x); rect.setAttribute("y", 110 - bh);
    rect.setAttribute("width", bw); rect.setAttribute("height", bh);
    rect.setAttribute("fill", "var(--accent)");
    svg.appendChild(rect);
    const txt = document.createElementNS("http://www.w3.org/2000/svg", "text");
    txt.setAttribute("x", x + bw / 2); txt.setAttribute("y", 120);
    txt.setAttribute("font-size", "10"); txt.setAttribute("text-anchor", "middle");
    txt.textContent = labels[i] || "";
    svg.appendChild(txt);
  });
  container.appendChild(svg);
}

function renderDurationTable(container, rows) {
  container.innerHTML = "";
  (rows || []).forEach((r) => {
    const row = document.createElement("div");
    row.className = "history-row";
    row.innerHTML = `<span class="hphrase">${esc(r.date)}</span><span class="htime">${r.takes} 次</span><span class="hscore">${fmtSec(r.seconds)}</span>`;
    container.appendChild(row);
  });
}

// Activity heatmap (last 28 days). One cell per day, coloured by practice
// seconds; more practice = brighter. Mirrors desktop ui_stats heatmap.
function renderActivityHeatmap(container, activity) {
  container.innerHTML = "";
  const days = activity || [];
  if (!days.length) { container.textContent = "暂无数据"; return; }
  const max = Math.max(1, ...days.map((d) => d.seconds || 0));
  const grid = document.createElement("div");
  grid.className = "heatmap";
  days.forEach((d) => {
    const cell = document.createElement("div");
    cell.className = "heat-cell";
    cell.title = `${d.date} — ${fmtSec(d.seconds)}`;
    const secs = d.seconds || 0;
    if (secs <= 0) {
      cell.classList.add("none");
    } else {
      const ratio = secs / max;
      cell.classList.add(ratio > 0.75 ? "hi" : ratio > 0.4 ? "mid" : "lo");
    }
    const label = document.createElement("span");
    label.className = "heat-label";
    label.textContent = d.date.slice(5);
    cell.appendChild(label);
    grid.appendChild(cell);
  });
  container.appendChild(grid);
}

/* ---------- 设置 ---------- */

const SETTING_FIELDS = [
  { key: "ui_language", label: "界面语言", type: "select", from: "ui_language_choices" },
  { key: "user_name", label: "用户名", type: "text" },
  { key: "voice", label: "音色", type: "select", from: "voices" },
  { key: "reference_speed", label: "参考语速", type: "number", step: "0.1", min: "0.7", max: "1.4" },
  { key: "fluency_enabled", label: "流畅度评分", type: "checkbox" },
  { key: "fluency_weight", label: "流畅度权重", type: "number", step: "1", min: "0", max: "100" },
  { key: "shadowing_enabled", label: "跟读评分", type: "checkbox" },
  { key: "shadowing_weight", label: "跟读权重", type: "number", step: "1", min: "0", max: "100" },
  { key: "pass_gating_enabled", label: "达标才下一句", type: "checkbox" },
  { key: "auto_play_phrase", label: "自动播放参考", type: "checkbox" },
  { key: "phrase_source", label: "短语来源", type: "select", from: "phrase_source_choices" },
  { key: "phrase_length", label: "短语长度", type: "select", from: "phrase_length_choices" },
  { key: "max_record_seconds", label: "最长录音（秒）", type: "number", step: "1" },
  { key: "silence_timeout", label: "静音自动停止（秒）", type: "number", step: "1" },
  { key: "silence_threshold", label: "静音阈值", type: "number", step: "0.01" },
  { key: "history_mode", label: "历史模式", type: "select", from: "history_modes" },
  { key: "daily_target_minutes", label: "每日目标（分钟）", type: "number", step: "1" },
  { key: "native_language", label: "母语", type: "text" },
  { key: "drill_pass_threshold", label: "专项达标分", type: "number", step: "1" },
  { key: "drill_max_attempts", label: "专项最大次数", type: "number", step: "1" },
  { key: "dictation_pass_threshold", label: "听写达标分", type: "number", step: "1" },
  { key: "dictation_max_attempts", label: "听写最大次数", type: "number", step: "1" },
  { key: "dictation_random_order", label: "听写乱序", type: "checkbox" },
  { key: "dictation_review_enabled", label: "听写复习", type: "checkbox" },
  { key: "stats_enabled", label: "统计", type: "checkbox" },
];

function renderSettingsForm() {
  const s = state.settings;
  if (!s) return;
  const form = el("settings-form");
  form.innerHTML = "";
  const restartSet = new Set(s.restart_required_keys || []);
  SETTING_FIELDS.forEach((f) => {
    const row = document.createElement("div");
    row.className = "setting-row";
    const label = document.createElement("label");
    label.textContent = f.label + (restartSet.has(f.key) ? "（需重启）" : "");
    row.appendChild(label);
    let input;
    if (f.type === "checkbox") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!s[f.key];
      input.addEventListener("change", () => saveSetting(f.key, input.checked));
    } else if (f.type === "select") {
      input = document.createElement("select");
      const choices = (f.from === "history_modes")
        ? ["with_audio", "metadata_only", "none"]
        : (s[f.from] || []);
      choices.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = c;
        if (s[f.key] === c) opt.selected = true;
        input.appendChild(opt);
      });
      input.addEventListener("change", () => saveSetting(f.key, input.value));
    } else {
      input = document.createElement("input");
      input.type = f.type || "text";
      input.value = s[f.key] ?? "";
      if (f.step) input.step = f.step;
      if (f.min != null) input.min = f.min;
      if (f.max != null) input.max = f.max;
      const apply = () => {
        const v = input.type === "number" ? Number(input.value) : input.value;
        saveSetting(f.key, v);
      };
      input.addEventListener("change", apply);
      input.addEventListener("blur", apply);
    }
    row.appendChild(input);
    form.appendChild(row);
  });
}

async function saveSetting(key, value) {
  try {
    const r = await API.saveSetting(key, value);
    if (r.restart_required) {
      setBanner(`设置已保存（${key} 需重启生效）`, "loading");
    } else {
      setBanner("设置已保存", "ready");
    }
    state.settings[key] = value;
  } catch (err) {
    setBanner("保存失败: " + err.message, "error");
  }
}

/* ---------- 源文本 ---------- */

async function loadSourceText() {
  try {
    const d = await API.sourceText();
    state.sourceText = d.text || "";
    el("source-text").value = state.sourceText;
  } catch (e) { /* 源文本加载失败不阻塞 */ }
}

async function saveSourceText() {
  const text = el("source-text").value;
  try {
    await API.saveSourceText(text);
    state.sourceText = text;
    setBanner("练习文本已保存", "ready");
  } catch (err) {
    setBanner("保存失败: " + err.message, "error");
  }
}

/* ---------- 启动 ---------- */

async function init() {
  try {
    const h = await API.health();
    state.settings = h.settings || await API.settings();
    el("engine-label").textContent = `引擎: ${h.engine} · LLM: ${h.llm_backend}`;
    await loadSourceText();
    if (h.ready) {
      setBanner("就绪", "ready");
      setControlsReady(true);
      await generatePhrase();
    } else {
      setBanner("模型加载中… 请稍候", "loading");
      pollReady();
    }
    loadHistory();
    refreshSession();
    // 初始化模式导航
    document.querySelectorAll(".mode-btn").forEach((b) => {
      b.addEventListener("click", () => switchMode(b.dataset.mode));
    });
    bindEvents();
  } catch (err) {
    setBanner("无法连接后端: " + err.message, "error");
  }
}

async function pollReady() {
  const timer = setInterval(async () => {
    try {
      const h = await API.health();
      if (h.ready) {
        clearInterval(timer);
        setBanner("就绪", "ready");
        setControlsReady(true);
        await generatePhrase();
      } else if (h.load_error) {
        clearInterval(timer);
        setBanner("初始化失败: " + h.load_error, "error");
      }
    } catch (e) { /* 重试 */ }
  }, 2000);
}

async function refreshSession() {
  try {
    const s = await API.sessionStats();
    el("session-summary").textContent = `本次：${s.takes} 次 · 均 ${s.avg} · 最佳 ${s.best}`;
  } catch (e) {}
}

function bindEvents() {
  el("btn-play-ref").addEventListener("click", () => playReference(false));
  el("btn-play-slow").addEventListener("click", () => playReference(true));
  el("btn-next").addEventListener("click", generatePhrase);
  el("btn-record").addEventListener("click", () => state.recording ? stopRecording() : startRecording("practice"));
  el("btn-play-user").addEventListener("click", playUser);
  el("btn-refresh-history").addEventListener("click", loadHistory);
  el("btn-save-source").addEventListener("click", saveSourceText);
  // 听写
  el("btn-dict-start").addEventListener("click", startDictation);
  el("btn-dict-play").addEventListener("click", () => playDictation(false));
  el("btn-dict-play-slow").addEventListener("click", () => playDictation(true));
  el("btn-dict-submit").addEventListener("click", submitDictation);
  el("btn-dict-skip").addEventListener("click", skipDictation);
  el("btn-dict-prev").addEventListener("click", prevDictation);
  el("btn-dict-finish").addEventListener("click", finishDictation);
  // 专项
  el("btn-drill-start").addEventListener("click", startDrill);
  el("btn-drill-play").addEventListener("click", () => { if (state.drillWav) playBuffer(state.drillWav, false); });
  el("btn-drill-play-slow").addEventListener("click", () => { if (state.drillWav) playBuffer(state.drillWav, true); });
  el("btn-drill-next").addEventListener("click", nextDrill);
  el("btn-drill-record").addEventListener("click", () => state.recording ? stopRecording() : startRecording("drill"));
  el("btn-drill-play-user").addEventListener("click", () => { if (state.drillWav) playBuffer(state.drillWav, false); });
  el("btn-drill-finish").addEventListener("click", finishDrill);
  // 弱点
  el("weakness-range").addEventListener("change", loadWeakness);
  // 历史
  el("btn-history-refresh").addEventListener("click", loadHistoryFull);
  el("btn-history-export").addEventListener("click", exportHistory);
  el("btn-history-clear").addEventListener("click", clearHistory);
  // 统计
  el("btn-stats-refresh").addEventListener("click", loadDashboard);
}

init();
