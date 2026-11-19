/* Camera detail page */
const camId = new URLSearchParams(location.search).get("id");
let periods = [];
let cam = null;
let activePeriod = "1d";
let pollTimer = null;

// Snapshot browser. `shot` is null while showing the live/latest image.
const snaps = { days: [], day: null, frames: [], idx: -1 };
let shot = null;

const $ = (id) => document.getElementById(id);

function renderClip() {
  const tl = cam.timelapses[activePeriod];
  const label = periods.find((p) => p.key === activePeriod)?.label ?? activePeriod;
  if (!tl) {
    $("clip").innerHTML = `<p class="muted">Ingen timelapse för ${esc(label)} ännu.
      ${cam.enabled ? "Den skapas i nattkörningen, eller tryck på <b>Generera timelapse</b>." :
        "Aktivera kameran för att börja spara bilder."}</p>`;
    return;
  }
  const mp4 = tl.mp4, gif = tl.gif;
  const meta = (t) => t ? `${t.frames} bilder · ${fmtBytes(t.size_bytes)} · ${esc(t.encoder)}` +
    `${t.temp_station ? ` · vägtemp. ${esc(t.temp_station)}` : ""} · skapad ${fmtTime(t.created_at)}` : "";
  $("clip").innerHTML = `
    <div class="clip-grid">
      ${mp4 ? `<div><h3>Video (MP4)</h3>
        <video src="${mp4.url}" controls loop muted playsinline preload="metadata"></video>
        <p class="muted">${fmtTime(mp4.range_start)} – ${fmtTime(mp4.range_end)}<br>${meta(mp4)}</p>
        <a class="btn small ghost" href="${mp4.url}" download>Ladda ner MP4</a></div>` : ""}
      ${gif ? `<div><h3>GIF-animation</h3>
        <img src="${gif.url}" alt="GIF-animation ${esc(label)}" loading="lazy">
        <p class="muted">${meta(gif)}</p>
        <a class="btn small ghost" href="${gif.url}" download>Ladda ner GIF</a></div>` : ""}
    </div>`;
}

function renderTabs() {
  $("tabs").innerHTML = periods.map((p) => {
    const has = cam.timelapses[p.key] ? "has" : "";
    return `<button role="tab" class="tab ${has} ${p.key === activePeriod ? "active" : ""}"
      data-period="${p.key}" aria-selected="${p.key === activePeriod}">${esc(p.label)}</button>`;
  }).join("");
}

function renderJob(job) {
  const el = $("jobStatus");
  if (!job) { el.textContent = ""; return; }
  const txt = { queued: "I kö…", running: "Genererar…", done: "Klart", failed: "Misslyckades" }[job.status];
  el.className = `job-status ${job.status}`;
  el.innerHTML = `<b>${txt}</b> <span class="muted">(${job.trigger === "nightly" ? "nattkörning" : "manuell"},
    ${fmtTime(job.finished_at || job.started_at || job.created_at)})</span>
    ${job.message ? `<pre>${esc(job.message)}</pre>` : ""}`;
  const busy = job.status === "queued" || job.status === "running";
  $("generate").disabled = busy;
  clearTimeout(pollTimer);
  if (busy) pollTimer = setTimeout(() => pollJob(job.id), 3000);
}

async function pollJob(id) {
  const job = await api(`api/jobs/${id}`);
  renderJob(job);
  if (job.status === "done" || job.status === "failed") await load();
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

function renderTempSetting() {
  $("tempOverlay").checked = cam.temp_overlay;
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
  $("stationInfo").innerHTML = (cam.temp_overlay ? "Visas i nattkörningen och när du genererar. " :
    "Avstängd för den här kameran. ") + info;
}

function render() {
  document.title = `${cam.name} – TRV Webbkameror`;
  $("camName").textContent = cam.name;
  $("camMeta").textContent = [cam.county && `${cam.county} län`, cam.type, cam.direction && `Riktning ${cam.direction}°`,
    `${cam.snapshot_count} sparade bilder`, `${cam.archive_days} dagar i årsarkivet`].filter(Boolean).join(" · ");
  $("enabled").checked = cam.enabled;
  $("enabledLabel").textContent = cam.enabled ? "Aktiv – sparar bilder" : "Inaktiv";
  renderTempSetting();
  if (!shot) showLatest();
  renderTabs();
  renderClip();
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

$("tabs").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-period]");
  if (!b) return;
  activePeriod = b.dataset.period;
  renderTabs();
  renderClip();
});

$("enabled").addEventListener("change", async (ev) => {
  try {
    await api(`api/cameras/${encodeURIComponent(camId)}`, { method: "PATCH", body: { enabled: ev.target.checked } });
    await load();
    loadStatus();
  } catch (e) { alert(e.message); ev.target.checked = !ev.target.checked; }
});

$("tempOverlay").addEventListener("change", async (ev) => {
  try {
    await api(`api/cameras/${encodeURIComponent(camId)}`, { method: "PATCH", body: { temp_overlay: ev.target.checked } });
    cam.temp_overlay = ev.target.checked;
    renderTempSetting();
  } catch (e) { alert(e.message); ev.target.checked = !ev.target.checked; }
});

$("generate").addEventListener("click", async () => {
  const chosen = [...document.querySelectorAll("#periodChecks input:checked")].map((i) => i.value);
  if (!chosen.length) { alert("Välj minst en period."); return; }
  try {
    const job = await api(`api/cameras/${encodeURIComponent(camId)}/timelapses`, { method: "POST", body: { periods: chosen } });
    renderJob(job);
  } catch (e) { alert(`Kunde inte starta: ${e.message}`); }
});

(async () => {
  if (!camId) { $("camName").textContent = "Ingen kamera vald"; return; }
  periods = await api("api/periods");
  $("periodChecks").innerHTML = periods.map((p) =>
    `<label class="check"><input type="checkbox" value="${p.key}" checked> ${esc(p.label)}</label>`).join("");
  try { await load(); } catch (e) { $("camName").textContent = e.message; return; }
  try { await loadDay(null); } catch (_) { /* browsing stays hidden */ }
  updateNav();
  if (cam.lat != null) {
    const m = L.map("miniMap", { zoomControl: false, attributionControl: false }).setView([cam.lat, cam.lon], 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(m);
    L.circleMarker([cam.lat, cam.lon], { radius: 8, color: "#0b6b2f", fillColor: "#2ea043", fillOpacity: 1 }).addTo(m);
  }
  loadStatus();
})();
