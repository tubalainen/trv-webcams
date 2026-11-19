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
  render();
}

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
