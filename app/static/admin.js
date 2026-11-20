/* Admin page: navigation, overview, generation, storage, settings and maintenance */
const $ = (id) => document.getElementById(id);
let data = null; // settings payload from api/admin/settings

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/* ---- Navigation (hash routing) ---- */
const VIEWS = {
  oversikt: "Översikt", generera: "Generera", klipp: "Klipp", lagring: "Lagring",
  installningar: "Inställningar", underhall: "Underhåll",
};
let currentView = null;

const viewFromHash = () => (VIEWS[location.hash.slice(1)] ? location.hash.slice(1) : "oversikt");

function showView(name) {
  currentView = name;
  document.querySelectorAll("[data-view]").forEach((s) => { s.hidden = s.dataset.view !== name; });
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === name) {
      a.setAttribute("aria-current", "page");
      a.scrollIntoView({ block: "nearest", inline: "nearest" });
    } else {
      a.removeAttribute("aria-current");
    }
  });
  document.title = `${VIEWS[name]} – Admin – TRV Webbkameror`;
  window.scrollTo(0, 0);
  if (name === "oversikt") renderOverview();
  if (name === "generera") setupBatch();
  if (name === "klipp") {
    loadClipBatches().then(() => {
      if (pendingBatch) { $("clipBatch").value = pendingBatch; pendingBatch = null; }
      loadClips(true);
    });
  }
  if (name === "lagring" && (!storageData || Date.now() - storageAt > 60000)) loadStorage();
}

async function route() {
  const next = viewFromHash();
  if (currentView === "installningar" && next !== "installningar" && dirtyFields().length) {
    const n = dirtyFields().length;
    const choice = await appDialog({
      title: "Osparade ändringar",
      body: `Du har ${plural(n, "osparad ändring", "osparade ändringar")} i inställningarna.`,
      buttons: [
        { label: "Stanna kvar", value: "stay", kind: "ghost" },
        { label: "Släng ändringarna", value: "discard", kind: "ghost" },
        { label: "Spara", value: "save" },
      ],
    });
    if (choice === "save" && await saveDirty()) { showView(next); return; }
    if (choice === "discard") { renderSettings(); showView(next); return; }
    history.replaceState(null, "", "#installningar"); // stay
    return;
  }
  if (next !== currentView) showView(next);
}
window.addEventListener("hashchange", route);

/* ---- Cameras (names for jobs, enabled list for batches) ---- */
let allCams = [];
let camsAt = 0;
async function loadCams() {
  if (Date.now() - camsAt > 60000) {
    allCams = await api("api/cameras");
    camsAt = Date.now();
  }
  return allCams;
}
const camName = (id) => allCams.find((c) => c.id === id)?.name || id;

/* ---- Översikt ---- */
const RUNS = [
  ["collect", "Hämta bilder"],
  ["temps", "Hämta vägtemperatur"],
  ["retention", "Daglig rensning"],
  ["sync", "Synka kameralistan"],
];
const JOB_STATUS = {
  queued: ["I kö", ""], running: ["Pågår", "info"], done: ["Klar", "on"],
  failed: ["Misslyckades", "bad"], skipped: ["Hoppades över", ""],
};

