// Settings → Machine: routing.json's "machine" block (eki/machinecaps.py) — how many agents may
// work at once across all projects, how many test runs, how many browsers, and the memory floor
// (eki/memadmit.py) below which new work waits. One fold; open, it shows the caps, the floor and the
// live line from /api/machine (eki/machinenow.py) with each kind's learned memory estimate, read
// again every few seconds while open.
// A field left empty is dropped, and an empty block drops the machine key.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const AGENTS = 12, BROWSERS = 3, FLOOR_GB = 6, SWAP_MAX = 60;  // machinecaps.AGENTS, BROWSERS, FLOOR_GB, SWAP_MAX
  const MOST = { agents: 64, checks: 32, browsers: 16, floor_gb: 64, swap_max: 100 };  // machinecaps.MOST
  const KEYS = Object.keys(MOST);
  const EVERY = 4000;
  const block = (d) => (d.machine && typeof d.machine === "object" ? d.machine : {});
  let open = false, got = null, timer = null;

  const agents = (d) => (block(d).agents != null ? block(d).agents : got ? got.agents : AGENTS);
  const checks = (d) => (block(d).checks != null ? block(d).checks : got ? got.checks : 2);
  const plural = (n, w) => `${n} ${w}${+n === 1 ? "" : "s"}`;
  const browsers = (d) => (block(d).browsers != null ? block(d).browsers : got ? got.browsers : BROWSERS);
  const summary = (d) =>
    `${plural(agents(d), "agent")} · ${plural(checks(d), "test run")} · ${plural(browsers(d), "browser")} at once`;

  function num(f, v, ph) {
    return `<input class="set-in" type="number" min="1" max="${MOST[f]}" data-f="${f}" value="${esc(v == null ? "" : v)}" placeholder="${esc(ph)}">`;
  }

  function form(d) {
    const m = block(d), line = settingsForm.line;
    const body = settingsForm.lines("",
      line("Agents at once", num("agents", m.agents, got ? got.agents : AGENTS),
        "how many agents may work at the same time across all projects; each mostly waits on the model, so this mostly costs subscription quota, not CPU") +
      line("Test runs at once", num("checks", m.checks, got ? got.checks : ""),
        "how many test runs — eki's checks and the agents' own tests — may run at the same time; each takes several cores, so keep this low enough that load stays near the core count") +
      line("Browsers at once", num("browsers", m.browsers, got ? got.browsers : BROWSERS),
        "how many browsers eki's runs may have open at once (Playwright, in an agent's script or the playtester's MCP); each renders the game, so a few already load the machine; a browser past the cap waits for one to close") +
      line("Memory floor (GB)", num("floor_gb", m.floor_gb, FLOOR_GB),
        "new agents, test runs and browsers wait while free memory is below this; running work is never stopped") +
      line("Swap at most (%)", num("swap_max", m.swap_max, SWAP_MAX),
        "new agents, test runs and browsers wait while more of the swap than this is in use; running work is never stopped") +
      `<div class="set-sub" data-machine-now>${esc(got ? got.line : "now: reading…")}</div>` +
      `<div class="set-sub" data-machine-mem>${memory()}</div>`);
    setTimeout(live, 0);
    return `<div data-machine>${settingsForm.fold("machine", `<span data-machine-sum>${esc(summary(d))}</span>`, body, open)}</div>`;
  }

  // each kind's memory estimate ('browser 1.1 GB (12 runs)', 'agent 0.3 GB (default)'), why new
  // work waits if it does, and that the counts stay as upper bounds
  function estimate(e) {
    const gb = `${e.kind} ${Number(e.gb).toFixed(1)} GB`;
    return e.learned ? `${gb} (${plural(e.samples, "run")})` : `${gb} (default)`;
  }
  function memory() {
    const mem = got && got.memory;
    if (!mem) return "";
    const est = (mem.estimates || []).map(estimate).join(" · ");
    return (mem.waiting ? `<div class="set-memwait"><b>${esc(mem.waiting)}</b></div>` : "") +
      (est ? `<div>memory each needs: ${esc(est)}</div>` : "") +
      `<div class="dim">new work starts only if its estimate fits above the floor; the counts above stay as upper bounds</div>`;
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
    const mem = document.querySelector("[data-machine-mem]");
    if (mem && got) mem.innerHTML = memory();
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
    if (!KEYS.includes(f)) return false;
    const m = block(d), v = el.value.trim();
    if (v === "" || !isFinite(+v)) delete m[f]; else m[f] = Math.max(1, Math.min(MOST[f], Math.round(+v)));
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
