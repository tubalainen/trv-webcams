/* Shared helpers */
const api = async (path, opts = {}) => {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
};

const fmtBytes = (b) => {
  if (b == null) return "–";
  const u = ["B", "kB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i ? 1 : 0)} ${u[i]}`;
};

const fmtTime = (iso) => {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("sv-SE", { dateStyle: "short", timeStyle: "short" });
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function showVersion(version) {
  const brand = document.querySelector(".topbar .brand");
  if (!brand || !version) return;
  let v = document.getElementById("appVersion");
  if (!v) {
    v = document.createElement("span");
    v.id = "appVersion";
    v.className = "app-version";
    brand.insertAdjacentElement("afterend", v);
  }
  v.textContent = `v${version}`;
  v.title = `trv-webcams ${version}`;
}

async function loadStatus() {
  const el = document.getElementById("status");
  if (!el) return;
  try {
    const s = await api("api/status");
    showVersion(s.version);
    const enc = s.encoder ? s.encoder.codec : "detekterar…";
    const warn = s.api_key_configured ? "" : ' <b class="warn">TRV_API_KEY saknas!</b>';
    el.innerHTML =
      `<span title="Aktiva / totalt">🎥 ${s.cameras_enabled} / ${s.cameras_total} aktiva</span>` +
      `<span title="Bilder + timelapses">💾 ${fmtBytes(s.usage.snapshots_bytes + s.usage.timelapses_bytes)}` +
      ` (≈${fmtBytes(s.estimated_bytes_at_retention)} vid full retention, ${fmtBytes(s.disk_free_bytes)} ledigt)</span>` +
      `<span title="${esc(s.gpus && s.gpus.length ? `GPU: ${s.gpus.join(", ")}` : "Videokodare")}">⚙️ ${esc(enc)}` +
      `${s.encoder ? (s.encoder.mode === "cpu" ? " (CPU)" : " (GPU)") : ""}</span>` +
      (s.jobs_pending ? `<span>⏳ ${s.jobs_pending} jobb i kö</span>` : "") + warn;
  } catch (e) {
    el.textContent = "Status ej tillgänglig";
  }
}

/* Toast notifications: toast("Sparat", "ok" | "error" | "info"). */
function toast(msg, kind = "info", ms = 5000) {
  let box = document.getElementById("toasts");
  if (!box) {
    box = document.createElement("div");
    box.id = "toasts";
    box.className = "toasts";
    box.setAttribute("role", "status");
    box.setAttribute("aria-live", "polite");
    document.body.appendChild(box);
  }
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.innerHTML = `<span class="toast-ico" aria-hidden="true">${{ ok: "✓", error: "!", info: "i" }[kind] || "i"}</span>` +
    `<span class="toast-msg"></span><button type="button" class="toast-x" aria-label="Stäng">×</button>`;
  t.querySelector(".toast-msg").textContent = msg;
  const close = () => { t.classList.add("out"); setTimeout(() => t.remove(), 200); };
  t.querySelector(".toast-x").addEventListener("click", close);
  box.appendChild(t);
  if (ms) setTimeout(close, kind === "error" ? ms * 2 : ms);
  return t;
}

/* Modal dialog in the app's style. Resolves with the value of the chosen button, or null
 * when cancelled (Esc, backdrop or the cancel button).
 * buttons: [{label, value, kind: "primary" | "danger" | "ghost"}] */
function appDialog({ title, body = "", buttons }) {
  return new Promise((resolve) => {
    const d = document.createElement("dialog");
    d.className = "dialog";
    const paras = String(body).split(/\n\n+/).filter(Boolean).map((p) => `<p>${esc(p)}</p>`).join("");
    d.innerHTML = `<h2>${esc(title)}</h2><div class="dialog-body">${paras}</div><div class="dialog-actions">` +
      buttons.map((b, i) => `<button type="button" class="btn ${b.kind === "danger" ? "danger" :
        b.kind === "ghost" ? "ghost" : ""}" data-i="${i}">${esc(b.label)}</button>`).join("") + "</div>";
    let result = null;
    d.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-i]");
      if (b) { result = buttons[Number(b.dataset.i)].value; d.close(); }
      else if (ev.target === d) d.close(); // backdrop
    });
    d.addEventListener("close", () => { d.remove(); resolve(result); });
    document.body.appendChild(d);
    d.showModal();
    // Focus the safe choice first
    (d.querySelector(".btn.ghost") || d.querySelector(".btn")).focus();
  });
}

const confirmDialog = ({ title, body, confirmText = "OK", danger = false }) =>
  appDialog({ title, body, buttons: [
    { label: "Avbryt", value: false, kind: "ghost" },
    { label: confirmText, value: true, kind: danger ? "danger" : "primary" },
  ] }).then((v) => v === true);
