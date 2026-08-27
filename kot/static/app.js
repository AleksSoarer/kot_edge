const $ = (id) => document.getElementById(id);

function renderText(id, value) {
  $(id).textContent = value || "";
}

function renderOptional(id, value) {
  const normalized = (value || "").trim();
  renderText(id, normalized);
  $(id).classList.toggle("hidden", !normalized);
}

function setLed(prefix, value) {
  $(`${prefix}Led`).classList.toggle("on", Boolean(value));
  $(`${prefix}Text`).textContent = value ? "OK" : "OFF";
}

function renderMeter(prefix, value) {
  const available = Number.isFinite(value);
  const percent = available ? Math.max(0, Math.min(100, Math.round(value))) : null;
  const filled = percent === null ? 0 : Math.round(percent / 10);
  $(`${prefix}Bar`).textContent = "|".repeat(filled) + "_".repeat(10 - filled);
  $(`${prefix}Percent`).textContent = percent === null ? "--%" : `${String(percent).padStart(3)}%`;
}

function renderMicLevels(values) {
  const levels = Array.isArray(values) ? values : [];
  for (let channel = 0; channel < 8; channel += 1) {
    const value = Number.isFinite(levels[channel]) ? levels[channel] : 0;
    const filled = Math.round(Math.max(0, Math.min(100, value)) * 8 / 100);
    $(`micLevel${channel}`).textContent = "|".repeat(filled) + "_".repeat(8 - filled);
  }
}

function render(state) {
  const mode = state.mode || "idle";

  document.body.className = mode;
  renderText("cat", state.face);
  renderText("modeTitle", state.mode_title);
  renderText("modeIcon", state.mode_icon);
  renderText("message", state.message);
  renderOptional("heard", state.heard_text);
  renderOptional("reply", state.reply_text);
  renderMeter("cpu", state.cpu_percent);
  renderMeter("ram", state.ram_percent);
  renderMicLevels(state.mic_levels);

  setLed("mic", state.mic_online);
  setLed("core", state.core_online);
  setLed("ha", state.ha_online);

  const showTrack = Boolean(state.music_available || state.track || state.artist);
  $("trackBox").classList.toggle("hidden", !showTrack);

  const musicStatus = state.music_status || "";
  $("trackIcon").textContent = musicStatus === "Playing" ? "▶" : musicStatus === "Paused" ? "Ⅱ" : "♪";
  $("track").textContent = state.track || "YANDEX MUSIC";
  $("artist").textContent = state.artist || musicStatus.toUpperCase();
  $("volume").textContent = Number.isInteger(state.volume) ? `VOL ${state.volume}%` : "";

  if (state.updated_at) {
    const d = new Date(state.updated_at);
    $("updated").textContent = `UPDATED ${d.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })}`;
  }
}

function updateClock() {
  $("clock").textContent = new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

let ws = null;
let reconnectTimer = null;
let pingTimer = null;

function setEdgeOnline(online) {
  $("netLed").classList.toggle("on", online);
  $("netText").textContent = online ? "LOCAL" : "RECONNECT";
}

function connect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  const scheme = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${scheme}://${location.host}/ws`);

  ws.addEventListener("open", () => {
    setEdgeOnline(true);

    if (pingTimer) clearInterval(pingTimer);
    pingTimer = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) ws.send("ping");
    }, 15000);
  });

  ws.addEventListener("message", (event) => {
    try {
      render(JSON.parse(event.data));
    } catch (error) {
      console.error("Bad status payload", error);
    }
  });

  ws.addEventListener("close", () => {
    setEdgeOnline(false);
    if (pingTimer) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
    reconnectTimer = setTimeout(connect, 1500);
  });

  ws.addEventListener("error", () => ws.close());
}

async function loadInitialStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (response.ok) render(await response.json());
  } catch {
    setEdgeOnline(false);
  }
}

updateClock();
setInterval(updateClock, 1000);
loadInitialStatus();
connect();
