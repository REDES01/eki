// Settings: the "notify" block of routing.json — pushes to the phone through ntfy (eki/notify.py).
// settings.js puts form(d) into the routing form and hands edits inside [data-notify] to edit().
// A field left at its default is dropped, and a block left all default drops the notify key.
// The topic is a secret: it is shown in a password field, never in plain view.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const KINDS = ["goal_done", "needs_you"];                                      // notify.KINDS
  const LABELS = { goal_done: "a goal finished", needs_you: "something needs you" };
  const SERVER = "https://ntfy.sh";                                              // notify.DEFAULTS
  const block = (d) => (d.notify && typeof d.notify === "object" ? d.notify : {});

  function form(d) {
    const n = block(d);
    const events = Array.isArray(n.events) ? n.events : KINDS;
    const box = (k) => `<label class="set-check"><input type="checkbox" data-event="${k}"${events.includes(k) ? " checked" : ""}> ${esc(LABELS[k])}</label>`;
    const field = (label, html) => `<label class="set-field"><span>${label}</span>${html}</label>`;
    return `<section class="set-sect"><h3>Notifications</h3><div data-notify>
      <div class="set-grid">
        ${field("Server", `<input class="set-in" data-f="server" value="${esc(n.server)}" placeholder="${SERVER}" spellcheck="false">`)}
        ${field("Topic <i class=\"dim\">empty is off; a secret</i>", `<input class="set-in" type="password" data-f="topic" value="${esc(n.topic)}" placeholder="off" autocomplete="off" spellcheck="false">`)}
      </div>
      ${field("Send", `<div class="set-checks">${KINDS.map(box).join("")}</div>`)}
      <div class="dim">Subscribe to the topic in the ntfy app on the iPhone; the Watch mirrors it. <code>eki notify test</code> sends one.</div>
    </div></section>`;
  }

  // one input changed: put it in d.notify, or take it out when it is back to its default
  function edit(el, d) {
    const n = block(d);
    const f = el.dataset.f;
    if (el.dataset.event) {
      const on = new Set(Array.isArray(n.events) ? n.events : KINDS);
      el.checked ? on.add(el.dataset.event) : on.delete(el.dataset.event);
      n.events = KINDS.filter((k) => on.has(k)).concat([...on].filter((k) => !KINDS.includes(k)));
      if (JSON.stringify(n.events) === JSON.stringify(KINDS)) delete n.events;
    } else if (f === "server" || f === "topic") {
      const v = el.value.trim();
      if (v && !(f === "server" && v === SERVER)) n[f] = v; else delete n[f];
    }
    if (Object.keys(n).length) d.notify = n; else delete d.notify;
    return false;
  }

  window.settingsNotify = { form, edit };
})();
