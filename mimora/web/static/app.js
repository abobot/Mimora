/* Mimora 网页版 - 前端逻辑
 *
 * 流程：加载设置 → 生成短语 → 播放参考音频 → 浏览器录音(getUserMedia)
 *      → 上传 WAV 给后端评分 → 显示反馈 → 下一句。
 *
 * 录音在浏览器侧完成（Web Audio API），后端只负责合成参考音与评分。
 * 所有音频上传为 16-bit PCM WAV；录音采样率统一为 16 kHz 以匹配引擎。
 */

"use strict";

const API = {
  async health() {
    const r = await fetch("/api/health");
    return r.json();
  },
  async settings() {
    const r = await fetch("/api/settings");
    return r.json();
  },
  async generatePhrase(sourceText, phraseSource) {
    const r = await fetch("/api/phrase", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_text: sourceText, length: "full", phrase_source: phraseSource }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    return r.json();
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
  async sessionStats() {
    const r = await fetch("/api/stats/session");
    return r.json();
  },
  async history() {
    const r = await fetch("/api/stats/history?lookback=50");
    return r.json();
  },
};

const el = (id) => document.getElementById(id);

const state = {
  ready: false,
  settings: null,
  phrase: "",
  translation: "",
  referenceWav: null, // { audioBuffer, sampleRate }
  referenceSrc: null,
  userBlob: null,     // 最近一次录音的 WAV Blob
  userSrc: null,
  recording: false,
  mediaStream: null,
  chunks: [],
  sampleRate: 16000,
  phraseSource: "llm", // 'llm' | 'practice'
  sourceText: "",
};

/* ---------- 音频工具 ---------- */

function wavToWavBlob(float32, sampleRate) {
  // 将浏览器录音的 Float32 采样重采样到 16 kHz 单声道，输出 16-bit PCM WAV。
  // 浏览器以 context.sampleRate 采样；后端引擎要 16 kHz，故重采样。
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
  // 录音期间锁定参考/我的录音播放与下一句，与桌面端 enter_recording
  // 一致：参考音或上一句录音会从扬声器漏进麦克风，污染本次录音的评分。
  el("btn-play-user").disabled = !state.userBlob || rec;
}

function showFeedback(result) {
  const scoreEl = el("feedback-score");
  scoreEl.textContent = result.score > 0 ? Math.round(result.score) : "—";
  scoreEl.className = "feedback-score " + (result.score >= (state.settings?.score_threshold || 70) ? "pass" : "fail");
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
  const s = el("session-summary");
  s.textContent = `本次：${result.session_takes} 次 · 均 ${result.session_avg} · 最佳 ${result.session_best}`;
}

/* ---------- 录音 ---------- */

async function ensureMic() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("浏览器不支持 getUserMedia（请使用较新的 Chrome/Edge）");
  }
  state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  return state.mediaStream;
}

async function startRecording() {
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
      // 缓存 PCM 片段用于评分
      state.chunks.push(new Float32Array(ch));
      let sum = 0;
      for (let i = 0; i < ch.length; i++) sum += ch[i] * ch[i];
      level = Math.sqrt(sum / ch.length);
      el("rec-level-bar").style.width = Math.min(100, level * 400) + "%";
      // 处理器必须连到 destination 才会被渲染（onaudioprocess 才触发）。
      // 清零输出缓冲，确保不会把录音信号回放出来（避免任何浏览器上的反馈）。
      const out = e.outputBuffer.getChannelData(0);
      out.fill(0);
    };
    source.connect(processor);
    processor.connect(ctx.destination);
    state.recording = true;
    el("btn-record").textContent = "■ 停止";
    el("btn-record").classList.add("recording");
    setControlsReady(true, true); // 录音中：锁参考/我的录音/下一句
    startTimer();
  } catch (err) {
    setBanner("录音启动失败: " + err.message, "error");
  }
}

function stopRecording() {
  if (!state.recording) return;
  state.recording = false;
  el("btn-record").textContent = "● 开始录音";
  el("btn-record").classList.remove("recording");
  stopTimer();
  // 停止处理器与流，然后处理已采集的 PCM
  state._processor?.disconnect();
  state._ctx?.close();
  state._stream?.getTracks().forEach((t) => t.stop());
  processPcm(state.chunks, state._sampleRate || 16000);
}

function processPcm(chunks, sampleRate) {
  if (!chunks.length) {
    setBanner("没有采到音频", "error");
    setControlsReady(true, false); // 无音频也要解锁按钮
    return;
  }
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const flat = new Float32Array(total);
  let off = 0;
  for (const c of chunks) { flat.set(c, off); off += c.length; }
  wavToWavBlob(flat, sampleRate).then(async (wavBlob) => {
    state.userBlob = wavBlob;
    el("btn-play-user").disabled = false;
    await uploadAndScore(wavBlob);
  }).catch((err) => {
    setBanner("录音处理失败: " + err.message, "error");
    setControlsReady(true, false); // 处理失败也要解锁按钮
  });
}

