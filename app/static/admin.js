/* Admin page: login and global settings */
const $ = (id) => document.getElementById(id);
let data = null;

function fmtValue(f, v) {
  if (f.kind === "bool" || f.kind === "choice") {
    const c = f.choices.find((x) => x.value === String(v));
    return c ? c.label : String(v);
  }
  return `${String(v).replace(".", ",")}${f.unit ? ` ${f.unit}` : ""}`;
}

function inputFor(f) {
  const id = `f_${f.key}`;
  if (f.choices.length) {
    const opts = f.choices.map((c) =>
      `<option value="${esc(c.value)}"${c.value === String(f.value) ? " selected" : ""}>${esc(c.label)}</option>`).join("");
    return `<select id="${id}" name="${f.key}">${opts}</select>`;
  }
  const step = f.kind === "float" ? "0.5" : "1";
  return `<span class="with-unit"><input id="${id}" name="${f.key}" type="number" step="${step}"
    min="${f.min ?? ""}" max="${f.max ?? ""}" value="${esc(f.value)}" required>${f.unit ? `<span>${esc(f.unit)}</span>` : ""}</span>`;
}

function render() {
  const i = data.info;
  $("info").innerHTML = `
    <span>🎥 ${i.cameras_enabled} aktiva kameror, varav ${i.cameras_enabled_hd} sparar högupplöst</span>
    <span>🖼️ ${i.cameras_with_hd} av ${i.cameras_checked} kontrollerade kameror har högupplösta bilder</span>`;
  $("groups").innerHTML = data.groups.map((g) => {
    const fields = data.fields.filter((f) => f.group === g.key);
    return `<fieldset class="card"><legend>${esc(g.label)}</legend>${fields.map((f) => `
      <div class="setting">
        <label class="field" for="f_${f.key}"><span>${esc(f.label)}</span>${inputFor(f)}</label>
        <p class="muted">${esc(f.help)}${f.help ? " " : ""}
          ${f.saved ? `Ändrad här (standard från .env: ${esc(fmtValue(f, f.env_default))}).
            <button type="button" class="linklike" data-reset="${f.key}">Återställ</button>` :
            "Standard från .env."}</p>
      </div>`).join("")}</fieldset>`;
  }).join("");
}

async function showSettings() {
  data = await api("api/admin/settings");
  $("login").hidden = true;
  $("settings").hidden = false;
  $("logout").hidden = false;
  $("cleanup").hidden = false;
  $("batch").hidden = false;
  render();
  loadStorage();
  setupBatch();
}

/* ---- Generate for a county or all of Sweden ---- */
let batchGen = null;
let batchTimer = null;
let counties = {};
let enabledCams = [];

function batchScope() {
  const county = $("batchScope").value;
  return county
    ? { scope: "county", county_no: Number(county), label: `${counties[county]} län` }
    : { scope: "all", county_no: null, label: "hela Sverige" };
}

const enabledInScope = () => {
  const sc = batchScope();
  return enabledCams.filter((c) => sc.scope === "all" || c.county_no === sc.county_no).length;
};

async function pollBatch(id) {
  clearTimeout(batchTimer);
  try {
    const b = await api(`api/batches/${id}`);
    const s = b.status;
    const done = s.done + s.failed + s.skipped;
    $("batchProgress").textContent = `${b.county}: ${done} av ${b.total} klara` +
      (s.failed ? `, ${s.failed} misslyckades` : "") + (s.skipped ? `, ${s.skipped} hoppades över` : "") +
      (b.finished ? " ✓" : " …");
    if (!b.finished) batchTimer = setTimeout(() => pollBatch(id), 3000);
  } catch (_) { /* batch removed */ }
}

