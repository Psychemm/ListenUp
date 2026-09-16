// Offscreen document: fetches WAV chunks from the server and plays them back to back.
// Keeps one chunk prefetched so there is no gap while the next one downloads.

let current = null; // { jobId, serverUrl, index, audio, cancelled, rate, chunks: Map<number, Blob|null>, segments, total }
let audio = new Audio();
audio.preload = "auto";

// Route the element through a gain node so volume can go above 100%.
let ctx = null, gain = null;
function setVolume(v) {
  try {
    if (!ctx) {
      ctx = new AudioContext();
      gain = ctx.createGain();
      ctx.createMediaElementSource(audio).connect(gain);
      gain.connect(ctx.destination);
    }
    if (ctx.state === "suspended") ctx.resume();
    gain.gain.value = Math.max(0, Math.min(2, Number(v) || 1));
  } catch (e) {
    audio.volume = Math.max(0, Math.min(1, Number(v) || 1));
  }
}

function report(patch) {
  chrome.runtime.sendMessage({ type: "player-state", patch }).catch(() => {});
}

async function fetchChunk(job, n) {
  while (!job.cancelled) {
    const r = await fetch(`${job.serverUrl}/jobs/${job.jobId}/chunks/${n}`);
    if (r.status === 200) return await r.blob();
    if (r.status === 204) return null;              // end of job
    if (r.status === 202) continue;                 // still generating; long-poll again
    throw new Error(`chunk ${n}: HTTP ${r.status} ${(await r.text()).slice(0, 200)}`);
  }
  return null;
}

async function pollStatus(job) {
  try {
    const r = await fetch(`${job.serverUrl}/jobs/${job.jobId}`);
    if (!r.ok) return;
    const s = await r.json();
    job.segments = s.segments || [];
    job.total = s.done ? s.chunks_ready : (s.prep_done ? s.segments.length : 0);
    report({ total: job.total, ready: s.chunks_ready, segment: job.segments[job.index] || "", llmUsed: s.llm_used, llmNote: s.llm_note });
    if (s.error) report({ status: "error", error: s.error });
  } catch (e) { /* server may be gone */ }
}

function getChunk(job, n) {
  if (!job.chunks.has(n)) job.chunks.set(n, fetchChunk(job, n));
  return job.chunks.get(n);
}

function playBlob(blob, rate) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    audio.src = url;
    audio.playbackRate = rate;
    audio.onended = () => { URL.revokeObjectURL(url); resolve("ended"); };
    audio.onerror = () => { URL.revokeObjectURL(url); reject(new Error("audio decode/playback error")); };
    audio.play().catch(reject);
  });
}

async function run(job) {
  report({ status: "loading", index: 0 });
  const statusTimer = setInterval(() => pollStatus(job), 1500);
  try {
    let n = 0;
    while (!job.cancelled) {
      job.index = n;
      const blob = await getChunk(job, n);
      if (job.cancelled) break;
      if (!blob) break; // finished
      getChunk(job, n + 1); // prefetch next while this one plays
      report({ status: "playing", index: n, segment: job.segments[n] || "" });
      job.skipResolve = null;
      const outcome = await Promise.race([
        playBlob(blob, job.rate),
        new Promise(res => { job.skipResolve = res; }),
      ]);
      if (job.cancelled) break;
      if (typeof outcome === "number") { n = Math.max(0, n + outcome); audio.pause(); continue; }
      n += 1;
    }
    if (!job.cancelled) report({ status: "idle", index: 0, segment: "", jobId: null });
  } catch (e) {
    if (!job.cancelled) report({ status: "error", error: String(e.message || e) });
  } finally {
    clearInterval(statusTimer);
  }
}

function stopCurrent() {
  if (current) { current.cancelled = true; if (current.skipResolve) current.skipResolve(0); }
  audio.pause();
  audio.removeAttribute("src");
  current = null;
}

chrome.runtime.onMessage.addListener((msg) => {
  switch (msg.type) {
    case "offscreen-play":
      stopCurrent();
      current = { jobId: msg.jobId, serverUrl: msg.serverUrl, index: 0, cancelled: false, rate: msg.playbackRate || 1, chunks: new Map(), segments: [], total: 0, skipResolve: null };
      setVolume(msg.volume ?? 1);
      run(current);
      break;
    case "offscreen-volume":
      setVolume(msg.volume);
      break;
    case "offscreen-pause":
      audio.pause();
      report({ status: "paused" });
      break;
    case "offscreen-resume":
      if (current && audio.src) { audio.play().then(() => report({ status: "playing" })).catch(() => {}); }
      break;
    case "offscreen-stop":
      stopCurrent();
      break;
    case "offscreen-skip":
      if (current && current.skipResolve) current.skipResolve(msg.delta);
      break;
    case "offscreen-rate":
      if (current) current.rate = msg.rate;
      audio.playbackRate = msg.rate;
      break;
  }
});
