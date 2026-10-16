// Запись голоса в браузере и перевод в WAV 16 кГц моно — так его принимает модель распознавания речи.
const VoiceRecorder = (() => {
  async function start() {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Браузер не даёт доступ к микрофону");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const rec = new MediaRecorder(stream);
    const chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.start();
    return {
      cancel: () => { if (rec.state !== "inactive") rec.stop(); stream.getTracks().forEach((t) => t.stop()); },
      stop: () => new Promise((resolve) => {
        rec.onstop = async () => {
          stream.getTracks().forEach((t) => t.stop());
          const raw = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
          try { resolve(await toWav(raw)); } catch (e) { resolve(raw); }
        };
        rec.stop();
      }),
    };
  }

  async function toWav(blob, maxSeconds = Infinity) {
    const buf = await blob.arrayBuffer();
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    let decoded;
    try { decoded = await ctx.decodeAudioData(buf); } finally { await ctx.close(); }
    if (!decoded.duration || decoded.duration > maxSeconds) throw new Error("Запись должна быть не длиннее 2 минут");
    const rate = 16000;
    const off = new OfflineAudioContext(1, Math.ceil(decoded.duration * rate), rate);
    const src = off.createBufferSource();
    src.buffer = decoded; src.connect(off.destination); src.start();
    const rendered = await off.startRendering();
    return encodeWav(rendered.getChannelData(0), rate);
  }

  function encodeWav(samples, rate) {
    const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
    const w = (o, s) => [...s].forEach((ch, i) => view.setUint8(o + i, ch.charCodeAt(0)));
    w(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
    view.setUint16(34, 16, true); w(36, "data"); view.setUint32(40, samples.length * 2, true);
    let o = 44;
    for (const s of samples) { const v = Math.max(-1, Math.min(1, s)); view.setInt16(o, v < 0 ? v * 0x8000 : v * 0x7fff, true); o += 2; }
    return new Blob([view], { type: "audio/wav" });
  }

  return { start, toWav };
})();
