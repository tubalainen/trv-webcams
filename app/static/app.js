/* Overview page: map + list */
const state = { cameras: [], markers: new Map(), counties: {} };

const map = L.map("map", { preferCanvas: true }).setView([62.5, 16.5], 5);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · Bilder: Trafikverket',
}).addTo(map);
const cluster = L.markerClusterGroup({ chunkedLoading: true, showCoverageOnHover: false, maxClusterRadius: 45 });
map.addLayer(cluster);

const camUrl = (id) => `camera.html?id=${encodeURIComponent(id)}`;

// Colours per camera kind (from Trafikverket's Type). Active cameras are larger and solid.
const KINDS = {
  flow: { label: "Trafikflöde", plural: "trafikflödeskameror", fill: "#1f6feb", edge: "#0a3069" },
  road: { label: "Väglag", plural: "väglagskameror", fill: "#e8710a", edge: "#7a3a00" },
  "": { label: "Övrig", plural: "övriga kameror", fill: "#8c959f", edge: "#424a53" },
};
const kindOf = (c) => KINDS[c.kind] || KINDS[""];

function markerStyle(c) {
  const k = kindOf(c);
  return c.enabled
    ? { radius: 8, color: k.edge, weight: 2.5, fillColor: k.fill, fillOpacity: 0.95 }
    : { radius: 5, color: k.fill, weight: 1, fillColor: k.fill, fillOpacity: 0.35 };
}

function kindTag(c) {
  return `<span class="kind-tag kind-${c.kind || "other"}" title="${esc(c.type || "Okänd typ")}">${kindOf(c).label}</span>`;
}

function popupHtml(c) {
  return `<div class="popup">
    <strong>${esc(c.name)}</strong><br>
    <span class="muted">${kindTag(c)} ${esc(c.county)}${c.enabled ? " · Aktiv" : ""}${c.hd ? ` · <span class="badge" title="Har högupplösta bilder">HD ${esc(c.hd)}</span>` : ""}</span>
    <img src="api/cameras/${encodeURIComponent(c.id)}/latest.jpg" alt="" loading="lazy">
    <div class="popup-actions">
      <a class="btn small" href="${camUrl(c.id)}">Öppna</a>
      <button class="btn small ${c.enabled ? "ghost" : ""}" data-toggle="${esc(c.id)}">
        ${c.enabled ? "Avaktivera" : "Aktivera"}</button>
    </div></div>`;
}

function filtered() {
  const q = document.getElementById("search").value.trim().toLowerCase();
  const county = document.getElementById("county").value;
  const kind = document.getElementById("kind").value;
  const only = document.getElementById("onlyEnabled").checked;
  return state.cameras.filter((c) =>
    (!q || c.name.toLowerCase().includes(q)) &&
    (!county || String(c.county_no) === county) &&
    (!kind || c.kind === kind) &&
    (!only || c.enabled));
}

function render() {
  const cams = filtered();
  const list = document.getElementById("list");
  const frag = document.createDocumentFragment();
  for (const c of cams.slice(0, 500)) {
    const li = document.createElement("li");
    li.className = c.enabled ? "on" : "";
    li.innerHTML = `
      <label class="switch" title="Aktiv – spara bilder och skapa timelapses">
        <input type="checkbox" data-toggle="${esc(c.id)}" ${c.enabled ? "checked" : ""}>
        <span class="slider"></span>
      </label>
      <a href="${camUrl(c.id)}" class="cam-link" data-focus="${esc(c.id)}">
        <span class="name">${esc(c.name)}</span>
        <span class="muted">${kindTag(c)} ${esc(c.county)}${c.hd ? ` <span class="badge${c.saves_hd ? " on" : ""}" title="${c.saves_hd ? "Sparar högupplösta bilder" : "Har högupplösta bilder"}">HD ${esc(c.hd)}</span>` : ""}</span>
      </a>`;
    frag.appendChild(li);
  }
  list.replaceChildren(frag);
  const enabled = state.cameras.filter((c) => c.enabled).length;
  document.getElementById("count").textContent =
    `${cams.length} kameror visas${cams.length > 500 ? " (första 500 i listan)" : ""} · ${enabled} aktiva`;

  cluster.clearLayers();
  const layers = [];
  for (const c of cams) {
    if (c.lat == null || c.lon == null) continue;
    let m = state.markers.get(c.id);
    if (!m) {
      m = L.circleMarker([c.lat, c.lon], markerStyle(c));
      m.bindPopup(() => popupHtml(m.cam), { minWidth: 260 });
      m.bindTooltip(c.name);
      state.markers.set(c.id, m);
    }
    m.cam = c;
    m.setStyle(markerStyle(c));
    layers.push(m);
  }
  cluster.addLayers(layers);

  const bulk = document.getElementById("bulk");
  const scope = bulkScope();
  bulk.hidden = !scope;
  if (scope) {
    document.getElementById("bulkLabel").textContent = `${scope.text[0].toUpperCase()}${scope.text.slice(1)}:`;
  }
}

