/* Camera detail page */
const camId = new URLSearchParams(location.search).get("id");
let cam = null;
let clips = [];
let openClip = null;
let pollTimer = null;
let generator = null;

// Snapshot browser. `shot` is null while showing the live/latest image.
const snaps = { days: [], day: null, frames: [], idx: -1 };
let shot = null;

const $ = (id) => document.getElementById(id);

const fmtRange = (c) => `${fmtTime(c.range_start)} – ${fmtTime(c.range_end)}`;

function renderClips() {
  if (!clips.length) {
    $("clips").innerHTML = `<p class="muted card">Inga klipp ännu. ${cam.enabled || cam.snapshot_count ?
      "Välj tidsspann ovan och tryck på <b>Generera</b>." : "Aktivera kameran för att börja spara bilder."}</p>`;
    return;
  }
  $("clips").innerHTML = clips.map((c) => {
    const kinds = [c.mp4 && "MP4", c.gif && "GIF"].filter(Boolean).join(" + ");
    const size = (c.mp4?.bytes || 0) + (c.gif?.bytes || 0);
    const open = openClip === c.id;
    return `<article class="card clip-item${open ? " open" : ""}" data-clip="${c.id}">
      <header class="clip-head">
        <button type="button" class="linklike clip-title" data-open="${c.id}" aria-expanded="${open}">
          ${esc(fmtRange(c))}</button>
        <span class="muted">${kinds}${c.duration_s ? ` · ${c.duration_s} s` : ""} · ${fmtBytes(size)}${c.temp_station ?
          ` · vägtemp. ${esc(c.temp_station)}` : ""} · skapad ${fmtTime(c.created_at)}</span>
        <span class="clip-actions">
          ${c.mp4 && c.gif ? `<button type="button" class="btn small ghost" data-del-clip="${c.id}" data-kind="mp4">Ta bort MP4</button>
          <button type="button" class="btn small ghost" data-del-clip="${c.id}" data-kind="gif">Ta bort GIF</button>` : ""}
          <button type="button" class="btn small danger" data-del-clip="${c.id}" data-kind="all">Ta bort klippet</button>
        </span>
      </header>
      ${open ? `<div class="clip-grid">
        ${c.mp4 ? `<div><video src="${c.mp4.url}" controls loop muted playsinline autoplay preload="metadata"></video>
          <a class="btn small ghost" href="${c.mp4.url}" download>Ladda ner MP4</a></div>` : ""}
        ${c.gif ? `<div><img src="${c.gif.url}" alt="GIF ${esc(fmtRange(c))}" loading="lazy">
          <a class="btn small ghost" href="${c.gif.url}" download>Ladda ner GIF</a></div>` : ""}
      </div>` : ""}
    </article>`;
  }).join("");
}

async function loadClips() {
  clips = await api(`api/cameras/${encodeURIComponent(camId)}/clips`);
  if (openClip === null && clips.length) openClip = clips[0].id;
  renderClips();
}

function renderJob(job) {
  const el = $("jobStatus");
  if (!job) { el.textContent = ""; return; }
  const txt = { queued: "I kö…", running: "Genererar…", done: "Klart", failed: "Misslyckades",
    skipped: "Hoppades över" }[job.status] || job.status;
  const how = { nightly: "automatisk (äldre)", batch: "län/hela Sverige" }[job.trigger] || "manuell";
  el.className = `job-status ${job.status}`;
  el.innerHTML = `<b>Senaste generering: ${txt}</b> <span class="muted">(${how},
    ${fmtTime(job.finished_at || job.started_at || job.created_at)})</span>
    ${job.message ? `<pre>${esc(job.message)}</pre>` : ""}`;
  const busy = job.status === "queued" || job.status === "running";
  clearTimeout(pollTimer);
  if (busy) pollTimer = setTimeout(() => pollJob(job.id), 3000);
}

async function pollJob(id) {
  const job = await api(`api/jobs/${id}`);
  renderJob(job);
  if (!["queued", "running"].includes(job.status)) {
    if (job.clip_id) openClip = job.clip_id;
    await Promise.all([load(), loadClips()]);
  }
}

const snapUrl = (day, hms) =>
  `api/cameras/${encodeURIComponent(camId)}/snapshots/${day}/${hms}.jpg`;
const fmtHms = (hms) => `${hms.slice(0, 2)}:${hms.slice(2, 4)}:${hms.slice(4, 6)}`;

function showLatest() {
  shot = null;
  $("latest").src = `api/cameras/${encodeURIComponent(camId)}/latest.jpg?t=${Date.now()}`;
  $("latest").alt = "Senaste bild";
  $("latestCaption").textContent = cam.last_photo_time ? `Senaste sparade bild: ${fmtTime(cam.last_photo_time)}` :
    "Senaste bild från Trafikverket";
  updateNav();
}

