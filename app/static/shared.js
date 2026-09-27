// Shared by every hub page. A plain script (not a module), so pages can use these as globals.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const store = {
  get(k, d = "") { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const data = await r.json().catch(() => ({error: `HTTP ${r.status}`}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}
function setMsg(el, text, kind = "") { el.textContent = text; el.className = "msg " + kind; }

// TTS status in the header (#ttsDot, #ttsText), on every page that has them.
async function pollStatus() {
  try {
    const s = await api("/api/status");
    $("#ttsDot").classList.toggle("on", s.connected);
    $("#ttsText").textContent = s.connected ? (s.lct ? "TTS connected · LCT" : "TTS connected") : "TTS not responding";
  } catch { $("#ttsDot").classList.remove("on"); $("#ttsText").textContent = "App server not responding"; }
}
if ($("#ttsDot")) { pollStatus(); setInterval(pollStatus, 8000); }