async function setupBatch() {
  if (batchGen) return;
  [counties, enabledCams] = await Promise.all([api("api/counties"), api("api/cameras?enabled=true")]);
  $("batchScope").innerHTML = `<option value="">Hela Sverige</option>` + Object.entries(counties)
    .sort((a, b) => a[1].localeCompare(b[1], "sv"))
    .map(([no, name]) => `<option value="${no}">${esc(name)}</option>`).join("");
  batchGen = await createGenerator($("batchGenerator"), {
    availabilityUrl: () => {
      const sc = batchScope();
      return sc.scope === "county" ? `api/availability?county=${sc.county_no}` : "api/availability";
    },
    scopeText: () => `${batchScope().label}, ${enabledInScope()} aktiva kameror`,
    submit: async (body) => {
      const sc = batchScope();
      const b = await api("api/admin/batches", { method: "POST", body: { ...body, scope: sc.scope, county_no: sc.county_no } });
      pollBatch(b.id);
      const skipped = b.skipped_cameras
        ? ` ${b.skipped_cameras} saknar bilder i tidsspannet och hoppas över (${b.skipped_names.slice(0, 5).join(", ")}${b.skipped_cameras > 5 ? " …" : ""}).`
        : "";
      return `Startad: ${b.total} kameror i kö.${skipped}`;
    },
  });
  $("batchScope").addEventListener("change", () => batchGen.reloadAvailability());
}

/* ---- Storage and cleanup ---- */
let storageData = null;
const isoDay = (d) => d.toISOString().slice(0, 10);
const daysAgo = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return isoDay(d); };
// storage day rows: [day, snapshot images, snapshot bytes, archive images, archive-only bytes]
const dayImages = (d) => (d[1] > 0 ? d[1] : d[3]);
const dayBytes = (d) => d[2] + d[4];
const sumDays = (days, test) => days.filter((d) => test(d[0]))
  .reduce((a, d) => [a[0] + dayImages(d), a[1] + dayBytes(d)], [0, 0]);

