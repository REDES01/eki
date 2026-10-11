// Settings → Machine: routing.json's "machine" block (eki/machinecaps.py) — how many agents may
// work at once across all projects, and how many test runs. One fold; open, it shows both caps
// and the live line from /api/machine (eki/machinenow.py), read again every few seconds while open.
// A field left empty is dropped, and an empty block drops the machine key.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const AGENTS = 12, MOST = 32;                                                  // machinecaps.AGENTS, MOST
  const EVERY = 4000;
  const block = (d) => (d.machine && typeof d.machine === "object" ? d.machine : {});
  let open = false, got = null, timer = null;

  const agents = (d) => (block(d).agents != null ? block(d).agents : got ? got.agents : AGENTS);
  const checks = (d) => (block(d).checks != null ? block(d).checks : got ? got.checks : 2);
  const plural = (n, w) => `${n} ${w}${+n === 1 ? "" : "s"}`;
  const summary = (d) => `${plural(agents(d), "agent")} · ${plural(checks(d), "test run")} at once`;

  function num(f, v, ph) {
    return `<input class="set-in" type="number" min="1" max="${MOST}" data-f="${f}" value="${esc(v == null ? "" : v)}" placeholder="${esc(ph)}">`;
  }

  function form(d) {
    const m = block(d), line = settingsForm.line;
    const body = settingsForm.lines("",
      line("Agents at once", num("agents", m.agents, got ? got.agents : AGENTS),
        "how many agents may work at the same time across all projects; each mostly waits on the model, so this mostly costs subscription quota, not CPU") +
      line("Test runs at once", num("checks", m.checks, got ? got.checks : ""),
        "how many test runs — eki's checks and the agents' own tests — may run at the same time; each takes several cores, so keep this low enough that load stays near the core count") +
      `<div class="set-sub" data-machine-now>${esc(got ? got.line : "now: reading…")}</div>`);
    setTimeout(live, 0);
    return `<div data-machine>${settingsForm.fold("machine", `<span data-machine-sum>${esc(summary(d))}</span>`, body, open)}</div>`;
  }

  // the live line, read now and again every few seconds while the fold is open and on the page
  async function live() {
    clearTimeout(timer);
    timer = null;
    const el = document.querySelector("[data-machine-now]");
    if (!el && got) return;
    try {
      const r = await fetch("/api/machine");
      if (r.ok) got = await r.json();
    } catch (e) { /* the next tick tries again */ }
    const now = document.querySelector("[data-machine-now]");
    if (now && got) now.textContent = got.line;
    const sum = document.querySelector("[data-machine-sum]");
    if (sum && window.settings && settings.draft) sum.textContent = summary(settings.draft("routing") || {});
    if (now && open) timer = setTimeout(live, EVERY);
  }

  function act(a) {
    if (a !== "fold") return false;
    open = !open;
    return true;
  }

  // one input changed: put it in d.machine, or take it out when empty
  function edit(el, d) {
    const f = el.dataset.f;
    if (f !== "agents" && f !== "checks") return false;
    const m = block(d), v = el.value.trim();
    if (v === "") delete m[f]; else m[f] = +v;
    if (Object.keys(m).length) d.machine = m; else delete d.machine;
    const sum = document.querySelector("[data-machine-sum]");
    if (sum) sum.textContent = summary(d);
    return false;
  }

  window.settingsMachine = { form, edit, summary };
  const machine = { title: "Machine", file: "routing", html: (d) => form(d), edit: (el, d) => edit(el, d), act };
  // The sidebar link #settings is window.settings until settings.js loads.
  if (window.settings && typeof window.settings.section === "function") settings.section("machine", machine);
  else (window.settingsSections = window.settingsSections || []).push(["machine", machine]);
})();
