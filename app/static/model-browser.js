// Browse Force Org models: filter by army, search by name, static only; a
// unit's favourites and the suggested models first; a 3D view beside the list.
// Used by the Models page and Scribe's "Browse all models…".
//   import {modelBrowser} from "/model-browser.js";
//   const b = modelBrowser(el, {faction, sub, suggested: [entryInfo], onPick: info => ..., pickLabel: "Use this",
//                               favoriteKey: "<chapter or faction>|<unit>", onFavorite: () => ...});
//   b.open({...same options}) to show another model's choices in the same browser.
// With favoriteKey, each model gets a star that adds it to (or removes it from) that unit's favourites.
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
        <div class="row mb-actions"><button class="primary mb-use" hidden></button>
          <button class="fit mb-fav" hidden></button></div>
        <div class="msg mb-msg"></div>
      </div>
    </div>`;
  const $ = s => root.querySelector(s);
  const view = viewer($(".mb-canvas"));
  let opts = {}, tiles = [], selected = null, timer = null, found = [], favourites = [];
  const liked = m => favourites.some(f => f.pick === m.pick);

  const row = (m, i, section) => `<div class="mb-row ${selected && selected.pick === m.pick ? "on" : ""}"
      data-section="${section}" data-i="${i}" role="button" tabindex="0"><span>${esc(m.name)}</span>
      <small>${esc(m.tile)}${m.credit ? " · " + esc(m.credit) : ""}</small>
      <span class="mb-tags">${opts.favoriteKey ? `<button class="mb-star ${liked(m) ? "on" : ""}"
        title="${liked(m) ? "Remove from" : "Add to"} this unit's favourites">${liked(m) ? "★" : "☆"}</button>` : ""}
      <span class="badge ${m.static ? "static" : "animated"}">${m.static ? "Static" : "Animated"}</span></span></div>`;

  // each section's models, so a row's data-section/data-i finds its model
  let sections = {};
  function render() {
    const keep = m => !$(".mb-static").checked || m.static;
    const favs = favourites.filter(keep);
    const shown = new Set(favs.map(m => m.pick));
    const suggested = (opts.suggested || []).filter(m => keep(m) && !shown.has(m.pick));
    suggested.forEach(m => shown.add(m.pick));
    const rest = found.filter(m => !shown.has(m.pick));
    sections = {v: favs, s: suggested, f: rest};
    const tileText = $(".mb-tiles").selectedOptions[0]?.textContent || "";
    const block = (title, key, empty) => sections[key].length
      ? `<h4>${esc(title)}</h4>${sections[key].map((m, i) => row(m, i, key)).join("")}`
      : empty === undefined ? "" : `<h4>${esc(title)}</h4><p class="summary">${empty}</p>`;
    $(".mb-list").innerHTML =
      (opts.favoriteKey ? block("Favorites", "v", "None yet. Star a model to add it.") : "")
      + block("Suggested", "s")
      + block(tileText, "f", "No models match.");
    showStar();
  }

  function showStar() {
    const b = $(".mb-fav");
    b.hidden = !(opts.favoriteKey && selected);
    if (!b.hidden) b.textContent = liked(selected) ? "★ Favourite" : "☆ Add to favourites";
  }

  async function loadFavourites() {
    favourites = opts.favoriteKey ? (await api(`/api/favorites?key=${encodeURIComponent(opts.favoriteKey)}`)).models : [];
  }

  async function toggleFavourite(m) {
    const on = !liked(m);
    await api("/api/favorites", {key: opts.favoriteKey, pick: m.pick, on});
    await loadFavourites();
    render();
    opts.onFavorite?.(m, on);
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
    showStar();
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
    if (!r) return;
    const m = sections[r.dataset.section][r.dataset.i];
    if (e.target.closest(".mb-star")) return toggleFavourite(m).catch(err => alert(err.message));
    r.classList.add("on");
    select(m);
  });
  $(".mb-fav").addEventListener("click", () => selected && toggleFavourite(selected).catch(err => alert(err.message)));
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
      await loadFavourites();
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