function showShot() {
  const hms = snaps.frames[snaps.idx];
  shot = { day: snaps.day, hms };
  $("latest").src = snapUrl(snaps.day, hms);
  $("latest").alt = `Bild ${snaps.day} ${fmtHms(hms)}`;
  $("latestCaption").textContent =
    `${snaps.day} ${fmtHms(hms)} · bild ${snaps.idx + 1} av ${snaps.frames.length} detta dygn`;
  // Preload the neighbours so stepping feels instant
  for (const i of [snaps.idx - 1, snaps.idx + 1]) {
    if (i >= 0 && i < snaps.frames.length) new Image().src = snapUrl(snaps.day, snaps.frames[i]);
  }
  updateNav();
}

const dayPos = () => snaps.days.indexOf(snaps.day);
const atNewest = () => !shot || (dayPos() === snaps.days.length - 1 && snaps.idx === snaps.frames.length - 1);
const atOldest = () => !snaps.frames.length ||
  (shot ? dayPos() <= 0 && snaps.idx <= 0 : snaps.days.length <= 1 && snaps.frames.length <= 1);

function updateNav() {
  const any = snaps.frames.length > 0;
  $("prevImg").hidden = $("nextImg").hidden = !any;
  $("prevImg").disabled = atOldest();
  $("nextImg").disabled = atNewest();
  $("toLatest").hidden = !shot;
}

async function loadDay(day) {
  const q = day ? `?day=${encodeURIComponent(day)}` : "";
  const r = await api(`api/cameras/${encodeURIComponent(camId)}/snapshots${q}`);
  Object.assign(snaps, r, { idx: -1 });
  return r.frames.length > 0;
}

let stepping = false;
async function step(dir) {
  if (stepping) return;
  stepping = true;
  try {
    if (!shot) {
      // Leaving the live view: refresh the list so the newest saved image is included
      await loadDay(null);
      if (!snaps.frames.length) return updateNav();
      snaps.idx = snaps.frames.length - 1;
      // The live view already shows the newest image, so "back" starts one step earlier
      if (dir < 0 && snaps.idx > 0) snaps.idx--;
      else if (dir < 0) return await stepDay(-1);
      return showShot();
    }
    const i = snaps.idx + dir;
    if (i >= 0 && i < snaps.frames.length) {
      snaps.idx = i;
      return showShot();
    }
    return await stepDay(dir);
  } catch (e) {
    $("latestCaption").textContent = `Kunde inte hämta bilder: ${e.message}`;
  } finally {
    stepping = false;
  }
}

async function stepDay(dir) {
  let p = dayPos() + dir;
  while (p >= 0 && p < snaps.days.length) {
    if (await loadDay(snaps.days[p])) {
      snaps.idx = dir < 0 ? snaps.frames.length - 1 : 0;
      return showShot();
    }
    p += dir;
  }
  updateNav();
}

function option(value, label, selected) {
  return `<option value="${value}"${value === selected ? " selected" : ""}>${esc(label)}</option>`;
}

function renderCamSettings() {
  // Temperature graph
  const tDef = cam.temp_overlay_default ? "på" : "av";
  $("tempMode").innerHTML = option("default", `Standard (${tDef})`, cam.temp_overlay_mode) +
    option("on", "På", cam.temp_overlay_mode) + option("off", "Av", cam.temp_overlay_mode);
  const st = cam.temp_station;
  let info;
  if (!st) {
    info = `Ingen vägväderstation inom ${cam.temp_station_max_km} km – timelapses skapas utan graf.`;
  } else {
    const km = st.distance_km != null ? `${String(st.distance_km).replace(".", ",")} km bort` : "";
    const last = st.last_value != null
      ? ` Senast ${String(st.last_value.toFixed(1)).replace(".", ",").replace("-", "−")} °C (${fmtTime(st.last_sample)}).`
      : " Ingen temperatur hämtad ännu.";
    info = `Vägväderstation ${esc(st.name)}, ${km}.${last}`;
  }
  $("stationInfo").innerHTML = (cam.temp_overlay ? "Visas som standard när du genererar. " :
    "Avstängd för den här kameran. ") + info;

  // Image resolution
  const fs = cam.fullsize;
  const hdSize = fs.width ? `${fs.width}×${fs.height}` : "max";
  const rDef = cam.resolution_default === "high" ? "högupplöst" : "lågupplöst";
  $("resMode").innerHTML = option("default", `Standard (${rDef})`, cam.resolution_mode) +
    option("high", `Högupplöst (${hdSize})`, cam.resolution_mode) +
    option("low", "Lågupplöst (385×217)", cam.resolution_mode);
  let r;
  if (!fs.available) {
    r = "Kameran har ingen högupplöst bild – lågupplösta bilder sparas.";
  } else if (fs.width) {
    r = `Kameran har högupplösta bilder: ${fs.width}×${fs.height}.`;
  } else {
    r = "Kameran anger att den har högupplösta bilder (upplösningen är inte kontrollerad än).";
  }
  r += cam.enabled
    ? (cam.saves_fullsize ? " Nya bilder sparas högupplösta (ca 5 gånger mer lagring)." : " Nya bilder sparas lågupplösta.")
    : " Gäller när kameran är aktiv.";
  $("resInfo").textContent = r;
}

