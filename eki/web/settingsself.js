// Settings → eki self: routing.json's "self" block — autonomy (queue.MODES). How many of eki's
// items build at once is `parallel` on eki's own workflow card; the machine-wide caps are
// Settings → Machine. Registers the "self" section with settings.js.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const MODES = ["propose", "apply"];                                            // queue.MODES
  const block = (d) => (d.self && typeof d.self === "object" ? d.self : {});

  function form(d) {
    const me = block(d), group = settingsForm.group, line = settingsForm.line;
    const opt = (v, cur, label) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label || v)}</option>`;
    return `<div data-self>${group("eki self",
        line("Autonomy", `<select class="set-in" data-f="autonomy">${MODES.map((m) => opt(m, me.autonomy || "propose")).join("")}</select>`,
          "propose waits for you; apply queues fit items by itself"))}</div>`;
  }

  function edit(el, d) {
    const f = el.dataset.f;
    if (f !== "autonomy") return false;
    if (!d.self || typeof d.self !== "object") d.self = {};
    d.self.autonomy = el.value;
    return false;
  }

  const self = { title: "eki self", file: "routing", html: form, edit };
  // The sidebar link #settings is window.settings until settings.js loads.
  if (window.settings && typeof window.settings.section === "function") settings.section("self", self);
  else (window.settingsSections = window.settingsSections || []).push(["self", self]);
})();
