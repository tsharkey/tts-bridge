// Browse Force Org models: filter by army, search by name, static only; the
// suggested models first when choosing for a list's model; a 3D view beside
// the list. Used by the Models page and Scribe's "Browse all models…".
//   import {modelBrowser} from "/model-browser.js";
//   const b = modelBrowser(el, {faction, sub, suggested: [entryInfo], onPick: info => ..., pickLabel: "Use this"});
//   b.open({...same options}) to show another model's choices in the same browser.
import {viewer} from "/viewer3d.js";

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

export function modelBrowser(root, options = {}) {
  root.innerHTML = `
    <div class="mb">
      <div class="mb-side">
        <div class="mb-filters">
          <input class="mb-q" type="search" placeholder="Search models by name">
          <div class="row">
            <select class="mb-tiles"></select>
            <label class="mb-check"><input type="checkbox" class="mb-static"> Static only</label>
          </div>
        </div>
        <div class="mb-count summary"></div>
        <div class="mb-list"></div>
      </div>
      <div class="mb-view">
        <canvas class="mb-canvas"></canvas>
        <div class="mb-name"></div>
        <div class="summary mb-info">Pick a model to see it. Drag to turn it, scroll to zoom.</div>
        <button class="primary mb-use" hidden></button>
        <div class="msg mb-msg"></div>
      </div>
    </div>`;
  const $ = s => root.querySelector(s);
  const view = viewer($(".mb-canvas"));
  let opts = {}, tiles = [], selected = null, timer = null, found = [];

  const row = (m, i, section) => `<button class="mb-row ${selected && selected.pick === m.pick ? "on" : ""}"
      data-section="${section}" data-i="${i}"><span>${esc(m.name)}</span>
      <small>${esc(m.tile)}${m.credit ? " · " + esc(m.credit) : ""}</small>
      <span class="badge ${m.static ? "static" : "animated"}">${m.static ? "Static" : "Animated"}</span></button>`;

  function render() {
    const suggested = (opts.suggested || []).filter(m => !$(".mb-static").checked || m.static);
    const picks = new Set(suggested.map(m => m.pick));
    const rest = found.filter(m => !picks.has(m.pick));
    const tileText = $(".mb-tiles").selectedOptions[0]?.textContent || "";
    $(".mb-list").innerHTML =
      (suggested.length ? `<h4>Suggested</h4>${suggested.map((m, i) => row(m, i, "s")).join("")}` : "")
      + `<h4>${esc(tileText)}</h4>`
      + (rest.length ? rest.map(m => row(m, found.indexOf(m), "f")).join("") : `<p class="summary">No models match.</p>`);
  }

  async function search() {
    const tileValue = $(".mb-tiles").value;
    const params = new URLSearchParams({q: $(".mb-q").value, static: $(".mb-static").checked,
      tiles: tileValue === "@army" ? tiles.filter(t => t.army).map(t => t.tile).join(",") : tileValue === "@all" ? "" : tileValue});
    try {
      const r = await api(`/api/models?${params}`);
      found = r.models;
      $(".mb-count").textContent = r.total > r.models.length ? `Showing ${r.models.length} of ${r.total}; search to narrow it.`
        : `${r.total} model${r.total === 1 ? "" : "s"}`;
      render();
    } catch (e) { $(".mb-list").innerHTML = `<p class="msg error">${esc(e.message)}</p>`; }
  }

  async function select(m) {
    selected = m;
    root.querySelectorAll(".mb-row.on").forEach(r => r.classList.remove("on"));
    $(".mb-name").textContent = m.name;
    $(".mb-msg").textContent = "";
    const info = m.preview !== undefined ? m : await api(`/api/models/entry?pick=${encodeURIComponent(m.pick)}`);
    if (selected !== m) return;
    const status = t => $(".mb-info").textContent = t ?? `${info.tile}${info.credit ? " · " + info.credit : ""} · ${info.static ? "static" : "animated"}`;
    view.show(info, status);
    const use = $(".mb-use");
    use.hidden = !opts.onPick;
    use.textContent = opts.pickLabel || "Use this";
    use.disabled = false;
  }

  root.addEventListener("click", e => {
    const r = e.target.closest(".mb-row");
    if (r) {
      r.classList.add("on");
      select(r.dataset.section === "s" ? opts.suggested[r.dataset.i] : found[r.dataset.i]);
    }
  });
  $(".mb-use").addEventListener("click", async () => {
    if (!selected || !opts.onPick) return;
    $(".mb-use").disabled = true;
    try { await opts.onPick(selected); }
    catch (e) { $(".mb-msg").textContent = e.message; $(".mb-msg").className = "msg error mb-msg"; $(".mb-use").disabled = false; }
  });
  $(".mb-q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(search, 200); });
  $(".mb-tiles").addEventListener("change", search);
  $(".mb-static").addEventListener("change", search);

  async function open(o = {}) {
    opts = o;
    selected = null;
    $(".mb-name").textContent = "";
    $(".mb-info").textContent = "Pick a model to see it. Drag to turn it, scroll to zoom.";
    $(".mb-use").hidden = true;
    view.show(null);
    $(".mb-q").value = o.query || "";
    try {
      tiles = await api(`/api/models/tiles?${new URLSearchParams({faction: o.faction || "", sub: o.sub || ""})}`);
    } catch (e) { $(".mb-list").innerHTML = `<p class="msg error">${esc(e.message)}</p>`; return; }
    const mine = tiles.filter(t => t.army);
    $(".mb-tiles").innerHTML =
      (mine.length ? `<option value="@army">${esc(o.sub || o.faction)} models</option>` : "")
      + `<option value="@all">All armies</option>`
      + tiles.map(t => `<option value="${esc(t.tile)}">${esc(t.label)} (${t.models})</option>`).join("");
    await search();
  }
  open(options);
  return {open};
}
