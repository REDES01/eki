// Settings: the "notify" block of routing.json — pushes to the phone through ntfy (eki/notify.py).
// It registers the "notifications" section with settings.js: form(d) is its form, edit() its edits.
// A field left at its default is dropped, and a block left all default drops the notify key.
// The topic is a secret: it is shown in a password field, never in plain view.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const KINDS = ["goal_done", "needs_you", "heads_up"];                                      // notify.KINDS
  const LABELS = { goal_done: "a goal finished", needs_you: "something needs you", heads_up: "a long wait, for your information" };
  const SERVER = "https://ntfy.sh";                                              // notify.DEFAULTS
  const block = (d) => (d.notify && typeof d.notify === "object" ? d.notify : {});

  // a group of lines: where to send, the secret topic, then one switch per event
  function form(d) {
    const n = block(d), group = settingsForm.group, line = settingsForm.line;
    const events = Array.isArray(n.events) ? n.events : KINDS;
    const sw = (k) => line(esc(LABELS[k]), `<label class="set-check"><input type="checkbox" data-event="${k}"${events.includes(k) ? " checked" : ""} aria-label="${esc(LABELS[k])}"></label>`, `<code>${k}</code>`);
    return `<div data-notify>${group("Notifications",
        line("Server", `<input class="set-in" data-f="server" value="${esc(n.server)}" placeholder="${SERVER}" spellcheck="false">`, "the ntfy server") +
        line("Topic", `<input class="set-in" type="password" data-f="topic" value="${esc(n.topic)}" placeholder="off" autocomplete="off" spellcheck="false">`, "empty is off; a secret. Subscribe to it in the ntfy app on the iPhone (the Watch mirrors it); <code>eki notify test</code> sends one"))}
      ${group("Send when", KINDS.map(sw).join(""))}
    </div>`;
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
  const notifications = { title: "Notifications", file: "routing", html: (d) => form(d), edit: (el, d) => edit(el, d) };
  // The sidebar link #settings is window.settings until settings.js loads.
  if (window.settings && typeof window.settings.section === "function") settings.section("notifications", notifications);
  else (window.settingsSections = window.settingsSections || []).push(["notifications", notifications]);
})();