async function loadStorage() {
  storageData = await api("api/admin/storage");
  const sel = $("purgeCamera");
  const cur = sel.value;
  sel.innerHTML = `<option value="">Alla kameror</option>` + storageData.cameras.map((c) =>
    `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  sel.value = storageData.cameras.some((c) => c.id === cur) ? cur : "";
  renderStorageTable();
  if (cv.id) {
    if (storageData.cameras.some((c) => c.id === cv.id)) openCamView(cv.id, true);
    else closeCamView();
  }
  updatePreview();
}

function updatePreview() {
  const before = $("purgeDate").value;
  if (!storageData || !before) { $("purgePreview").textContent = ""; return; }
  const what = $("purgeWhat").value, camId = $("purgeCamera").value;
  const cams = storageData.cameras.filter((c) => !camId || c.id === camId);
  let imgs = 0, bytes = 0, clips = 0;
  for (const c of cams) {
    if (what !== "timelapses") {
      const [n, b] = sumDays(c.days, (d) => d < before);
      imgs += n; bytes += b;
    }
    if (what !== "images") {
      const old = c.timelapses.filter((t) => t.created_at.slice(0, 10) < before);
      clips += old.length;
      bytes += old.reduce((a, t) => a + t.bytes, 0);
    }
  }
  const parts = [];
  if (what !== "timelapses") parts.push(`${imgs} bilder`);
  if (what !== "images") parts.push(`${clips} timelapses`);
  $("purgePreview").textContent = `Tar bort ungefär ${parts.join(" och ")} (${fmtBytes(bytes)}) från före ${before}.`;
}

for (const id of ["purgeDate", "purgeWhat", "purgeCamera"]) $(id).addEventListener("input", updatePreview);
document.querySelectorAll("[data-days]").forEach((b) => b.addEventListener("click", () => {
  $("purgeDate").value = daysAgo(Number(b.dataset.days));
  updatePreview();
}));
$("purgeDate").max = isoDay(new Date());

$("purge").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  updatePreview();
  if (!confirm(`${$("purgePreview").textContent}\n\nDet går inte att ångra. Fortsätt?`)) return;
  $("purgeResult").textContent = "Tar bort…";
  try {
    const r = await api("api/admin/purge", { method: "POST", body: {
      before: $("purgeDate").value, what: $("purgeWhat").value, camera_id: $("purgeCamera").value || null } });
    $("purgeResult").textContent = `Klart: ${r.files} filer och ${r.timelapses} timelapses borttagna, ${fmtBytes(r.bytes)} frigjort.`;
    await loadStorage();
    loadStatus();
  } catch (e) { $("purgeResult").textContent = `Kunde inte ta bort: ${e.message}`; }
});

function camStats(c) {
  const [imgs, bytes] = sumDays(c.days, () => true);
  const clipBytes = c.timelapses.reduce((a, t) => a + t.bytes, 0);
  return { imgs, bytes, clipBytes, clips: c.timelapses.length,
    first: c.days.length ? c.days[0][0] : "", last: c.days.length ? c.days[c.days.length - 1][0] : "" };
}

function renderStorageTable() {
  const cams = storageData.cameras;
  let totImgs = 0, totBytes = 0, totClips = 0, totClipBytes = 0, totDays = 0;
  for (const c of cams) {
    const st = camStats(c);
    totImgs += st.imgs; totBytes += st.bytes; totClips += st.clips; totClipBytes += st.clipBytes; totDays += c.days.length;
  }
  $("storageSummary").textContent = `${cams.length} kameror med data · ${totImgs} bilder på ${totDays} kameradagar ` +
    `(${fmtBytes(totBytes)}) · ${totClips} klipp (${fmtBytes(totClipBytes)}) · ${fmtBytes(storageData.disk_free_bytes)} ledigt`;
  const q = $("storageSearch").value.trim().toLowerCase();
  const sort = $("storageSort").value;
  const rows = cams.filter((c) => !q || c.name.toLowerCase().includes(q)).map((c) => ({ c, st: camStats(c) }));
  const by = {
    size: (a, b) => (b.st.bytes + b.st.clipBytes) - (a.st.bytes + a.st.clipBytes),
    images: (a, b) => b.st.imgs - a.st.imgs,
    oldest: (a, b) => (a.st.first || "9").localeCompare(b.st.first || "9"),
    clips: (a, b) => b.st.clips - a.st.clips,
    name: (a, b) => a.c.name.localeCompare(b.c.name, "sv"),
  }[sort];
  rows.sort(by);
  $("storageRows").innerHTML = rows.length ? rows.map(({ c, st }) => `
    <tr class="clickable${cv.id === c.id ? " selected" : ""}" data-cam="${esc(c.id)}" tabindex="0">
      <td><a href="camera.html?id=${encodeURIComponent(c.id)}" data-link>${esc(c.name)}</a>
        ${c.enabled ? "" : '<span class="badge">inaktiv</span>'}</td>
      <td>${st.imgs}</td>
      <td>${c.days.length}${st.first ? ` <span class="muted">(${st.first} – ${st.last})</span>` : ""}</td>
      <td>${fmtBytes(st.bytes)}</td>
      <td>${st.clips}${st.clips ? ` <span class="muted">(${fmtBytes(st.clipBytes)})</span>` : ""}</td>
    </tr>`).join("") : `<tr><td colspan="5" class="muted">${cams.length ? "Inga kameror matchar sökningen." :
      "Inga sparade bilder eller klipp."}</td></tr>`;
}

$("storageSearch").addEventListener("input", renderStorageTable);
$("storageSort").addEventListener("change", renderStorageTable);
$("storageRows").addEventListener("click", (ev) => {
  if (ev.target.closest("[data-link]")) return;
  const row = ev.target.closest("[data-cam]");
  if (row) openCamView(row.dataset.cam);
});
$("storageRows").addEventListener("keydown", (ev) => {
  const row = ev.target.closest("[data-cam]");
  if (row && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); openCamView(row.dataset.cam); }
});

/* ---- Camera view: calendar of days, selection, thumbnails ---- */
const MONTHS_SV = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september",
  "oktober", "november", "december"];
const WD_SV = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"];
const cv = { id: null, cam: null, byDay: new Map(), month: null, selected: new Set(), anchor: null,
  focusDay: null, frames: [], shown: 0, archiveDay: false, image: null };
const THUMBS_PAGE = 120;

function heatClass(d, max) {
  if (!d) return "";
  if (d[1] === 0) return "ha";
  const r = d[1] / max;
  return r > 0.66 ? "h3" : r > 0.33 ? "h2" : "h1";
}

function openCamView(id, keepSelection = false) {
  const cam = storageData.cameras.find((c) => c.id === id);
  if (!cam) return;
  const changed = cv.id !== id;
  cv.id = id;
  cv.cam = cam;
  cv.byDay = new Map(cam.days.map((d) => [d[0], d]));
  if (changed || !keepSelection) { cv.selected = new Set(); cv.anchor = null; cv.focusDay = null; $("cvThumbs").innerHTML = ""; }
  cv.selected = new Set([...cv.selected].filter((d) => cv.byDay.has(d)));
  const last = cam.days.length ? cam.days[cam.days.length - 1][0] : isoDay(new Date());
  if (changed || !cv.month) cv.month = new Date(`${last.slice(0, 7)}-01T00:00`);
  const st = camStats(cam);
  $("camView").hidden = false;
  $("cvName").innerHTML = `<a href="camera.html?id=${encodeURIComponent(id)}">${esc(cam.name)}</a>`;
  $("cvMeta").textContent = `${st.imgs} bilder på ${cam.days.length} dagar (${fmtBytes(st.bytes)})` +
    (st.first ? `, ${st.first} – ${st.last}` : "") + ` · ${st.clips} klipp (${fmtBytes(st.clipBytes)})`;
  $("cvBefore").max = isoDay(new Date());
  renderCamCalendar();
  renderSelection();
  renderStorageTable();
  if (changed) $("camView").scrollIntoView({ behavior: "smooth", block: "start" });
}

function closeCamView() {
  cv.id = null;
  $("camView").hidden = true;
  renderStorageTable();
}

function renderCamCalendar() {
  const m = cv.month;
  const days = cv.cam.days;
  const max = Math.max(1, ...days.map((d) => d[1]));
  $("cvMonth").textContent = `${MONTHS_SV[m.getMonth()]} ${m.getFullYear()}`;
  const first = (m.getDay() + 6) % 7;
  const count = new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate();
  let html = WD_SV.map((w) => `<span class="cal-wd">${w}</span>`).join("");
  for (let i = 0; i < first; i++) html += "<span></span>";
  for (let n = 1; n <= count; n++) {
    const key = `${m.getFullYear()}-${String(m.getMonth() + 1).padStart(2, "0")}-${String(n).padStart(2, "0")}`;
    const d = cv.byDay.get(key);
    const cls = ["cal-day", heatClass(d, max)];
    if (cv.selected.has(key)) cls.push("picked");
    if (cv.focusDay === key) cls.push("focus");
    const title = d ? `${key}: ${dayImages(d)} bilder${d[1] === 0 ? " (timbilder)" : ""}, ${fmtBytes(dayBytes(d))}` : key;
    html += `<button type="button" class="${cls.join(" ")}" data-day="${key}" title="${esc(title)}"${d ? "" : " disabled"}
      aria-pressed="${cv.selected.has(key)}">${n}</button>`;
  }
  $("cvGrid").innerHTML = html;
  const firstDay = days.length ? days[0][0] : null, lastDay = days.length ? days[days.length - 1][0] : null;
  const ym = (d) => d.slice(0, 7);
  const cur = `${m.getFullYear()}-${String(m.getMonth() + 1).padStart(2, "0")}`;
  $("cvPrev").disabled = !firstDay || cur <= ym(firstDay);
  $("cvNext").disabled = !lastDay || cur >= ym(lastDay);
}

function renderSelection() {
  const sel = [...cv.selected].sort();
  let imgs = 0, bytes = 0;
  for (const k of sel) { const d = cv.byDay.get(k); imgs += dayImages(d); bytes += dayBytes(d); }
  $("cvSelection").textContent = sel.length
    ? `${sel.length} ${sel.length === 1 ? "dag" : "dagar"} markerade: ${imgs} bilder, ${fmtBytes(bytes)}` +
      ` (${sel[0]}${sel.length > 1 ? ` – ${sel[sel.length - 1]}` : ""})`
    : "Inga dagar markerade.";
  $("cvDelDays").disabled = !sel.length;
  $("cvClear").disabled = !sel.length;
  const fd = cv.focusDay && cv.byDay.get(cv.focusDay);
  $("cvShow").hidden = !fd;
  $("cvDayTitle").textContent = fd ? `${cv.focusDay}: ${dayImages(fd)} bilder${fd[1] === 0 ? " (bara timbilder)" : ""}` : "";
}

$("cvGrid").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-day]");
  if (!b || b.disabled) return;
  const key = b.dataset.day;
  if (ev.shiftKey && cv.anchor) {
    const [a, z] = [cv.anchor, key].sort();
    for (const d of cv.byDay.keys()) if (d >= a && d <= z) cv.selected.add(d);
  } else if (cv.selected.has(key)) {
    cv.selected.delete(key);
  } else {
    cv.selected.add(key);
  }
  cv.anchor = key;
  if (cv.focusDay !== key) $("cvThumbs").innerHTML = "";
  cv.focusDay = key;
  $("cvMore").hidden = true;
  renderCamCalendar();
  renderSelection();
});
$("cvPrev").addEventListener("click", () => { cv.month = new Date(cv.month.getFullYear(), cv.month.getMonth() - 1, 1); renderCamCalendar(); });
$("cvNext").addEventListener("click", () => { cv.month = new Date(cv.month.getFullYear(), cv.month.getMonth() + 1, 1); renderCamCalendar(); });
$("cvClose").addEventListener("click", closeCamView);
$("cvClear").addEventListener("click", () => { cv.selected.clear(); renderCamCalendar(); renderSelection(); });

const camApi = () => `api/admin/cameras/${encodeURIComponent(cv.id)}`;
const thumbUrl = (name) => cv.archiveDay
  ? `${camApi()}/archive/${cv.focusDay}/${name}.jpg`
  : `api/cameras/${encodeURIComponent(cv.id)}/snapshots/${cv.focusDay}/${name}.jpg`;
const fmtName = (n) => (n.length === 2 ? `${n}:00` : `${n.slice(0, 2)}:${n.slice(2, 4)}:${n.slice(4, 6)}`);

function renderThumbs() {
  const next = cv.frames.slice(cv.shown, cv.shown + THUMBS_PAGE);
  $("cvThumbs").insertAdjacentHTML("beforeend", next.map((n) => `
    <button type="button" class="thumb" data-img="${n}" title="${fmtName(n)}">
      <img src="${thumbUrl(n)}" alt="${cv.focusDay} ${fmtName(n)}" loading="lazy"><span>${fmtName(n)}</span>
    </button>`).join(""));
  cv.shown += next.length;
  $("cvMore").hidden = cv.shown >= cv.frames.length;
  $("cvMore").textContent = `Visa fler (${cv.frames.length - cv.shown} kvar)`;
}

$("cvShow").addEventListener("click", async () => {
  const d = cv.byDay.get(cv.focusDay);
  cv.archiveDay = d[1] === 0;
  const r = cv.archiveDay
    ? await api(`${camApi()}/archive?day=${cv.focusDay}`)
    : await api(`api/cameras/${encodeURIComponent(cv.id)}/snapshots?day=${cv.focusDay}`);
  cv.frames = r.frames;
  cv.shown = 0;
  $("cvThumbs").innerHTML = cv.frames.length ? "" : '<p class="muted">Inga bilder den dagen.</p>';
  renderThumbs();
});
$("cvMore").addEventListener("click", renderThumbs);

$("cvThumbs").addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-img]");
  if (!t) return;
  cv.image = t.dataset.img;
  $("cvImageEl").src = thumbUrl(cv.image);
  $("cvImageCap").textContent = `${cv.cam.name} · ${cv.focusDay} ${fmtName(cv.image)}`;
  $("cvImage").showModal();
});

async function cvDelete(question, request) {
  if (!confirm(`${question}\n\nDet går inte att ångra.`)) return null;
  $("cvResult").textContent = "Tar bort…";
  try {
    const r = await request();
    $("cvResult").textContent = `Klart: ${r.files} filer${r.timelapses ? ` och ${r.timelapses} klipp` : ""} borttagna, ${fmtBytes(r.bytes)} frigjort.`;
    await loadStorage();
    loadStatus();
    return r;
  } catch (e) {
    $("cvResult").textContent = `Kunde inte ta bort: ${e.message}`;
    return null;
  }
}

$("cvDelImage").addEventListener("click", async () => {
  const name = cv.image;
  const r = await cvDelete(`Ta bort bilden ${cv.focusDay} ${fmtName(name)}?`,
    () => api(`${camApi()}/snapshots/${cv.focusDay}/${name}.jpg`, { method: "DELETE" }));
  if (!r) return;
  $("cvImage").close();
  cv.frames = cv.frames.filter((n) => n !== name);
  $("cvThumbs").querySelector(`[data-img="${name}"]`)?.remove();
  cv.shown = Math.max(0, cv.shown - 1);
});

$("cvDelDays").addEventListener("click", () => {
  const days = [...cv.selected].sort();
  cvDelete(`Ta bort alla bilder från ${days.length} ${days.length === 1 ? "dag" : "dagar"} (${$("cvSelection").textContent})?`,
    () => api(`${camApi()}/delete-images`, { method: "POST", body: { days } }))
    .then((r) => { if (r) { cv.selected.clear(); $("cvThumbs").innerHTML = ""; renderSelection(); } });
});

$("cvDelBefore").addEventListener("click", () => {
  const before = $("cvBefore").value;
  if (!before) { $("cvResult").textContent = "Välj ett datum."; return; }
  const [n, b] = sumDays(cv.cam.days, (d) => d < before);
  cvDelete(`Ta bort ${n} bilder (${fmtBytes(b)}) från före ${before} för ${cv.cam.name}?`,
    () => api(`${camApi()}/delete-images`, { method: "POST", body: { before } }));
});

$("cvDelAll").addEventListener("click", () => {
  const st = camStats(cv.cam);
  cvDelete(`Ta bort ALLA ${st.imgs} bilder (${fmtBytes(st.bytes)}) för ${cv.cam.name}?`,
    () => api(`${camApi()}/delete-images`, { method: "POST", body: { all: true } }));
});

$("cvDelClips").addEventListener("click", () => {
  const st = camStats(cv.cam);
  cvDelete(`Ta bort alla ${st.clips} klipp (${fmtBytes(st.clipBytes)}) för ${cv.cam.name}?`,
    () => api(`${camApi()}/timelapses`, { method: "DELETE" }));
});

$("wipeConfirm").addEventListener("input", () => {
  $("wipeBtn").disabled = $("wipeConfirm").value.trim() !== "RENSA ALLT";
});

$("wipe").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!confirm("Rensa ALLA bilder, timelapses och sparad vägtemperatur? Det går inte att ångra.")) return;
  $("wipeResult").textContent = "Rensar…";
  try {
    const r = await api("api/admin/wipe", { method: "POST", body: { confirm: $("wipeConfirm").value } });
    $("wipeResult").textContent = `Klart: ${r.files} filer och ${r.timelapses} timelapses borttagna, ${fmtBytes(r.bytes)} frigjort.`;
    $("wipeConfirm").value = "";
    $("wipeBtn").disabled = true;
    await loadStorage();
    loadStatus();
  } catch (e) { $("wipeResult").textContent = `Kunde inte rensa: ${e.message}`; }
});

async function save(body) {
  $("save").disabled = true;
  $("saveResult").textContent = "Sparar…";
  try {
    data = await api("api/admin/settings", { method: "PUT", body });
    render();
    const n = data.changed.length;
    $("saveResult").textContent = (n ? `Sparat (${n} ändrade). ` : "Inget ändrades. ") + data.applied.join(" ");
    loadStatus();
  } catch (e) {
    $("saveResult").textContent = `Kunde inte spara: ${e.message}`;
  } finally {
    $("save").disabled = false;
  }
}

$("settings").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const values = {};
  for (const f of data.fields) {
    const el = $(`f_${f.key}`);
    const v = f.kind === "int" ? parseInt(el.value, 10) : f.kind === "float" ? parseFloat(el.value) :
      f.kind === "bool" ? el.value === "true" : el.value;
    if (v !== f.value) values[f.key] = v;
  }
  save({ values });
});

$("groups").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-reset]");
  if (b) save({ reset: [b.dataset.reset] });
});

$("login").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("loginError").textContent = "";
  try {
    await api("api/admin/login", { method: "POST", body: { password: $("password").value } });
    $("password").value = "";
    await showSettings();
  } catch (e) {
    $("loginError").textContent = e.message;
  }
});

$("logout").addEventListener("click", async () => {
  await api("api/admin/logout", { method: "POST" });
  location.reload();
});

(async () => {
  loadStatus();
  const s = await api("api/admin/session");
  if (!s.enabled) { $("disabled").hidden = false; return; }
  if (s.logged_in) await showSettings();
  else { $("login").hidden = false; $("password").focus(); }
})();
