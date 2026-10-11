// Settings → eki self: routing.json's "self" block — autonomy (queue.MODES) and how many items
// run at once. Registers the "self" section with settings.js.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const MODES = ["propose", "apply"];                                            // queue.MODES
  const block = (d) => (d.self && typeof d.self === "object" ? d.self : {});

  function form(d) {
    const me = block(d), group = settingsForm.group, line = settingsForm.line;
    const opt = (v, cur, label) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label || v)}</option>`;
    return `<div data-self>${group("eki self",
        line("Autonomy", `<select class="set-in" data-f="autonomy">${MODES.map((m) => opt(m, me.autonomy || "propose")).join("")}</select>`,
          "propose waits for you; apply queues fit items by itself") +
        line("Agents at once", `<input class="set-in" type="number" min="1" max="64" data-f="agents" value="${esc(mach(d).agents == null ? "" : mach(d).agents)}" placeholder="12">`,
          "how many agents may build at the same time across all projects (each project's workflow card can cap its own)") +
        line("Test runs at once", `<input class="set-in" type="number" min="1" max="32" data-f="checks" value="${esc(mach(d).checks == null ? "" : mach(d).checks)}" placeholder="3">`,
          "how many checks / test runs may run at the same time; this is what loads the Mac"))}</div>`;
  }

  // the machine caps live in routing.json's machine block (eki/machinecaps.py), read by the scheduler
  function mach(d) { return d.machine && typeof d.machine === "object" ? d.machine : {}; }

  function edit(el, d) {
    const f = el.dataset.f;
    if (f !== "autonomy" && f !== "agents" && f !== "checks") return false;
    if (f === "autonomy") {
      if (!d.self || typeof d.self !== "object") d.self = {};
      d.self.autonomy = el.value;
      return false;
    }
    if (!d.machine || typeof d.machine !== "object") d.machine = {};
    const n = Math.max(1, Math.min(f === "agents" ? 64 : 32, Math.round(+el.value)));
    if (el.value === "" || !isFinite(n)) delete d.machine[f]; else d.machine[f] = n;
    return false;
  }

  const self = { title: "eki self", file: "routing", html: form, edit };
  // The sidebar link #settings is window.settings until settings.js loads.
  if (window.settings && typeof window.settings.section === "function") settings.section("self", self);
  else (window.settingsSections = window.settingsSections || []).push(["self", self]);
})();