async function saveCamSetting(body) {
  try {
    await api(`api/cameras/${encodeURIComponent(camId)}`, { method: "PATCH", body });
    await load();
    loadStatus();
  } catch (e) { alert(e.message); renderCamSettings(); }
}

function render() {
  document.title = `${cam.name} – TRV Webbkameror`;
  $("camName").textContent = cam.name;
  $("camMeta").textContent = [cam.county && `${cam.county} län`, cam.type, cam.direction && `Riktning ${cam.direction}°`,
    `${cam.snapshot_count} sparade bilder`, `${cam.archive_days} dagar i årsarkivet`].filter(Boolean).join(" · ");
  $("enabled").checked = cam.enabled;
  $("enabledLabel").textContent = cam.enabled ? "Aktiv – sparar bilder" : "Inaktiv";
  renderCamSettings();
  if (!shot) showLatest();
  renderJob(cam.last_job);
}

async function load() {
  cam = await api(`api/cameras/${encodeURIComponent(camId)}`);
  render();
}

$("prevImg").addEventListener("click", () => step(-1));
$("nextImg").addEventListener("click", () => step(1));
$("toLatest").addEventListener("click", showLatest);
document.addEventListener("keydown", (ev) => {
  if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
  if (ev.target.closest("input, textarea, select, video")) return;
  if (ev.key === "ArrowLeft" && !$("prevImg").hidden && !$("prevImg").disabled) { ev.preventDefault(); step(-1); }
  if (ev.key === "ArrowRight" && !$("nextImg").hidden && !$("nextImg").disabled) { ev.preventDefault(); step(1); }
});

$("clips").addEventListener("click", async (ev) => {
  const open = ev.target.closest("[data-open]");
  if (open) {
    const id = Number(open.dataset.open);
    openClip = openClip === id ? -1 : id;
    return renderClips();
  }
  const del = ev.target.closest("[data-del-clip]");
  if (del) {
    const c = clips.find((x) => x.id === Number(del.dataset.delClip));
    const kind = del.dataset.kind;
    const what = { mp4: "MP4:an i klippet", gif: "GIF:en i klippet", all: "klippet" }[kind];
    if (!confirm(`Ta bort ${what} ${fmtRange(c)}?\n\nKlippet kan genereras igen när som helst.`)) return;
    try {
      await api(`api/cameras/${encodeURIComponent(camId)}/clips/${c.id}?kind=${kind}`, { method: "DELETE" });
      if (kind === "all" && openClip === c.id) openClip = -1;
      await Promise.all([load(), loadClips()]);
      loadStatus();
    } catch (e) { alert(`Kunde inte ta bort: ${e.message}`); }
  }
});

$("enabled").addEventListener("change", async (ev) => {
  try {
    await api(`api/cameras/${encodeURIComponent(camId)}`, { method: "PATCH", body: { enabled: ev.target.checked } });
    await load();
    loadStatus();
  } catch (e) { alert(e.message); ev.target.checked = !ev.target.checked; }
});

$("tempMode").addEventListener("change", (ev) => saveCamSetting({ temp_overlay_mode: ev.target.value }));
$("resMode").addEventListener("change", (ev) => saveCamSetting({ resolution_mode: ev.target.value }));

(async () => {
  if (!camId) { $("camName").textContent = "Ingen kamera vald"; return; }
  try { await load(); } catch (e) { $("camName").textContent = e.message; return; }
  generator = await createGenerator($("generator"), {
    availabilityUrl: () => `api/cameras/${encodeURIComponent(camId)}/availability`,
    temperatureDefault: () => (cam.temp_overlay ? "på" : "av"),
    submit: async (body) => {
      const job = await api(`api/cameras/${encodeURIComponent(camId)}/clips`, { method: "POST", body });
      renderJob(job);
      return "Genereringen är startad. Klippet visas i biblioteket när det är klart.";
    },
  });
  await loadClips();
  try { await loadDay(null); } catch (_) { /* browsing stays hidden */ }
  updateNav();
  if (cam.lat != null) {
    const m = L.map("miniMap", { zoomControl: false, attributionControl: false }).setView([cam.lat, cam.lon], 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(m);
    L.circleMarker([cam.lat, cam.lon], { radius: 8, color: "#0b6b2f", fillColor: "#2ea043", fillOpacity: 1 }).addTo(m);
  }
  loadStatus();
})();
