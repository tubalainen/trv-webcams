/* Generate dialog shared by the camera page (one camera) and the overview (county / all of
 * Sweden): quick picks, a month calendar with the days that have images, start/end time,
 * formats (MP4/GIF), clip length and the temperature graph. */

const pad2 = (n) => String(n).padStart(2, "0");
const isoDate = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const isoTime = (d) => `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
const MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti",
  "september", "oktober", "november", "december"];
const WEEKDAYS = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"];

let genOptions = null;
async function loadGenOptions() {
  if (!genOptions) genOptions = await api("api/generate/options");
  return genOptions;
}

/**
 * @param {HTMLElement} root
 * @param {{availabilityUrl: () => string, submit: (body: object) => Promise<string>,
 *          scopeText?: () => string, temperatureDefault?: () => string}} opts
 *   submit() sends the request and returns a short Swedish status text.
 */
async function createGenerator(root, opts) {
  const o = await loadGenOptions();
  const today = new Date();
  const st = {
    month: new Date(today.getFullYear(), today.getMonth(), 1),
    start: null, end: null, endNow: true,
    snap: new Set(), arch: new Set(),
  };
  const retentionDay = new Date(today);
  retentionDay.setDate(retentionDay.getDate() - o.max_days);
  // Earliest selectable day: the first day with saved images (never before the retention)
  let minDay = new Date(today);
  let firstKey = null; // YYYY-MM-DD of the oldest image, null when there are none

  function updateLimits() {
    const all = [...st.snap, ...st.arch].sort();
    firstKey = all.length ? all[0] : null;
    const first = firstKey ? new Date(`${firstKey}T00:00`) : new Date(today);
    minDay = first > retentionDay ? first : retentionDay;
    minDay.setHours(0, 0, 0, 0);
    // Drop a selection that starts before the oldest image
    if (st.start && (!firstKey || st.start < isoDate(minDay))) { st.start = null; st.end = null; }
    if (st.month < new Date(minDay.getFullYear(), minDay.getMonth(), 1)) {
      st.month = new Date(minDay.getFullYear(), minDay.getMonth(), 1);
    }
    for (const b of root.querySelectorAll("[data-preset]")) {
      const start = new Date();
      start.setDate(start.getDate() - Number(b.dataset.preset));
      const tooEarly = !firstKey || isoDate(start) < firstKey;
      b.disabled = tooEarly;
      b.title = !firstKey ? "Det finns inga sparade bilder ännu"
        : tooEarly ? `Bilder finns från ${firstKey}` : "";
    }
  }

  root.classList.add("generator");
  root.innerHTML = `
    <div class="gen-presets" role="group" aria-label="Förval">
      ${o.presets.map((p) => `<button type="button" class="btn small ghost" data-preset="${p.days}">${esc(p.label)}</button>`).join("")}
    </div>
    <div class="cal" aria-label="Välj tidsspann i kalendern">
      <div class="cal-head">
        <button type="button" class="btn small ghost" data-cal="-1" aria-label="Föregående månad">‹</button>
        <span class="cal-title"></span>
        <button type="button" class="btn small ghost" data-cal="1" aria-label="Nästa månad">›</button>
      </div>
      <div class="cal-grid"></div>
      <div class="cal-legend muted">
        <span><i class="cal-dot snap"></i> alla bilder</span>
        <span><i class="cal-dot arch"></i> en bild per timme</span>
      </div>
    </div>
    <div class="gen-times">
      <label class="field"><span>Från</span>
        <span class="gen-dt"><b class="gen-start-day">–</b><input type="time" class="gen-start-time" value="00:00"></span></label>
      <label class="field"><span>Till</span>
        <span class="gen-dt"><b class="gen-end-day">–</b><input type="time" class="gen-end-time" value="23:59">
        <label class="check"><input type="checkbox" class="gen-now" checked> nu</label></span></label>
    </div>
    <div class="gen-opts">
      <fieldset class="gen-formats"><legend>Format</legend>
        <label class="check"><input type="checkbox" value="mp4" checked> MP4</label>
        <label class="check"><input type="checkbox" value="gif" checked> GIF</label>
      </fieldset>
      <label class="field"><span>Längd</span>
        <select class="gen-duration">${o.durations.map((d) =>
          `<option value="${d}"${d === o.default_duration ? " selected" : ""}>${d} s</option>`).join("")}</select></label>
      <label class="field"><span>Temperaturgraf</span>
        <select class="gen-temp">
          <option value="default">Standard${opts.temperatureDefault ? ` (${esc(opts.temperatureDefault())})` : ""}</option>
          <option value="on">På</option><option value="off">Av</option>
        </select></label>
    </div>
    <p class="muted gen-summary" role="status"></p>
    <button type="button" class="btn gen-go">Generera</button>
    <div class="gen-result" role="status"></div>`;

  const q = (sel) => root.querySelector(sel);

  function rangeDates() {
    if (!st.start) return null;
    const endDay = st.end || st.start;
    const start = new Date(`${st.start}T${q(".gen-start-time").value || "00:00"}`);
    const end = st.endNow ? new Date() : new Date(`${endDay}T${q(".gen-end-time").value || "23:59"}:59`);
    return { start, end };
  }

  function daysWithImages(from, to) {
    let n = 0, archOnly = 0;
    for (const set of [st.snap, st.arch]) {
      for (const d of set) if (d >= from && d <= to) { n++; if (set === st.arch) archOnly++; }
    }
    return { n, archOnly };
  }

  function renderSummary() {
    const r = rangeDates();
    const fmts = [...root.querySelectorAll(".gen-formats input:checked")].map((i) => i.value.toUpperCase());
    let text;
    if (!firstKey) {
      text = "Det finns inga sparade bilder ännu. Aktivera kameran och vänta tills bilder har hämtats.";
    } else if (!r) {
      text = `Välj ett förval eller klicka på en start- och slutdag i kalendern. Bilder finns från ${firstKey}.`;
    } else {
      const { n, archOnly } = daysWithImages(isoDate(r.start), isoDate(r.end));
      const dur = q(".gen-duration").value;
      text = `${fmtTime(r.start.toISOString())} – ${st.endNow ? "nu" : fmtTime(r.end.toISOString())}` +
        ` · ${n} dagar med bilder${archOnly ? ` (varav ${archOnly} med en bild per timme)` : ""}` +
        ` · ${fmts.join(" + ") || "inget format"}, ${dur} s` + (opts.scopeText ? ` · ${opts.scopeText()}` : "");
      if (!n) text += " – det finns inga bilder i tidsspannet.";
    }
    q(".gen-summary").textContent = text;
    q(".gen-go").disabled = !firstKey || !r || !fmts.length;
    q(".gen-start-day").textContent = st.start || "–";
    q(".gen-end-day").textContent = st.endNow ? "nu" : (st.end || st.start || "–");
    q(".gen-end-time").disabled = st.endNow;
  }

  function renderCalendar() {
    const m = st.month;
    q(".cal-title").textContent = `${MONTHS[m.getMonth()]} ${m.getFullYear()}`;
    const first = (m.getDay() + 6) % 7; // Monday first
    const days = new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate();
    const endSel = st.endNow ? isoDate(today) : (st.end || st.start);
    let html = WEEKDAYS.map((w) => `<span class="cal-wd">${w}</span>`).join("");
    for (let i = 0; i < first; i++) html += "<span></span>";
    for (let d = 1; d <= days; d++) {
      const date = new Date(m.getFullYear(), m.getMonth(), d);
      const key = isoDate(date);
      const cls = ["cal-day"];
      if (st.snap.has(key)) cls.push("snap"); else if (st.arch.has(key)) cls.push("arch");
      if (st.start && key >= st.start && key <= endSel) cls.push("in-range");
      if (key === st.start) cls.push("start");
      if (key === endSel) cls.push("end");
      if (key === isoDate(today)) cls.push("today");
      const off = !firstKey || date > today || date < minDay;
      html += `<button type="button" class="${cls.join(" ")}" data-day="${key}"${off ? " disabled" : ""}
        aria-pressed="${cls.includes("in-range")}">${d}</button>`;
    }
    q(".cal-grid").innerHTML = html;
    const next = new Date(m.getFullYear(), m.getMonth() + 1, 1);
    root.querySelector('[data-cal="1"]').disabled = next > today;
    root.querySelector('[data-cal="-1"]').disabled = m <= new Date(minDay.getFullYear(), minDay.getMonth(), 1);
    renderSummary();
  }

  root.addEventListener("click", (ev) => {
    const preset = ev.target.closest("[data-preset]");
    if (preset) {
      const start = new Date();
      start.setDate(start.getDate() - Number(preset.dataset.preset));
      st.start = isoDate(start);
      st.end = isoDate(today);
      st.endNow = true;
      q(".gen-start-time").value = isoTime(start);
      q(".gen-now").checked = true;
      st.month = new Date(today.getFullYear(), today.getMonth(), 1);
      return renderCalendar();
    }
    const nav = ev.target.closest("[data-cal]");
    if (nav) {
      st.month = new Date(st.month.getFullYear(), st.month.getMonth() + Number(nav.dataset.cal), 1);
      return renderCalendar();
    }
    const day = ev.target.closest("[data-day]");
    if (day) {
      const key = day.dataset.day;
      if (!st.start || st.end || key < st.start) {
        st.start = key;
        st.end = null;
        q(".gen-start-time").value = "00:00";
      } else {
        st.end = key;
      }
      // An end day in the past cannot mean "now"
      const endKey = st.end || st.start;
      st.endNow = endKey === isoDate(today) && q(".gen-now").checked;
      if (endKey !== isoDate(today)) q(".gen-now").checked = false;
      return renderCalendar();
    }
  });
  q(".gen-now").addEventListener("change", (ev) => {
    st.endNow = ev.target.checked;
    if (st.endNow) st.end = isoDate(today);
    renderCalendar();
  });
  root.addEventListener("input", (ev) => { if (!ev.target.closest(".cal")) renderSummary(); });

  q(".gen-go").addEventListener("click", async () => {
    const r = rangeDates();
    if (!r) return;
    if (r.end <= r.start) { q(".gen-result").textContent = "Slutet måste ligga efter starten."; return; }
    const body = {
      start: r.start.toISOString(),
      end: r.end.toISOString(),
      formats: [...root.querySelectorAll(".gen-formats input:checked")].map((i) => i.value),
      duration_s: Number(q(".gen-duration").value),
      temp_overlay: q(".gen-temp").value,
    };
    q(".gen-go").disabled = true;
    q(".gen-result").textContent = "Startar…";
    try {
      q(".gen-result").textContent = await opts.submit(body);
    } catch (e) {
      q(".gen-result").textContent = `Kunde inte starta: ${e.message}`;
    } finally {
      renderSummary();
    }
  });

  async function reloadAvailability() {
    try {
      const a = await api(opts.availabilityUrl());
      st.snap = new Set(a.snapshots);
      st.arch = new Set(a.archive_only);
    } catch (_) { st.snap = new Set(); st.arch = new Set(); }
    updateLimits();
    renderCalendar();
  }

  await reloadAvailability();
  return { reloadAvailability, render: renderCalendar };
}