// What "Aktivera/Avaktivera alla" applies to: the chosen county, camera kind or both.
function bulkScope() {
  const county = document.getElementById("county").value;
  const kind = document.getElementById("kind").value;
  if (!county && !kind) return null;
  const what = kind ? KINDS[kind].plural : "kameror";
  return {
    body: { ...(county && { county: Number(county) }), ...(kind && { kind }) },
    text: county ? `${what} i ${state.counties[county]} län` : `alla ${what}`,
  };
}

async function toggle(id, enabled) {
  const c = state.cameras.find((x) => x.id === id);
  if (!c) return;
  const prev = c.enabled;
  c.enabled = enabled ?? !c.enabled;
  try {
    await api(`api/cameras/${encodeURIComponent(id)}`, { method: "PATCH", body: { enabled: c.enabled } });
  } catch (e) {
    c.enabled = prev;
    alert(`Kunde inte uppdatera: ${e.message}`);
  }
  render();
  map.closePopup();
  loadStatus();
}

async function bulk(enabled) {
  const scope = bulkScope();
  if (!scope) return;
  const text = scope.text.startsWith("alla ") ? scope.text : `alla ${scope.text}`;
  if (!confirm(`${enabled ? "Aktivera" : "Avaktivera"} ${text}?`)) return;
  await api("api/cameras/bulk", { method: "POST", body: { ...scope.body, enabled } });
  await loadCameras();
  loadStatus();
}

async function loadCameras() {
  state.cameras = await api("api/cameras");
  render();
}

document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-toggle]");
  if (t && t.tagName === "BUTTON") { ev.preventDefault(); toggle(t.dataset.toggle); }
});
document.getElementById("list").addEventListener("change", (ev) => {
  const t = ev.target.closest("input[data-toggle]");
  if (t) toggle(t.dataset.toggle, t.checked);
});
document.getElementById("list").addEventListener("mouseover", (ev) => {
  const a = ev.target.closest("[data-focus]");
  if (!a) return;
  const m = state.markers.get(a.dataset.focus);
  if (m && cluster.hasLayer(m)) m.openTooltip();
});
let searchTimer;
document.getElementById("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(render, 150);
});
document.getElementById("county").addEventListener("change", () => {
  render();
  const cams = filtered().filter((c) => c.lat != null);
  if (cams.length) map.fitBounds(L.latLngBounds(cams.map((c) => [c.lat, c.lon])).pad(0.1));
});
document.getElementById("kind").addEventListener("change", render);
document.getElementById("onlyEnabled").addEventListener("change", render);
document.getElementById("bulkOn").addEventListener("click", () => bulk(true));
document.getElementById("bulkOff").addEventListener("click", () => bulk(false));

(async () => {
  state.counties = await api("api/counties");
  const sel = document.getElementById("county");
  for (const [no, name] of Object.entries(state.counties).sort((a, b) => a[1].localeCompare(b[1], "sv"))) {
    const o = document.createElement("option");
    o.value = no;
    o.textContent = name;
    sel.appendChild(o);
  }
  state.cameras = await api("api/cameras");
  // Show the user's own selection first: "Endast aktiva" is on when any camera is active.
  // Unticking it shows all cameras again.
  if (state.cameras.some((c) => c.enabled)) document.getElementById("onlyEnabled").checked = true;
  render();
  if (!state.cameras.length) {
    document.getElementById("count").textContent =
      "Inga kameror ännu – kameralistan hämtas från Trafikverket vid start (kontrollera TRV_API_KEY).";
    setTimeout(loadCameras, 10000);
  }
  loadStatus();
  setInterval(loadStatus, 60000);
})();
