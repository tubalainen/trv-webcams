/* Camera detail page */
const camId = new URLSearchParams(location.search).get("id");
let periods = [];
let cam = null;
let activePeriod = "1d";
let pollTimer = null;

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
  const meta = (t) => t ? `${t.frames} bilder · ${fmtBytes(t.size_bytes)} · ${esc(t.encoder)} · skapad ${fmtTime(t.created_at)}` : "";
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

function render() {
  document.title = `${cam.name} – TRV Webbkameror`;
  $("camName").textContent = cam.name;
  $("camMeta").textContent = [cam.county && `${cam.county} län`, cam.type, cam.direction && `Riktning ${cam.direction}°`,
    `${cam.snapshot_count} sparade bilder`, `${cam.archive_days} dagar i årsarkivet`].filter(Boolean).join(" · ");
  $("enabled").checked = cam.enabled;
  $("enabledLabel").textContent = cam.enabled ? "Aktiv – sparar bilder" : "Inaktiv";
  $("latest").src = `api/cameras/${encodeURIComponent(camId)}/latest.jpg?t=${Date.now()}`;
  $("latestCaption").textContent = cam.last_photo_time ? `Senaste sparade bild: ${fmtTime(cam.last_photo_time)}` :
    "Senaste bild från Trafikverket";
  renderTabs();
  renderClip();
  renderJob(cam.last_job);
}

async function load() {
  cam = await api(`api/cameras/${encodeURIComponent(camId)}`);
  render();
}

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
  if (cam.lat != null) {
    const m = L.map("miniMap", { zoomControl: false, attributionControl: false }).setView([cam.lat, cam.lon], 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(m);
    L.circleMarker([cam.lat, cam.lon], { radius: 8, color: "#0b6b2f", fillColor: "#2ea043", fillOpacity: 1 }).addTo(m);
  }
  loadStatus();
})();
