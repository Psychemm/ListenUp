const DEFAULTS = { serverUrl: "http://127.0.0.1:8765", useLlm: true, llmModel: "", voice: "default", temperature: 0.8, exaggeration: 0.5, cfgWeight: 0.5, playbackRate: 1.0, volume: 1.0 };
const $ = (id) => document.getElementById(id);
const send = (msg) => chrome.runtime.sendMessage(msg).catch(() => {});
const pct = (v) => Math.round(v * 100) + "%";
// "cori_samuel_f.wav" -> "Cori Samuel (f)"
const prettyVoice = (n) => n === "default" ? "Default (built-in)" : n.replace(/\.(wav|mp3|flac)$/i, "").replace(/_(m|f)$/i, " ($1)").replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
function showVals() { $("rateVal").textContent = $("rate").value + "x"; $("volVal").textContent = pct(+$("volume").value); }

function render(s) {
  const busy = s.status === "playing" || s.status === "paused" || s.status === "loading";
  $("title").textContent = s.status === "idle" ? "Idle" : (s.status === "loading" ? "Preparing…" : s.title || "");
  $("seg").textContent = s.segment || "";
  $("err").textContent = s.status === "error" ? s.error : "";
  $("prog").hidden = !busy || !s.total;
  if (s.total) { $("prog").max = s.total; $("prog").value = s.index + 1; }
  $("counter").textContent = busy && s.total ? `${s.index + 1} / ${s.total}` + (s.llmUsed === false && s.llmNote ? `  ·  regex cleanup (${s.llmNote})` : "") : "";
  $("toggle").textContent = s.status === "playing" ? "⏸" : "▶";
  for (const id of ["prev", "toggle", "next", "stop"]) $(id).disabled = !busy;
}

async function loadSettings() {
  const s = { ...DEFAULTS, ...(await chrome.storage.local.get(DEFAULTS)) };
  $("serverUrl").value = s.serverUrl; $("useLlm").checked = s.useLlm; $("llmModel").value = s.llmModel;
  $("temperature").value = s.temperature; $("exaggeration").value = s.exaggeration; $("cfgWeight").value = s.cfgWeight; $("rate").value = s.playbackRate; $("volume").value = s.volume;
  showVals();
  await refreshHealth(s);
  $("voice").value = s.voice;
}

async function saveSettings() {
  const s = {
    serverUrl: $("serverUrl").value.trim().replace(/\/$/, "") || DEFAULTS.serverUrl,
    useLlm: $("useLlm").checked, llmModel: $("llmModel").value.trim(), voice: $("voice").value,
    temperature: +$("temperature").value, exaggeration: +$("exaggeration").value, cfgWeight: +$("cfgWeight").value, playbackRate: +$("rate").value, volume: +$("volume").value,
  };
  await chrome.storage.local.set(s);
  return s;
}

async function refreshHealth(s) {
  try {
    const h = await (await fetch(`${s.serverUrl}/health`)).json();
    $("dot").className = "dot " + (h.engine_loaded ? "ok" : "");
    $("health").textContent = `${h.engine}${h.engine_loaded ? "" : " (loading)"} · LLM ${h.llm_ok ? h.llm_model : "off: " + h.llm_note}`;
    const v = await (await fetch(`${s.serverUrl}/voices`)).json();
    $("voice").innerHTML = v.voices.map(n => `<option value="${n}">${prettyVoice(n)}</option>`).join("");
  } catch (e) {
    $("dot").className = "dot bad";
    $("health").textContent = "server not running (start-server.ps1)";
  }
}

$("readPage").onclick = async () => { await saveSettings(); send({ type: "read", mode: "page" }); };
$("readSel").onclick = async () => { await saveSettings(); send({ type: "read", mode: "selection" }); };
$("readFrom").onclick = async () => { await saveSettings(); send({ type: "read", mode: "from-selection" }); };
$("toggle").onclick = () => send({ type: "toggle" });
$("stop").onclick = () => send({ type: "stop" });
$("prev").onclick = () => send({ type: "skip", delta: -1 });
$("next").onclick = () => send({ type: "skip", delta: 1 });
$("rate").oninput = async () => { showVals(); await saveSettings(); send({ type: "set-rate", rate: +$("rate").value }); };
$("volume").oninput = async () => { showVals(); await saveSettings(); send({ type: "set-volume", volume: +$("volume").value }); };
for (const id of ["serverUrl", "useLlm", "llmModel", "voice", "temperature", "exaggeration", "cfgWeight"]) $(id).onchange = saveSettings;
$("serverUrl").onchange = async () => refreshHealth(await saveSettings());

chrome.runtime.onMessage.addListener((msg) => { if (msg.type === "state") render(msg.state); });
chrome.runtime.sendMessage({ type: "get-state" }).then(render).catch(() => render({ status: "idle" }));
loadSettings();