async function uploadAndScore(wavBlob) {
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
    // 录音结束后恢复全部按钮（参考/我的录音/下一句在录音期间被锁定）。
    setControlsReady(true, false);
  }
}

/* ---------- 计时器（仅 UI 显示） ---------- */

let timerInterval = null;
let timerSec = 0;
function startTimer() {
  timerSec = 0;
  el("rec-timer").textContent = "0:00";
  timerInterval = setInterval(() => {
    timerSec++;
    const m = Math.floor(timerSec / 60), s = timerSec % 60;
    el("rec-timer").textContent = `${m}:${s.toString().padStart(2, "0")}`;
  }, 1000);
}
function stopTimer() { clearInterval(timerInterval); }

/* ---------- 播放 ---------- */

async function loadReferenceAudio(base64, sampleRate) {
  const blob = base64ToWav(base64, sampleRate);
  const arrayBuffer = await blob.arrayBuffer();
  const ctx = new AudioContext();
  state.referenceWav = await ctx.decodeAudioData(arrayBuffer);
  ctx.close();
}

function playReference(slow) {
  if (!state.referenceWav) return;
  stopReference();
  const src = new AudioContext();
  const node = src.createBufferSource();
  node.buffer = state.referenceWav;
  node.playbackRate.value = slow ? 0.7 : 1.0;
  node.connect(src.destination);
  node.start();
  state.referenceSrc = { ctx: src, node };
}

function stopReference() {
  if (state.referenceSrc) {
    try { state.referenceSrc.node.stop(); state.referenceSrc.ctx.close(); } catch (e) {}
    state.referenceSrc = null;
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

/* ---------- 短语 ---------- */

async function generatePhrase() {
  stopReference();
  setBanner("生成短语…", "loading");
  el("phrase-text").textContent = "…";
  el("translation").textContent = "";
  try {
    const data = await API.generatePhrase(state.sourceText, state.phraseSource);
    state.phrase = data.phrase;
    state.translation = data.translation || "";
    state.referenceWav = null;
    el("phrase-text").textContent = data.phrase;
    el("translation").textContent = data.translation || "";
    el("voice-label").textContent = data.voice || "";
    await loadReferenceAudio(data.reference_wav_base64, data.reference_sr);
    setControlsReady(true);
    setBanner("就绪", "ready");
    // 自动播放参考音（与桌面端 auto_play_phrase 一致）
    if (state.settings?.auto_play_phrase) playReference(false);
    // 清除旧的反馈
    el("feedback-score").textContent = "—";
    el("feedback-grade").textContent = "";
    el("feedback-text").textContent = "";
    el("word-errors").innerHTML = "";
  } catch (err) {
    setBanner("生成失败: " + err.message, "error");
    el("phrase-text").textContent = "生成失败，请重试";
  }
}

/* ---------- 历史 ---------- */

async function loadHistory() {
  try {
    const data = await API.history();
    const list = el("history-list");
    list.innerHTML = "";
    (data.takes || []).slice().reverse().forEach((t) => {
      const row = document.createElement("div");
      row.className = "history-row";
      const score = document.createElement("span");
      score.className = "hscore " + (t.score >= (state.settings?.score_threshold || 70) ? "pass" : "fail");
      score.textContent = Math.round(t.score);
      const phrase = document.createElement("span");
      phrase.className = "hphrase";
      phrase.textContent = t.phrase;
      const time = document.createElement("span");
      time.className = "htime";
      time.textContent = (t.timestamp || "").replace("T", " ").slice(5, 16);
      row.appendChild(score); row.appendChild(phrase); row.appendChild(time);
      list.appendChild(row);
    });
  } catch (e) { /* 历史加载失败不阻塞练习 */ }
}

/* ---------- 启动 ---------- */

async function init() {
  try {
    const h = await API.health();
    state.settings = h.settings || await API.settings();
    el("engine-label").textContent = `引擎: ${h.engine} · LLM: ${h.llm_backend}`;
    if (h.ready) {
      setBanner("就绪", "ready");
      setControlsReady(true);
      await generatePhrase();
    } else {
      setBanner("模型加载中… 请稍候", "loading");
      // 轮询直到就绪
      pollReady();
    }
    loadHistory();
    refreshSession();
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

/* ---------- 事件绑定 ---------- */

el("btn-play-ref").addEventListener("click", () => playReference(false));
el("btn-play-slow").addEventListener("click", () => playReference(true));
el("btn-next").addEventListener("click", generatePhrase);
el("btn-record").addEventListener("click", () => state.recording ? stopRecording() : startRecording());
el("btn-play-user").addEventListener("click", playUser);
el("btn-refresh-history").addEventListener("click", loadHistory);

init();
