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
const sumDays = (days, test) => days.filter((d) => test(d[0]))
  .reduce((a, d) => [a[0] + d[1], a[1] + d[2] + d[4]], [0, 0]);

async function loadStorage() {
  storageData = await api("api/admin/storage");
  const sel = $("purgeCamera");
  const cur = sel.value;
  sel.innerHTML = `<option value="">Alla kameror</option>` + storageData.cameras.map((c) =>
    `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  sel.value = storageData.cameras.some((c) => c.id === cur) ? cur : "";
  $("storageRows").innerHTML = storageData.cameras.length ? storageData.cameras.map((c) => {
    const [imgs, bytes] = sumDays(c.days, () => true);
    const tlBytes = c.timelapses.reduce((a, t) => a + t.bytes, 0);
    const clips = c.timelapses.length;
    const span = c.days.length ? `${c.days[0][0]} – ${c.days[c.days.length - 1][0]}` : "–";
    return `<tr>
      <td><a href="camera.html?id=${encodeURIComponent(c.id)}">${esc(c.name)}</a>
        ${c.enabled ? "" : '<span class="badge">inaktiv</span>'}</td>
      <td>${imgs}</td><td title="${esc(span)}">${c.days.length}</td>
      <td>${fmtBytes(bytes + tlBytes)}</td><td>${clips}</td>
      <td class="row-actions">
        <button type="button" class="btn small ghost" data-del="images" data-cam="${esc(c.id)}">Ta bort bilder</button>
        <button type="button" class="btn small ghost" data-del="timelapses" data-cam="${esc(c.id)}">Ta bort timelapses</button>
      </td></tr>`;
  }).join("") : `<tr><td colspan="6" class="muted">Inga sparade bilder eller timelapses.</td></tr>`;
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

$("storageRows").addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-del]");
  if (!b) return;
  const cam = storageData.cameras.find((c) => c.id === b.dataset.cam);
  const what = b.dataset.del === "images" ? "alla bilder" : "alla timelapses";
  if (!confirm(`Ta bort ${what} för ${cam.name}? Det går inte att ångra.`)) return;
  try {
    const id = encodeURIComponent(cam.id);
    const r = b.dataset.del === "images"
      ? await api(`api/admin/cameras/${id}/delete-images`, { method: "POST", body: { all: true } })
      : await api(`api/admin/cameras/${id}/timelapses`, { method: "DELETE" });
    alert(`Klart: ${r.files} filer borttagna, ${fmtBytes(r.bytes)} frigjort.`);
    await loadStorage();
    loadStatus();
  } catch (e) { alert(`Kunde inte ta bort: ${e.message}`); }
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