function relTime(iso) {
  if (!iso) return "–";
  const min = Math.round((new Date(iso) - Date.now()) / 60000);
  if (min < 1) return "nu";
  if (min < 60) return `om ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `om ${h} h${min % 60 ? ` ${min % 60} min` : ""}`;
  return `om ${plural(Math.round(h / 24), "dygn", "dygn")}`;
}

const statCard = (icon, label, value, sub, extra = "") => `
  <div class="card stat">
    <span class="stat-label"><span aria-hidden="true">${icon}</span> ${label}</span>
    <span class="stat-value">${value}</span>
    <span class="stat-sub">${sub}</span>${extra}
  </div>`;

const alertBox = (kind, text, link) => `
  <div class="alert ${kind}" role="${kind === "bad" ? "alert" : "status"}">
    <span class="alert-ico" aria-hidden="true">${{ bad: "⛔", warn: "⚠️", info: "ℹ️", ok: "✓" }[kind]}</span>
    <span>${text}</span>${link ? `<a href="${link[0]}">${link[1]}</a>` : ""}
  </div>`;

async function renderOverview() {
  let s, jobs;
  try {
    [s, jobs] = await Promise.all([api("api/status"), api("api/jobs?limit=10"), loadCams()]);
  } catch (e) {
    $("ovAlerts").innerHTML = alertBox("bad", `Kunde inte läsa läget: ${esc(e.message)}`);
    return;
  }
  const i = data.info;
  const appUsed = s.usage.snapshots_bytes + s.usage.timelapses_bytes;
  const total = s.disk_total_bytes || 1;
  const free = s.disk_free_bytes;
  const other = Math.max(0, total - free - appUsed);
  const growth = Math.max(0, s.estimated_bytes_at_retention - s.usage.snapshots_bytes);
  const pct = (b) => `${Math.min(100, Math.max(0, (b / total) * 100)).toFixed(1)}%`;
  const failed = jobs.filter((j) => j.status === "failed");

  // Warnings
  const alerts = [];
  if (!s.api_key_configured) {
    alerts.push(alertBox("bad", "<b>TRV_API_KEY saknas.</b> Inga bilder kan hämtas från Trafikverket. " +
      "Sätt nyckeln i <code>.env</code> och starta om containern."));
  }
  if (free < 5 * 1024 ** 3 || free / total < 0.1) {
    alerts.push(alertBox("bad", `<b>Lite diskutrymme kvar:</b> ${fmtBytes(free)} ledigt ` +
      `(${Math.round((free / total) * 100)} %).`, ["#lagring", "Gå till Lagring"]));
  } else if (growth > free) {
    alerts.push(alertBox("warn", `<b>Disken räcker inte vid full retention.</b> Bilderna kan växa med ungefär ` +
      `${fmtBytes(growth)}, men bara ${fmtBytes(free)} är ledigt. Sänk retentionen, välj färre kameror ` +
      "eller lågupplösta bilder.", ["#installningar", "Inställningar"]));
  }
  if (failed.length) {
    alerts.push(alertBox("warn", `${failed.length} av de ${jobs.length} senaste genereringarna misslyckades.`));
  }
  if (!s.cameras_enabled) {
    alerts.push(alertBox("info", "Inga kameror är aktiva. Välj kameror på kartan för att börja spara bilder.",
      ["./", "Välj kameror"]));
  }
  $("ovAlerts").innerHTML = alerts.join("") || alertBox("ok", "Allt ser bra ut.");

  // Cards
  const enc = s.encoder;
  $("ovStats").innerHTML = [
    statCard("🎥", "Kameror", `${s.cameras_enabled}`,
      `aktiva av ${s.cameras_total} · ${i.cameras_enabled_hd} sparar högupplöst`,
      '<a class="stat-link" href="./">Välj kameror →</a>'),
    statCard("💾", "Lagring", fmtBytes(appUsed),
      `bilder och klipp · ≈${fmtBytes(s.estimated_bytes_at_retention)} vid full retention`,
      `<div class="disk-bar" title="Appen ${fmtBytes(appUsed)}, annat ${fmtBytes(other)}, ledigt ${fmtBytes(free)}">
        <i class="app" style="width:${pct(appUsed)}"></i><i class="other" style="width:${pct(other)}"></i></div>
       <span class="stat-sub">${fmtBytes(free)} ledigt av ${fmtBytes(total)}</span>`),
    statCard("⚙️", "Videokodare", enc ? esc(enc.codec) : "detekterar…",
      enc ? (enc.mode === "cpu" ? "CPU-kodning, ingen GPU hittades" :
        esc(s.gpus.length ? s.gpus.join(", ") : "GPU")) : "Kontrollerar GPU vid start"),
    statCard("⏳", "Jobb", `${s.jobs_pending}`, s.jobs_pending ? "i kö eller pågår" : "Inga jobb i kö"),
  ].join("");

  // Scheduled runs
  const runs = RUNS.filter(([id]) => id in s.next_runs);
  $("ovRuns").innerHTML = runs.length
    ? runs.map(([id, label]) => `<dt>${label}</dt><dd><b>${relTime(s.next_runs[id])}</b>
        <span class="muted">${fmtTime(s.next_runs[id])}</span></dd>`).join("")
    : '<p class="muted">Schemaläggaren är avstängd (<code>SCHEDULER_ENABLED=false</code>).</p>';

  // Recent jobs
  $("ovJobs").innerHTML = jobs.length ? jobs.map((j) => {
    const [label, cls] = JOB_STATUS[j.status] || [j.status, ""];
    return `<li>
      <span class="badge ${cls}">${label}</span>
      <span class="job-main"><span class="job-name"><a href="camera.html?id=${encodeURIComponent(j.camera_id)}">${esc(camName(j.camera_id))}</a>
        ${j.status === "done" && j.clip_id ? `<button type="button" class="btn small ghost" data-open-clip="${j.clip_id}"
          data-cam="${esc(j.camera_id)}">▶ Visa</button>` : ""}</span>
        <span class="muted">${fmtTime(j.range_start)} – ${fmtTime(j.range_end)} · ${esc(j.formats.join(", ").toUpperCase())}
          ${j.batch_id ? " · län/alla aktiva" : ""}</span>
        ${j.status === "failed" && j.message ? `<span class="muted warn">${esc(j.message)}</span>` : ""}</span>
      <span class="muted job-time">${fmtTime(j.created_at)}</span></li>`;
  }).join("") : '<li class="muted">Inga genereringar ännu.</li>';
}

/* ---- Generera: a county or all of Sweden ---- */
let batchGen = null;
let batchTimer = null;
let counties = {};

function batchScope() {
  const county = $("batchScope").value;
  return county
    ? { scope: "county", county_no: Number(county), label: `aktiva i ${counties[county]}` }
    : { scope: "all", county_no: null, label: "alla aktiva kameror" };
}

const enabledInScope = () => {
  const sc = batchScope();
  return allCams.filter((c) => c.enabled && (sc.scope === "all" || c.county_no === sc.county_no)).length;
};

async function pollBatch(id) {
  clearTimeout(batchTimer);
  try {
    const b = await api(`api/batches/${id}`);
    const s = b.status;
    const done = s.done + s.failed + s.skipped;
    $("batchProgressWrap").hidden = false;
    $("batchBar").style.width = `${b.total ? (done / b.total) * 100 : 100}%`;
    $("batchProgress").textContent = `${b.county}: ${done} av ${b.total} klara` +
      (s.failed ? `, ${s.failed} misslyckades` : "") + (s.skipped ? `, ${s.skipped} hoppades över` : "") +
      (b.finished ? " ✓" : " …");
    $("batchShowClips").hidden = !b.finished || !s.done;
    $("batchShowClips").dataset.batch = id;
    if (!b.finished) batchTimer = setTimeout(() => pollBatch(id), 3000);
    else toast(`Generering klar för ${b.county}: ${b.total - s.failed} av ${b.total} klipp.`,
      s.failed ? "error" : "ok");
  } catch (_) { /* batch removed */ }
}

// Only active cameras are generated, so the choices show how many are active. Counties without
// active cameras are listed last and disabled.
function renderBatchScope() {
  const sel = $("batchScope");
  const cur = sel.value;
  const active = {};
  for (const c of allCams) if (c.enabled) active[c.county_no] = (active[c.county_no] || 0) + 1;
  const total = Object.values(active).reduce((a, n) => a + n, 0);
  const sorted = Object.entries(counties).sort((a, b) => a[1].localeCompare(b[1], "sv"));
  const opt = ([no, name], n) =>
    `<option value="${no}"${n ? "" : " disabled"}>${esc(name)}${n ? ` (${n} aktiva)` : ""}</option>`;
  const withCams = sorted.filter(([no]) => active[no]);
  const without = sorted.filter(([no]) => !active[no]);
  sel.innerHTML = `<option value="">Alla aktiva kameror (${total})</option>` +
    (withCams.length ? `<optgroup label="Län">${withCams.map((c) => opt(c, active[c[0]])).join("")}</optgroup>` : "") +
    (without.length ? `<optgroup label="Inga aktiva kameror">${without.map((c) => opt(c, 0)).join("")}</optgroup>` : "");
  sel.value = active[cur] ? cur : "";
}

async function setupBatch() {
  if (batchGen) { await loadCams(); renderBatchScope(); return; }
  [counties] = await Promise.all([api("api/counties"), loadCams()]);
  renderBatchScope();
  batchGen = await createGenerator($("batchGenerator"), {
    availabilityUrl: () => {
      const sc = batchScope();
      return sc.scope === "county" ? `api/availability?county=${sc.county_no}` : "api/availability";
    },
    scopeText: () => (batchScope().scope === "all"
      ? `alla aktiva kameror (${enabledInScope()})` : `${enabledInScope()} ${batchScope().label}`),
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

/* ---- Klipp: gallery of generated clips and a player ---- */
const CLIPS_PAGE = 48;
const clips = { items: [], total: 0, bytes: 0, seq: 0 };
const player = { list: [], index: 0, kind: "mp4" };
let pendingBatch = null; // set by "Visa klippen" after a batch, applied when the view opens

const clipKind = () => document.querySelector('input[name="clipKind"]:checked').value;
const sameDay = (a, b) => a && b && a.slice(0, 10) === b.slice(0, 10);
function fmtRange(a, b) {
  const t = (iso) => new Date(iso).toLocaleTimeString("sv-SE", { hour: "2-digit", minute: "2-digit" });
  return sameDay(a, b) ? `${fmtTime(a)}–${t(b)}` : `${fmtTime(a)} – ${fmtTime(b)}`;
}

async function loadClipBatches() {
  try {
    const batches = await api("api/batches?limit=30");
    const sel = $("clipBatch");
    const cur = sel.value;
    sel.innerHTML = `<option value="">Alla klipp</option>` + batches.map((b) =>
      `<option value="${b.id}">${fmtTime(b.created_at)} · ${esc(b.county)} · ${plural(b.clips, "klipp", "klipp")}</option>`).join("");
    sel.value = [...sel.options].some((o) => o.value === cur) ? cur : "";
  } catch (_) { /* the filter is optional */ }
}

async function loadClips(reset) {
  const seq = ++clips.seq; // ignore answers to older searches
  const params = new URLSearchParams({ limit: CLIPS_PAGE, offset: reset ? 0 : clips.items.length });
  if ($("clipSearch").value.trim()) params.set("q", $("clipSearch").value.trim());
  if ($("clipBatch").value) params.set("batch_id", $("clipBatch").value);
  if (clipKind()) params.set("kind", clipKind());
  let r;
  try { r = await api(`api/clips?${params}`); } catch (e) { toast(`Kunde inte läsa klippen: ${e.message}`, "error"); return; }
  if (seq !== clips.seq) return;
  if (reset) { clips.items = []; $("clipGrid").innerHTML = ""; }
  clips.total = r.total;
  clips.bytes = r.bytes;
  const start = clips.items.length;
  clips.items.push(...r.clips);
  $("clipGrid").insertAdjacentHTML("beforeend", r.clips.map((c, i) => clipCard(c, start + i)).join(""));
  $("clipGrid").querySelectorAll("video[data-src]").forEach((v) => thumbObserver.observe(v));
  $("clipCount").textContent = clips.total
    ? `${plural(clips.total, "klipp", "klipp")} · ${fmtBytes(clips.bytes)}` +
      (clips.items.length < clips.total ? ` · visar ${clips.items.length}` : "")
    : "";
  if (!clips.total) {
    $("clipGrid").innerHTML = `<p class="muted empty">${$("clipSearch").value || $("clipBatch").value || clipKind()
      ? "Inga klipp matchar filtret." : 'Inga klipp ännu. Skapa klipp under <a href="#generera">Generera</a> eller på en kamerasida.'}</p>`;
  }
  $("clipMore").hidden = clips.items.length >= clips.total;
  $("clipMore").textContent = `Visa fler (${clips.total - clips.items.length} kvar)`;
}

function clipCard(c, i) {
  const thumb = c.mp4
    ? `<video data-src="${c.mp4.url}#t=0.5" preload="none" muted playsinline loop></video>`
    : `<img src="${c.gif.url}" alt="" loading="lazy">`;
  const badges = [c.mp4 && `MP4 ${fmtBytes(c.mp4.bytes)}`, c.gif && `GIF ${fmtBytes(c.gif.bytes)}`].filter(Boolean);
  return `<button type="button" class="clip-card" data-i="${i}" aria-label="Spela ${esc(c.camera_name)}, ${fmtRange(c.range_start, c.range_end)}">
    <span class="clip-thumb">${thumb}${c.duration_s ? `<span class="clip-dur">${c.duration_s} s</span>` : ""}<span class="clip-play" aria-hidden="true">▶</span></span>
    <span class="clip-info">
      <span class="clip-name">${esc(c.camera_name)}</span>
      <span class="muted">${fmtRange(c.range_start, c.range_end)}</span>
      <span class="clip-badges">${badges.map((b) => `<span class="badge">${b}</span>`).join("")}
        ${c.batch_id ? '<span class="badge info">körning</span>' : ""}</span>
    </span></button>`;
}

// Load video thumbnails only when they scroll into view
const thumbObserver = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    e.target.src = e.target.dataset.src;
    e.target.preload = "metadata";
    e.target.removeAttribute("data-src");
    thumbObserver.unobserve(e.target);
  }
}, { rootMargin: "200px" });

let clipSearchTimer;
$("clipSearch").addEventListener("input", () => { clearTimeout(clipSearchTimer); clipSearchTimer = setTimeout(() => loadClips(true), 250); });
$("clipBatch").addEventListener("change", () => loadClips(true));
document.querySelectorAll('input[name="clipKind"]').forEach((r) => r.addEventListener("change", () => loadClips(true)));
$("clipMore").addEventListener("click", () => loadClips(false));
$("clipGrid").addEventListener("click", (ev) => {
  const card = ev.target.closest("[data-i]");
  if (card) openPlayer(clips.items, Number(card.dataset.i));
});
// Preview on hover
$("clipGrid").addEventListener("mouseover", (ev) => {
  const v = ev.target.closest(".clip-card")?.querySelector("video:not([data-src])");
  if (v && v.paused) v.play().catch(() => {});
});
$("clipGrid").addEventListener("mouseout", (ev) => {
  const card = ev.target.closest(".clip-card");
  if (!card || card.contains(ev.relatedTarget)) return;
  const v = card.querySelector("video");
  if (v && !v.paused) { v.pause(); v.currentTime = 0.5; }
});

$("batchShowClips").addEventListener("click", () => {
  pendingBatch = $("batchShowClips").dataset.batch;
  location.hash = "klipp";
});

function openPlayer(list, index) {
  player.list = list;
  player.index = index;
  const c = list[index];
  player.kind = c.mp4 ? "mp4" : "gif";
  renderPlayer();
  if (!$("clipPlayer").open) $("clipPlayer").showModal();
}

function renderPlayer() {
  const c = player.list[player.index];
  if (!c.mp4 && player.kind === "mp4") player.kind = "gif";
  if (!c.gif && player.kind === "gif") player.kind = "mp4";
  $("cpTitle").innerHTML = `<a href="camera.html?id=${encodeURIComponent(c.camera_id)}">${esc(c.camera_name || camName(c.camera_id))}</a>`;
  $("cpMeta").textContent = `${fmtRange(c.range_start, c.range_end)}${c.duration_s ? ` · ${c.duration_s} s` : ""} · ${c.frames} bilder` +
    (c.encoder && c.encoder !== "gif" ? ` · ${c.encoder}` : "") + ` · skapad ${fmtTime(c.created_at)}`;
  $("cpTabs").hidden = !(c.mp4 && c.gif);
  document.querySelectorAll('input[name="cpKind"]').forEach((r) => { r.checked = r.value === player.kind; });
  const m = c[player.kind];
  $("cpMedia").innerHTML = player.kind === "mp4"
    ? `<video src="${m.url}" controls autoplay muted loop playsinline></video>`
    : `<img src="${m.url}" alt="GIF för ${esc(c.camera_name || "")}">`;
  const dl = (k) => c[k] ? `<a class="btn small ghost" href="${c[k].url}" download="${esc(c.camera_id)}-${c.id}.${k}">⬇ ${k.toUpperCase()} (${fmtBytes(c[k].bytes)})</a>` : "";
  $("cpActions").innerHTML = `<span class="cp-dl">${dl("mp4")}${dl("gif")}</span>
    <span class="cp-del">${c.mp4 && c.gif ? `<button type="button" class="btn small danger ghost" data-del="mp4">Ta bort MP4</button>
      <button type="button" class="btn small danger ghost" data-del="gif">Ta bort GIF</button>` : ""}
      <button type="button" class="btn small danger" data-del="all">Ta bort klippet</button></span>`;
  $("cpPrev").disabled = player.index === 0;
  $("cpNext").disabled = player.index >= player.list.length - 1;
  $("cpPrev").hidden = $("cpNext").hidden = player.list.length < 2;
}

$("cpTabs").addEventListener("change", (ev) => { player.kind = ev.target.value; renderPlayer(); });
$("cpPrev").addEventListener("click", () => { if (player.index > 0) { player.index--; renderPlayer(); } });
$("cpNext").addEventListener("click", () => {
  if (player.index < player.list.length - 1) { player.index++; renderPlayer(); }
});
$("clipPlayer").addEventListener("keydown", (ev) => {
  if (ev.key === "ArrowLeft") $("cpPrev").click();
  if (ev.key === "ArrowRight") $("cpNext").click();
});
$("clipPlayer").addEventListener("close", () => { $("cpMedia").innerHTML = ""; });

$("cpActions").addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-del]");
  if (!b) return;
  const c = player.list[player.index];
  const kind = b.dataset.del;
  const what = kind === "all" ? "hela klippet" : `klippets ${kind.toUpperCase()}`;
  const ok = await confirmDialog({
    title: `Ta bort ${what}?`, danger: true, confirmText: "Ta bort",
    body: `${c.camera_name || camName(c.camera_id)}, ${fmtRange(c.range_start, c.range_end)}.\n\nKlippet kan genereras igen.`,
  });
  if (!ok) return;
  try {
    const r = await api(`api/cameras/${encodeURIComponent(c.camera_id)}/clips/${c.id}?kind=${kind}`, { method: "DELETE" });
    toast(`Borttaget: ${fmtBytes(r.bytes)} frigjort.`, "ok");
    if (kind === "all") {
      player.list.splice(player.index, 1);
      if (!player.list.length) $("clipPlayer").close();
      else { player.index = Math.min(player.index, player.list.length - 1); renderPlayer(); }
    } else {
      c[kind] = null;
      renderPlayer();
    }
    storageData = null;
    loadStatus();
    if (currentView === "klipp") loadClips(true);
  } catch (e) {
    toast(`Kunde inte ta bort: ${e.message}`, "error");
  }
});

// "▶ Visa" in the overview's recent generations
$("ovJobs").addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-open-clip]");
  if (!b) return;
  try {
    const list = await api(`api/cameras/${encodeURIComponent(b.dataset.cam)}/clips`);
    const i = list.findIndex((c) => c.id === Number(b.dataset.openClip));
    if (i < 0) { toast("Klippet finns inte längre.", "info"); return; }
    list.forEach((c) => { c.camera_name = camName(c.camera_id); });
    openPlayer(list, i);
  } catch (e) { toast(`Kunde inte öppna klippet: ${e.message}`, "error"); }
});

/* ---- Lagring ---- */
let storageData = null;
let storageAt = 0;
const isoDay = (d) => d.toISOString().slice(0, 10);
const daysAgo = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return isoDay(d); };
// storage day rows: [day, snapshot images, snapshot bytes, archive images, archive-only bytes]
const dayImages = (d) => (d[1] > 0 ? d[1] : d[3]);
const dayBytes = (d) => d[2] + d[4];
const sumDays = (days, test) => days.filter((d) => test(d[0]))
  .reduce((a, d) => [a[0] + dayImages(d), a[1] + dayBytes(d)], [0, 0]);

async function loadStorage() {
  try {
    storageData = await api("api/admin/storage");
  } catch (e) {
    toast(`Kunde inte läsa lagringen: ${e.message}`, "error");
    return;
  }
  storageAt = Date.now();
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

/** Returns {text, imgs, clips, bytes} for the "older than" form. */
function purgeEstimate() {
  const before = $("purgeDate").value;
  if (!storageData || !before) return null;
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
  if (what !== "timelapses") parts.push(plural(imgs, "bild", "bilder"));
  if (what !== "images") parts.push(plural(clips, "klipp", "klipp"));
  const where = camId ? cams[0]?.name || "" : "alla kameror";
  return { imgs, clips, bytes, text: `${parts.join(" och ")} (${fmtBytes(bytes)}) från före ${before}, ${where}` };
}

function updatePreview() {
  const est = purgeEstimate();
  $("purgePreview").innerHTML = est ? `Tar bort ungefär <b>${esc(est.text)}</b>.` : "Välj ett datum.";
  $("purgeBtn").disabled = !est || (!est.imgs && !est.clips);
}

for (const id of ["purgeDate", "purgeWhat", "purgeCamera"]) $(id).addEventListener("input", updatePreview);
document.querySelectorAll("[data-days]").forEach((b) => b.addEventListener("click", () => {
  $("purgeDate").value = daysAgo(Number(b.dataset.days));
  updatePreview();
}));
$("purgeDate").max = isoDay(new Date());

const doneText = (r) => `${plural(r.files, "fil", "filer")}${r.timelapses ? ` och ${plural(r.timelapses, "klipp", "klipp")}` : ""}` +
  ` borttagna, ${fmtBytes(r.bytes)} frigjort.`;

$("purge").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const est = purgeEstimate();
  if (!est) return;
  const ok = await confirmDialog({
    title: "Ta bort äldre data?", danger: true, confirmText: "Ta bort",
    body: `Tar bort ungefär ${est.text}.\n\nDet går inte att ångra.`,
  });
  if (!ok) return;
  $("purgeBtn").disabled = true;
  try {
    const r = await api("api/admin/purge", { method: "POST", body: {
      before: $("purgeDate").value, what: $("purgeWhat").value, camera_id: $("purgeCamera").value || null } });
    toast(`Klart: ${doneText(r)}`, "ok");
    await loadStorage();
    loadStatus();
  } catch (e) {
    toast(`Kunde inte ta bort: ${e.message}`, "error");
    updatePreview();
  }
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
  const mini = (label, value, sub) =>
    `<div class="card stat"><span class="stat-label">${label}</span><span class="stat-value">${value}</span>` +
    `<span class="stat-sub">${sub}</span></div>`;
  $("storageSummary").innerHTML = [
    mini("Kameror med data", cams.length, `${totDays} kameradagar`),
    mini("Bilder", totImgs.toLocaleString("sv-SE"), fmtBytes(totBytes)),
    mini("Klipp", totClips, fmtBytes(totClipBytes)),
    mini("Ledigt på disken", fmtBytes(storageData.disk_free_bytes), "för nya bilder och klipp"),
  ].join("");
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
    <tr class="clickable" data-cam="${esc(c.id)}" tabindex="0">
      <td><a href="camera.html?id=${encodeURIComponent(c.id)}" data-link>${esc(c.name)}</a>
        ${c.enabled ? "" : '<span class="badge">inaktiv</span>'}</td>
      <td>${st.imgs}</td>
      <td>${c.days.length}${st.first ? ` <span class="muted">(${st.first} – ${st.last})</span>` : ""}</td>
      <td>${fmtBytes(st.bytes)}</td>
      <td>${st.clips}${st.clips ? ` <span class="muted">(${fmtBytes(st.clipBytes)})</span>` : ""}</td>
      <td class="chev" aria-hidden="true">›</td>
    </tr>`).join("") : `<tr><td colspan="6" class="muted">${cams.length ? "Inga kameror matchar sökningen." :
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
  $("storageList").hidden = true;
  $("purge").hidden = true;
  $("storageSummary").hidden = true;
  $("camView").hidden = false;
  $("cvName").innerHTML = `<a href="camera.html?id=${encodeURIComponent(id)}">${esc(cam.name)}</a>`;
  $("cvMeta").textContent = `${st.imgs} bilder på ${cam.days.length} dagar (${fmtBytes(st.bytes)})` +
    (st.first ? `, ${st.first} – ${st.last}` : "") + ` · ${st.clips} klipp (${fmtBytes(st.clipBytes)})`;
  $("cvBefore").max = isoDay(new Date());
  renderCamCalendar();
  renderSelection();
  if (changed) window.scrollTo(0, 0);
}

function closeCamView() {
  const id = cv.id;
  cv.id = null;
  $("camView").hidden = true;
  $("storageList").hidden = false;
  $("purge").hidden = false;
  $("storageSummary").hidden = false;
  renderStorageTable();
  if (id) $("storageRows").querySelector(`[data-cam="${CSS.escape(id)}"]`)?.focus();
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

function selectionText() {
  const sel = [...cv.selected].sort();
  let imgs = 0, bytes = 0;
  for (const k of sel) { const d = cv.byDay.get(k); imgs += dayImages(d); bytes += dayBytes(d); }
  return sel.length
    ? `${plural(sel.length, "dag", "dagar")}: ${plural(imgs, "bild", "bilder")}, ${fmtBytes(bytes)}` +
      ` (${sel[0]}${sel.length > 1 ? ` – ${sel[sel.length - 1]}` : ""})`
    : "";
}

function renderSelection() {
  const n = cv.selected.size;
  $("cvSelection").textContent = n ? selectionText() : "Inga dagar markerade.";
  $("cvDelDays").disabled = !n;
  $("cvClear").disabled = !n;
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

async function cvDelete(title, body, request) {
  const ok = await confirmDialog({ title, body: `${body}\n\nDet går inte att ångra.`, confirmText: "Ta bort", danger: true });
  if (!ok) return null;
  try {
    const r = await request();
    toast(`Klart: ${doneText(r)}`, "ok");
    await loadStorage();
    loadStatus();
    return r;
  } catch (e) {
    toast(`Kunde inte ta bort: ${e.message}`, "error");
    return null;
  }
}

$("cvDelImage").addEventListener("click", async () => {
  const name = cv.image;
  const r = await cvDelete("Ta bort bilden?", `${cv.cam.name}, ${cv.focusDay} ${fmtName(name)}.`,
    () => api(`${camApi()}/snapshots/${cv.focusDay}/${name}.jpg`, { method: "DELETE" }));
  if (!r) return;
  $("cvImage").close();
  cv.frames = cv.frames.filter((n) => n !== name);
  $("cvThumbs").querySelector(`[data-img="${name}"]`)?.remove();
  cv.shown = Math.max(0, cv.shown - 1);
});

$("cvDelDays").addEventListener("click", () => {
  const days = [...cv.selected].sort();
  cvDelete(`Ta bort ${plural(days.length, "dag", "dagar")}?`, `Alla bilder för ${cv.cam.name} under ${selectionText()}.`,
    () => api(`${camApi()}/delete-images`, { method: "POST", body: { days } }))
    .then((r) => { if (r) { cv.selected.clear(); $("cvThumbs").innerHTML = ""; renderSelection(); } });
});

$("cvDelBefore").addEventListener("click", () => {
  const before = $("cvBefore").value;
  if (!before) { toast("Välj ett datum först.", "info"); $("cvBefore").focus(); return; }
  const [n, b] = sumDays(cv.cam.days, (d) => d < before);
  if (!n) { toast(`${cv.cam.name} har inga bilder från före ${before}.`, "info"); return; }
  cvDelete("Ta bort äldre bilder?", `${plural(n, "bild", "bilder")} (${fmtBytes(b)}) från före ${before} för ${cv.cam.name}.`,
    () => api(`${camApi()}/delete-images`, { method: "POST", body: { before } }));
});

$("cvDelAll").addEventListener("click", () => {
  const st = camStats(cv.cam);
  cvDelete("Ta bort alla bilder?", `Alla ${plural(st.imgs, "bild", "bilder")} (${fmtBytes(st.bytes)}) för ${cv.cam.name}. ` +
    "Klippen behålls.", () => api(`${camApi()}/delete-images`, { method: "POST", body: { all: true } }));
});

$("cvDelClips").addEventListener("click", () => {
  const st = camStats(cv.cam);
  if (!st.clips) { toast(`${cv.cam.name} har inga klipp.`, "info"); return; }
  cvDelete("Ta bort alla klipp?", `Alla ${plural(st.clips, "klipp", "klipp")} (${fmtBytes(st.clipBytes)}) för ${cv.cam.name}. ` +
    "Bilderna behålls.", () => api(`${camApi()}/timelapses`, { method: "DELETE" }));
});

/* ---- Underhåll ---- */
$("wipeConfirm").addEventListener("input", () => {
  $("wipeBtn").disabled = $("wipeConfirm").value.trim() !== "RENSA ALLT";
});

$("wipe").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const ok = await confirmDialog({
    title: "Rensa allt?", danger: true, confirmText: "Rensa allt",
    body: "Alla bilder, klipp, sparad vägtemperatur och jobbhistorik tas bort för alla kameror.\n\nDet går inte att ångra.",
  });
  if (!ok) return;
  $("wipeBtn").disabled = true;
  try {
    const r = await api("api/admin/wipe", { method: "POST", body: { confirm: $("wipeConfirm").value } });
    toast(`Allt rensat: ${doneText(r)}`, "ok");
    $("wipeConfirm").value = "";
    storageData = null;
    loadStatus();
  } catch (e) {
    toast(`Kunde inte rensa: ${e.message}`, "error");
    $("wipeBtn").disabled = false;
  }
});

/* ---- Inställningar ---- */
const GROUP_META = {
  cameras: ["🎥", "Standardval för alla kameror. Varje kamera kan välja något annat på sin egen sida."],
  collect: ["📥", "Hur ofta bilder och vägtemperatur hämtas från Trafikverket."],
  retention: ["🗄️", "Hur länge bilder sparas och när den dagliga rensningen körs."],
  encoding: ["🎞️", "Hur timelapses kodas till MP4 och GIF."],
};
// Display order within the cards. nightly_minute is shown in the same row as nightly_hour.
const ORDER = ["trv_fullsize", "temp_overlay_default", "collect_interval_minutes", "temp_collect_interval_minutes",
  "temp_station_max_km", "snapshot_retention_days", "archive_retention_days", "nightly_hour", "hwaccel",
  "max_concurrent_jobs", "max_gpu_encodes", "output_fps", "gif_width", "gif_fps", "gif_max_frames"];
const SUBHEADS = { hwaccel: "Video", gif_width: "GIF" };
const PAIR = { nightly_hour: "nightly_minute" };
const rowKey = (key) => (key === "nightly_minute" ? "nightly_hour" : key);
const field = (key) => data.fields.find((f) => f.key === key);

const isSwitch = (f) => f.kind === "bool" && f.choices.some((c) => c.label === "På");
const isSegment = (f) => !isSwitch(f) && f.choices.length > 0 && f.choices.length <= 3;

function fmtValue(f, v) {
  if (f.choices.length) {
    const c = f.choices.find((x) => x.value === String(v));
    return c ? c.label : String(v);
  }
  return `${String(v).replace(".", ",")}${f.unit ? ` ${f.unit}` : ""}`;
}

function numberInput(f, cls = "") {
  const step = f.kind === "float" ? "0.5" : "1";
  return `<input id="f_${f.key}" class="${cls}" type="number" inputmode="${f.kind === "float" ? "decimal" : "numeric"}"
    step="${step}" min="${f.min ?? ""}" max="${f.max ?? ""}" value="${esc(f.value)}" required>`;
}

function controlFor(f) {
  const id = `f_${f.key}`;
  if (isSwitch(f)) {
    return `<label class="switch big"><input type="checkbox" id="${id}" ${f.value ? "checked" : ""}
      aria-labelledby="l_${f.key}"><span class="slider"></span><span class="switch-text">${f.value ? "På" : "Av"}</span></label>`;
  }
  if (isSegment(f)) {
    return `<div class="seg" role="radiogroup" aria-labelledby="l_${f.key}">${f.choices.map((c) => `
      <label><input type="radio" name="${id}" value="${esc(c.value)}"${c.value === String(f.value) ? " checked" : ""}>
        <span>${esc(c.label)}</span></label>`).join("")}</div>`;
  }
  if (f.choices.length) {
    return `<select id="${id}" aria-labelledby="l_${f.key}">${f.choices.map((c) =>
      `<option value="${esc(c.value)}"${c.value === String(f.value) ? " selected" : ""}>${esc(c.label)}</option>`).join("")}</select>`;
  }
  return `<span class="input-unit">${numberInput(f)}<span>${esc(f.unit)}</span></span>`;
}

function rowFor(f) {
  const pair = PAIR[f.key] && field(PAIR[f.key]);
  const saved = f.saved || (pair && pair.saved);
  const label = pair ? "Daglig rensning kl." : f.label;
  const control = pair
    ? `<span class="time-input">${numberInput(f)}<span>:</span>${numberInput(pair)}<span class="pad"></span></span>`
    : controlFor(f);
  const envText = pair
    ? `${String(f.env_default).padStart(2, "0")}:${String(pair.env_default).padStart(2, "0")}`
    : fmtValue(f, f.env_default);
  const resetKeys = pair ? `${f.key},${pair.key}` : f.key;
  return `
    <div class="setting-row" data-row="${f.key}">
      <div class="setting-text">
        <span class="setting-label" id="l_${f.key}">${esc(label)}${saved ? ' <span class="badge info">Ändrad i admin</span>' : ""}</span>
        ${f.help ? `<p class="muted">${esc(f.help)}</p>` : ""}
        ${saved ? `<p class="setting-meta">Standard från .env: ${esc(envText)} ·
          <button type="button" class="linklike" data-reset="${resetKeys}">Återställ</button></p>` : ""}
      </div>
      <div class="setting-control">${control}</div>
    </div>`;
}

function renderSettings(pending = {}) {
  const i = data.info;
  $("groups").innerHTML = data.groups.map((g) => {
    const [icon, desc] = GROUP_META[g.key] || ["", ""];
    const fields = data.fields.filter((f) => f.group === g.key && f.key !== "nightly_minute")
      .sort((a, b) => ORDER.indexOf(a.key) - ORDER.indexOf(b.key));
    const note = g.key === "cameras"
      ? `<p class="card-note">${i.cameras_enabled} aktiva kameror, varav ${i.cameras_enabled_hd} sparar högupplöst ·
          ${i.cameras_with_hd} av ${i.cameras_checked} kontrollerade kameror har högupplösta bilder.</p>` : "";
    return `<section class="card settings-card" aria-labelledby="g_${g.key}">
      <header class="settings-head">
        <h2 id="g_${g.key}"><span aria-hidden="true">${icon}</span> ${esc(g.label)}</h2>
        <p class="muted">${esc(desc)}</p>${note}
      </header>
      ${fields.map((f) => (SUBHEADS[f.key] ? `<h3 class="subhead">${SUBHEADS[f.key]}</h3>` : "") + rowFor(f)).join("")}
    </section>`;
  }).join("");
  for (const [key, v] of Object.entries(pending)) setInput(field(key), v);
  updateDirty();
}

function readValue(f) {
  if (isSwitch(f)) return $(`f_${f.key}`).checked;
  const raw = isSegment(f)
    ? document.querySelector(`input[name="f_${f.key}"]:checked`)?.value
    : $(`f_${f.key}`).value;
  if (f.kind === "int" || f.kind === "float") return raw === "" ? NaN : Number(raw);
  if (f.kind === "bool") return raw === "true";
  return raw;
}

function setInput(f, v) {
  if (!f) return;
  if (isSwitch(f)) {
    $(`f_${f.key}`).checked = v === true;
  } else if (isSegment(f)) {
    const r = document.querySelector(`input[name="f_${f.key}"][value="${CSS.escape(String(v))}"]`);
    if (r) r.checked = true;
  } else {
    $(`f_${f.key}`).value = Number.isNaN(v) ? "" : v;
  }
}

const dirtyFields = () => (data ? data.fields.filter((f) => readValue(f) !== f.value) : []);
const pendingValues = (except = []) => Object.fromEntries(dirtyFields()
  .filter((f) => !except.includes(f.key)).map((f) => [f.key, readValue(f)]));

function updateDirty() {
  const dirty = new Set(dirtyFields().map((f) => rowKey(f.key)));
  document.querySelectorAll("[data-row]").forEach((row) => {
    row.classList.toggle("dirty", dirty.has(row.dataset.row));
    row.classList.toggle("invalid", [...row.querySelectorAll("input[type=number]")].some((el) => !el.checkValidity()));
  });
  document.querySelectorAll(".switch-text").forEach((t) => {
    t.textContent = t.parentElement.querySelector("input").checked ? "På" : "Av";
  });
  const n = dirtyFields().length;
  $("savebar").hidden = !n;
  $("dirtyCount").textContent = plural(n, "osparad ändring", "osparade ändringar");
}

$("groups").addEventListener("input", updateDirty);
$("groups").addEventListener("change", updateDirty);

async function save(body, pending = {}) {
  $("save").disabled = true;
  try {
    data = await api("api/admin/settings", { method: "PUT", body });
    renderSettings(pending);
    const n = data.changed.length;
    toast((n ? `Sparat: ${plural(n, "inställning", "inställningar")} ändrade. ` : "Inget ändrades. ") +
      data.applied.join(" "), "ok");
    loadStatus();
    return true;
  } catch (e) {
    toast(`Kunde inte spara: ${e.message}`, "error");
    return false;
  } finally {
    $("save").disabled = false;
  }
}

const saveDirty = () => save({ values: pendingValues() });

$("settings").addEventListener("submit", (ev) => { ev.preventDefault(); saveDirty(); });
$("undo").addEventListener("click", () => renderSettings());
$("groups").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-reset]");
  if (!b) return;
  const keys = b.dataset.reset.split(",");
  save({ reset: keys }, pendingValues(keys)); // keep the other unsaved changes
});
window.addEventListener("beforeunload", (ev) => {
  if (dirtyFields().length) { ev.preventDefault(); ev.returnValue = ""; }
});

/* ---- Login ---- */
async function showApp() {
  data = await api("api/admin/settings");
  $("gate").hidden = true;
  $("shell").hidden = false;
  renderSettings();
  showView(viewFromHash());
  setInterval(() => { if (currentView === "oversikt" && !document.hidden) renderOverview(); }, 60000);
}

$("login").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("loginError").textContent = "";
  try {
    await api("api/admin/login", { method: "POST", body: { password: $("password").value } });
    $("password").value = "";
    await showApp();
  } catch (e) {
    $("loginError").textContent = e.message;
  }
});

$("logout").addEventListener("click", async () => {
  if (dirtyFields().length && !await confirmDialog({
    title: "Logga ut?", body: "Du har osparade ändringar i inställningarna. De försvinner om du loggar ut.",
    confirmText: "Logga ut",
  })) return;
  data = null; // no beforeunload warning
  await api("api/admin/logout", { method: "POST" });
  location.reload();
});

(async () => {
  loadStatus();
  const s = await api("api/admin/session");
  if (!s.enabled) { $("disabled").hidden = false; return; }
  if (s.logged_in) await showApp();
  else { $("login").hidden = false; $("password").focus(); }
})();
