// Scheduled: the "New scheduled task" sheet. The prompt, a project (from /api/projects, or none),
// when it fires (hourly at :MM, daily, weekdays, weekly on a day — in the zone eki fixed for the
// station, shown quiet) and who answers (settingstarget.js's picker over /api/models, Auto first).
// It POSTs /api/schedule {text, project?, cadence, target?} (eki/api_schedule.py) and shows the
// server's error inline. cadence(fields) is pure: the schedule.py cadence JSON from the form's fields.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const DAY_NAMES = { mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday" };
  const EVERY = [["hourly", "Hourly"], ["daily", "Daily"], ["weekdays", "Weekdays"], ["weekly", "Weekly"]];
  const F = { dlg: null, models: null, target: "", done: null };

  // {every, at, minute, day} → {"every": "hourly", "minute": M} | {"every": "daily"|"weekdays", "at": "HH:MM"}
  // | {"every": "weekly", "day": "mon", "at": "HH:MM"}
  function cadence(f) {
    const every = String(f.every || "daily");
    if (every === "hourly") {
      const m = Math.max(0, Math.min(59, parseInt(f.minute, 10) || 0));
      return { every, minute: m };
    }
    const hm = /^(\d{1,2}):(\d{2})/.exec(String(f.at || "09:00").trim()) || [0, "9", "00"];
    const at = `${String(Math.min(23, +hm[1])).padStart(2, "0")}:${String(Math.min(59, +hm[2])).padStart(2, "0")}`;
    if (every === "weekly") return { every, day: DAYS.includes(f.day) ? f.day : "mon", at };
    if (every === "daily" || every === "weekdays") return { every, at };
    throw new Error("unknown cadence: " + every);
  }

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify(body) });
    const got = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(got.error || r.statusText);
    return got;
  }

  const q = (sel) => F.dlg.querySelector(sel);

  function sheet() {
    if (F.dlg) return F.dlg;
    const d = document.createElement("dialog");
    d.className = "sheet";
    d.id = "sched-sheet";
    d.innerHTML = `<form method="dialog" id="sf-form" autocomplete="off">
      <h2>New scheduled task</h2>
      <label for="sf-text">Prompt</label>
      <textarea id="sf-text" rows="4" placeholder="What should run each time?" required></textarea>
      <label for="sf-project">Project</label>
      <select id="sf-project"><option value="">No project</option></select>
      <label for="sf-every">When</label>
      <div class="sf-when" style="display:flex;gap:8px;align-items:center">
        <select id="sf-every" style="width:auto">${EVERY.map(([v, l]) => `<option value="${v}"${v === "daily" ? " selected" : ""}>${l}</option>`).join("")}</select>
        <select id="sf-day" style="width:auto" hidden>${DAYS.map((x) => `<option value="${x}">${DAY_NAMES[x]}</option>`).join("")}</select>
        <span id="sf-minute-wrap" hidden style="display:inline-flex;align-items:center;gap:4px">at&nbsp;:<input id="sf-minute" type="number" min="0" max="59" value="0" style="width:5em"></span>
        <input id="sf-at" type="time" value="09:00" style="width:auto">
        <span id="sf-tz" class="quiet"></span>
      </div>
      <label>Model</label>
      <div id="sf-target"></div>
      <div id="sf-err" class="error"></div>
      <div class="row-end"><button type="button" class="btn" data-sf="cancel">Cancel</button>
        <button type="submit" class="btn-primary">Schedule</button></div>
    </form>`;
    document.body.appendChild(d);
    F.dlg = d;
    q("#sf-every").addEventListener("change", when);
    q("#sf-form").addEventListener("submit", submit);
    q("[data-sf=cancel]").addEventListener("click", () => d.close());
    q("#sf-target").addEventListener("change", (e) => {
      const w = e.target.closest("[data-tgt]");
      if (!w) return;
      F.target = settingsTarget.value(w, e.target);
      picker();
    });
    return d;
  }

  // the minute box for hourly, the day for weekly, the time for the rest
  function when() {
    const every = q("#sf-every").value;
    q("#sf-minute-wrap").hidden = every !== "hourly";
    q("#sf-minute-wrap").style.display = every === "hourly" ? "inline-flex" : "none";
    q("#sf-at").hidden = every === "hourly";
    q("#sf-day").hidden = every !== "weekly";
  }

  function picker() {
    const models = F.models || {};
    q("#sf-target").innerHTML = settingsTarget.html(F.target, { names: Object.keys(models), models, extra: [["", "Auto"]] });
  }

  async function fill() {
    try {
      const got = await api("/api/projects");
      q("#sf-project").innerHTML = '<option value="">No project</option>' + (got.projects || [])
        .filter((p) => p.folder).map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
    } catch (e) { /* No project still works */ }
    try { F.models = (await api("/api/models")).providers || {}; } catch (e) { F.models = {}; }
    picker();
  }

  function fields() {
    return { every: q("#sf-every").value, at: q("#sf-at").value, minute: q("#sf-minute").value, day: q("#sf-day").value };
  }

  async function submit(e) {
    e.preventDefault();
    const text = q("#sf-text").value.trim();
    if (!text) { q("#sf-err").textContent = "Write the prompt first."; return; }
    const body = { text, cadence: cadence(fields()) };
    const project = q("#sf-project").value;
    if (project) body.project = project;
    body.target = F.target || null;
    const b = q("button[type=submit]");
    b.disabled = true;
    try {
      const got = await api("/api/schedule", body);
      F.dlg.close();
      if (F.done) F.done(got);
    } catch (x) { q("#sf-err").textContent = x.message; }
    b.disabled = false;
  }

  // o.tz: the zone to show; o.done(got): called with {id} once it's saved
  function open(o) {
    o = o || {};
    sheet();
    F.done = o.done || null;
    F.target = "";
    q("#sf-text").value = "";
    q("#sf-err").textContent = "";
    q("#sf-tz").textContent = o.tz || "";
    when();
    picker();
    fill();
    F.dlg.showModal();
    q("#sf-text").focus();
  }

  window.scheduleForm = { open, cadence, DAYS, DAY_NAMES };
})();
