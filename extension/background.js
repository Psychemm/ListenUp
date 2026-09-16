// Service worker: context menus, keyboard commands, talks to the local server,
// and forwards playback control to the offscreen audio document.

const DEFAULTS = {
  serverUrl: "http://127.0.0.1:8765",
  useLlm: true,
  llmModel: "",
  voice: "default",
  temperature: 0.8,
  exaggeration: 0.5,
  cfgWeight: 0.5,
  playbackRate: 1.0,
  volume: 1.0,
};

let state = { status: "idle", jobId: null, title: "", index: 0, total: 0, segment: "", error: "", ready: 0 };
// The service worker can be suspended mid-playback; restore the last known state on wake.
chrome.storage.session.get("state").then((r) => { if (r.state) state = { ...state, ...r.state }; }).catch(() => {});

async function getSettings() {
  const s = await chrome.storage.local.get(DEFAULTS);
  return { ...DEFAULTS, ...s };
}

function setState(patch) {
  state = { ...state, ...patch };
  chrome.storage.session.set({ state }).catch(() => {});
  chrome.runtime.sendMessage({ type: "state", state }).catch(() => {});
  const badge = state.status === "playing" ? "▶" : state.status === "paused" ? "⏸" : state.status === "loading" ? "…" : "";
  chrome.action.setBadgeText({ text: badge });
  chrome.action.setBadgeBackgroundColor({ color: "#3b82f6" });
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "read-selection", title: "ListenUp: read selection", contexts: ["selection"] });
  chrome.contextMenus.create({ id: "read-from-selection", title: "ListenUp: read page from here", contexts: ["selection"] });
  chrome.contextMenus.create({ id: "read-page", title: "ListenUp: read this page", contexts: ["page", "frame", "link", "image"] });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "read-selection") startReading(tab, "selection");
  if (info.menuItemId === "read-from-selection") startReading(tab, "from-selection");
  if (info.menuItemId === "read-page") startReading(tab, "page");
});

chrome.commands.onCommand.addListener(async (cmd) => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (cmd === "read-selection" && tab) startReading(tab, "auto");
  if (cmd === "toggle-pause") togglePause();
});

async function extract(tab, mode) {
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: (m) => { window.__listenupMode = m; }, args: [mode] });
  const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  return res?.result;
}

async function startReading(tab, mode) {
  try {
    setState({ status: "loading", error: "", index: 0, total: 0, segment: "", ready: 0 });
    const page = await extract(tab, mode);
    if (page?.error) throw new Error(page.error);
    if (!page || !page.text || page.text.trim().length < 2) throw new Error("No readable text found on this page.");
    if (mode === "selection" && page.source !== "selection") throw new Error("Nothing is selected.");
    const settings = await getSettings();
    const body = {
      text: page.text,
      title: page.source === "page" ? page.title : null,
      options: {
        use_llm: settings.useLlm,
        llm_model: settings.llmModel || null,
        voice: settings.voice,
        temperature: Number(settings.temperature),
        exaggeration: Number(settings.exaggeration),
        cfg_weight: Number(settings.cfgWeight),
      },
    };
    const r = await fetch(`${settings.serverUrl}/jobs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error(`Server error ${r.status}: ${(await r.text()).slice(0, 200)}`);
    const { id } = await r.json();
    setState({ jobId: id, title: page.title || page.text.slice(0, 60) });
    await ensureOffscreen();
    chrome.runtime.sendMessage({ type: "offscreen-play", jobId: id, serverUrl: settings.serverUrl, playbackRate: Number(settings.playbackRate), volume: Number(settings.volume) });
  } catch (e) {
    console.error(e);
    setState({ status: "error", error: String(e.message || e) });
  }
}

async function ensureOffscreen() {
  const has = await chrome.offscreen.hasDocument?.();
  if (has) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["AUDIO_PLAYBACK"],
    justification: "Play TTS audio streamed from the local ListenUp server.",
  });
}

async function togglePause() {
  if (state.status === "playing") chrome.runtime.sendMessage({ type: "offscreen-pause" });
  else if (state.status === "paused") chrome.runtime.sendMessage({ type: "offscreen-resume" });
}

async function stop() {
  chrome.runtime.sendMessage({ type: "offscreen-stop" }).catch(() => {});
  if (state.jobId) {
    const s = await getSettings();
    fetch(`${s.serverUrl}/jobs/${state.jobId}`, { method: "DELETE" }).catch(() => {});
  }
  setState({ status: "idle", jobId: null, index: 0, total: 0, segment: "" });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.type) {
    case "read": {
      chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => tab && startReading(tab, msg.mode));
      sendResponse({ ok: true });
      break;
    }
    case "pause": chrome.runtime.sendMessage({ type: "offscreen-pause" }); sendResponse({ ok: true }); break;
    case "resume": chrome.runtime.sendMessage({ type: "offscreen-resume" }); sendResponse({ ok: true }); break;
    case "toggle": togglePause(); sendResponse({ ok: true }); break;
    case "stop": stop(); sendResponse({ ok: true }); break;
    case "skip": chrome.runtime.sendMessage({ type: "offscreen-skip", delta: msg.delta || 1 }); sendResponse({ ok: true }); break;
    case "set-rate": chrome.runtime.sendMessage({ type: "offscreen-rate", rate: msg.rate }); sendResponse({ ok: true }); break;
    case "set-volume": chrome.runtime.sendMessage({ type: "offscreen-volume", volume: msg.volume }); sendResponse({ ok: true }); break;
    case "get-state": sendResponse(state); break;
    case "player-state": setState(msg.patch); sendResponse({ ok: true }); break;
    default: return false;
  }
  return true;
});
