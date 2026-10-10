// Settings → eki self: routing.json's "self" block — autonomy (queue.MODES) and how many items
// run at once. Registers the "self" section with settings.js.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const MODES = ["propose", "apply"];                                            // queue.MODES
  const block = (d) => (d.self && typeof d.self === "object" ? d.self : {});

  function form(d) {
    const me = block(d), { field } = settingsForm;
    const opt = (v, cur, label) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label || v)}</option>`;
    return `<section class="set-sect"><h3>eki self</h3><div class="set-grid" data-self>
        ${field("Autonomy", `<select class="set-in" data-f="autonomy">${MODES.map((m) => opt(m, me.autonomy || "propose")).join("")}</select>`)}
        ${field("Parallel", `<input class="set-in" type="number" min="1" data-f="parallel" value="${esc(me.parallel == null ? "" : me.parallel)}" placeholder="3">`)}
      </div></section>`;
  }

  function edit(el, d) {
    const f = el.dataset.f;
    if (f !== "autonomy" && f !== "parallel") return false;
    if (!d.self || typeof d.self !== "object") d.self = {};
    if (f === "autonomy") d.self.autonomy = el.value;
    else if (el.value === "") delete d.self.parallel; else d.self.parallel = +el.value;
    return false;
  }

  const self = { title: "eki self", file: "routing", html: form, edit };
  if (window.settings) settings.section("self", self);
  else (window.settingsSections = window.settingsSections || []).push(["self", self]);
})();
