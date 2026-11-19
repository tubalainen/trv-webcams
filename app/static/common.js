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
