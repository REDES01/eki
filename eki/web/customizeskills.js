// Customize · Skills (customize.js draws the frame). Yours: each skill in eki's skills folder with
// its description and where it came from, Read (its SKILL.md in the side panel) and Remove.
// Discover: the skills eki can find (GET /api/skills/discover), each one Add away. Add opens a
// sheet for a folder or a git URL; eki/skills.py does the copying.
(function () {
  const $ = (id) => document.getElementById(id);

  async function load(c) {
    const S = c.S;
    if (S.mode === "discover") S.data.skillsDiscover = await c.api("/api/skills/discover");
    else S.data.skills = (await c.api("/api/skills")).skills || [];
  }

  const where = (c, s) => s.source ? `from ${c.esc(s.source)}` : "added by hand";

  function html(c) {
    const S = c.S, err = S.errs.load ? `<div class="cz-err">${c.esc(S.errs.load)}</div>` : "";
    const act = S.errs.act ? `<div class="cz-err">${c.esc(S.errs.act)}</div>` : "";
    return err + act + (S.mode === "discover" ? discover(c) : yours(c));
  }

  // one row each: the name and a line of description; the rest (path, where from) on click
  function yours(c) {
    const S = c.S, all = S.data.skills;
    if (!all) return '<div class="quiet">Loading…</div>';
    if (!all.length) return '<div class="quiet">No skills yet. Add one from a folder or a git URL, or look in Discover.</div>';
    const list = c.sorted(c.filter(all, ["name", "description"]));
    if (!list.length) return '<div class="quiet">No skill matches.</div>';
    return `<div class="cz-rows">${list.map((s) => {
      const k = "skill:" + s.name, open = S.open.has(k);
      return `<div class="cz-row" data-skill="${c.esc(s.name)}">
        <div class="cz-line" data-act="fold" data-key="${c.esc(k)}"><strong>${c.esc(s.name)}</strong>
          <span class="grow quiet">${c.esc(s.description)}</span>
          <button type="button" class="btn" data-act="read" data-name="${c.esc(s.name)}">Read</button>
          <button type="button" class="btn danger" data-act="remove" data-name="${c.esc(s.name)}">Remove</button></div>
        ${open ? `<div class="cz-more"><div>${c.esc(s.description)}</div>
          <div class="quiet">${where(c, s)}${s.added_at ? " · added " + new Date(s.added_at * 1000).toLocaleDateString() : ""}</div>
          <div class="quiet"><code>${c.esc(s.path)}</code></div></div>` : ""}</div>`;
    }).join("")}</div>`;
  }

  function discover(c) {
    const got = c.S.data.skillsDiscover;
    if (!got) return '<div class="quiet">Loading…</div>';
    const all = got.skills || [];
    if (!all.length) return `<div class="quiet">${c.esc(got.why || "Nothing to discover right now.")}</div>`;
    const list = c.sorted(c.filter(all, ["name", "description"]));
    if (!list.length) return '<div class="quiet">No skill matches.</div>';
    return `<div class="cards">${list.map((s) => `<div class="card">
        <h3>${c.esc(s.name)}</h3><div class="clamp quiet">${c.esc(s.description)}</div>
        <div class="cz-card-foot"><span class="quiet">${c.esc(s.repo)}</span>
          ${s.installed ? '<span class="quiet">Added</span>'
            : `<button type="button" class="btn" data-act="install" data-source="${c.esc(s.repo + "#" + s.path)}">Add</button>`}</div>
      </div>`).join("")}</div>`;
  }

  // the skill's SKILL.md, in the side panel beside the page
  async function read(c, name) {
    const got = await c.api("/api/skills/" + encodeURIComponent(name));
    $("panel-name").textContent = got.name || name;
    $("panel-path").textContent = "SKILL.md";
    $("panel-open").removeAttribute("href");
    $("panel-body").innerHTML = `<div class="answer pad">${md.render(got.text || "")}</div>`;
    $("panel").hidden = false;
    $("app").classList.add("with-panel");
    window.dispatchEvent(new Event("resize"));
  }

  async function remove(c, name) {
    if (!confirm(`Remove the skill ${name}? Claude Code and Codex stop seeing it.`)) return false;
    await c.api("/api/skills/" + encodeURIComponent(name) + "/remove", {});
    return true;
  }

  async function install(c, source) {
    await c.api("/api/skills", { source });
  }

  function add(c) {
    const f = c.folderField("folder", "~/path/to/skill");
    const d = c.sheet("Add a skill", `
      <label>A folder with a SKILL.md</label>${f.html}
      <label>or a git URL</label>
      <input name="url" placeholder="https://github.com/owner/repo#subdir · owner/repo" spellcheck="false">`,
      "Add skill", async (dlg) => {
        const folder = dlg.querySelector('[name="folder"]').value.trim();
        const url = dlg.querySelector('[name="url"]').value.trim();
        if (!folder && !url) throw new Error("Give a folder or a git URL.");
        if (folder && url) throw new Error("A folder or a git URL — not both.");
        await install(c, folder || url);
      });
    f.wire(d);
    return d;
  }

  async function click(e, c) {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const S = c.S, a = b.dataset.act;
    if (a === "fold") {
      if (e.target.closest("button")) return;
      const k = b.dataset.key;
      S.open.has(k) ? S.open.delete(k) : S.open.add(k);
      return c.draw();
    }
    delete S.errs.act;
    b.disabled = true;
    try {
      if (a === "read") await read(c, b.dataset.name);
      else if (a === "remove") { if (await remove(c, b.dataset.name)) await c.reload(); }
      else if (a === "install") { await install(c, b.dataset.source); await c.reload(); }
    } catch (x) { S.errs.act = x.message; c.draw(); }
    b.disabled = false;
  }

  window.customizeSkills = { load, html, add, click, read, remove, install };
})();
