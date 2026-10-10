// Customize · Connectors (customize.js draws the frame): the MCP servers Claude Code and Codex
// get. Yours: a row per server — its command or URL, an on/off switch, which programs see it, and
// Edit / Remove. Discover: eki/connectors.py's catalog, each card saying what it needs; Add asks
// for those keys (or a folder) and installs it. Env values come back masked and are never shown
// in full: a password field left blank keeps the value the server already has.
(function () {
  async function load(c) {
    const S = c.S;
    if (S.mode === "discover") S.data.catalog = (await c.api("/api/connectors/discover")).catalog || [];
    else S.data.connectors = (await c.api("/api/connectors")).connectors || [];
  }

  const line = (spec) => spec.url || [spec.command, ...(spec.args || [])].filter(Boolean).join(" ");
  const tick = (ok) => (ok ? "✓" : "–");

  function html(c) {
    const S = c.S, err = S.errs.load ? `<div class="cz-err">${c.esc(S.errs.load)}</div>` : "";
    const act = S.errs.act ? `<div class="cz-err">${c.esc(S.errs.act)}</div>` : "";
    return err + act + (S.mode === "discover" ? discover(c) : yours(c));
  }

  function yours(c) {
    const S = c.S, all = S.data.connectors;
    if (!all) return '<div class="quiet">Loading…</div>';
    if (!all.length) return '<div class="quiet">No connectors yet. Add one, or look in Discover.</div>';
    const list = c.sorted(c.filter(all.map((x) => ({ ...x, line: line(x.spec || {}) })), ["name", "line"]));
    if (!list.length) return '<div class="quiet">No connector matches.</div>';
    return `<div class="cz-rows">${list.map((x) => {
      const k = "mcp:" + x.name, open = S.open.has(k), p = x.programs || {}, env = (x.spec || {}).env || {};
      const n = c.esc(x.name);
      return `<div class="cz-row">
        <div class="cz-line" data-act="fold" data-key="${c.esc(k)}"><strong>${n}</strong>
          <span class="grow quiet">${c.esc(x.line)}</span>
          <span class="quiet">Claude ${tick(p.claude)} · Codex ${tick(p.codex)}</span>
          <button type="button" class="cz-switch${x.on ? " on" : ""}" role="switch" aria-checked="${!!x.on}"
            title="${x.on ? "On: click to turn off" : "Off: click to turn on"}" data-act="toggle" data-name="${n}" data-on="${x.on ? 1 : 0}"></button></div>
        ${open ? `<div class="cz-more"><div class="quiet"><code>${c.esc(x.line)}</code></div>
          ${Object.keys(env).length ? `<div class="quiet">${Object.entries(env).map(([k2, v]) => `${c.esc(k2)} ${c.esc(v)}`).join(" · ")}</div>` : ""}
          <div class="cz-card-foot"><span class="quiet">${x.on ? "on" : "off — in mcp-off.json, no program sees it"}</span>
            <button type="button" class="btn" data-act="edit" data-name="${n}">Edit</button>
            <button type="button" class="btn danger" data-act="remove" data-name="${n}">Remove</button></div></div>` : ""}
      </div>`;
    }).join("")}</div>`;
  }

  function discover(c) {
    const all = c.S.data.catalog;
    if (!all) return '<div class="quiet">Loading…</div>';
    const list = c.sorted(c.filter(all.map((x) => ({ ...x, name: x.title })), ["title", "blurb"]));
    if (!list.length) return '<div class="quiet">No connector matches.</div>';
    const needs = (x) => [...(x.needs || []), ...(x.folder ? ["a folder"] : [])];
    return `<div class="cards">${list.map((x) => `<div class="card">
        <h3>${c.esc(x.title)}</h3><div class="clamp quiet">${c.esc(x.blurb)}</div>
        <div class="cz-card-foot"><span class="quiet">${needs(x).length ? "Needs " + c.esc(needs(x).join(", ")) : "Needs nothing"}</span>
          ${x.added ? '<span class="quiet">Added</span>'
            : `<button type="button" class="btn" data-act="install" data-id="${c.esc(x.id)}">Add</button>`}</div>
      </div>`).join("")}</div>`;
  }

  async function toggle(c, name, on) {
    await c.api(`/api/connectors/${encodeURIComponent(name)}/${on ? "on" : "off"}`, {});
  }

  async function remove(c, name) {
    if (!confirm(`Remove the connector ${name}? Claude Code and Codex stop seeing it.`)) return false;
    await c.api(`/api/connectors/${encodeURIComponent(name)}/remove`, {});
    return true;
  }

  // the password fields for env keys: data-env holds the key, a blank one keeps what's there
  const envFields = (c, keys, masked) => keys.map((k) => `<label>${c.esc(k)}</label>
    <input type="password" data-env="${c.esc(k)}" autocomplete="off" placeholder="${masked && masked[k] ? c.esc(masked[k]) + " — blank keeps it" : "value"}">`).join("");

  function envOf(d) {
    const env = {};
    d.querySelectorAll("[data-env]").forEach((i) => { env[i.dataset.env] = i.value; });
    return env;
  }

  // A catalog card: with nothing needed it installs at once; else a sheet asks for the keys
  // (and the folder for Filesystem) first.
  function install(c, id) {
    const x = (c.S.data.catalog || []).find((y) => y.id === id);
    if (!x) return null;
    const post = (env, folder) => c.api(`/api/connectors/discover/${encodeURIComponent(id)}`,
      Object.assign({ env }, folder ? { folder } : {}));
    if (!(x.needs || []).length && !x.folder) return post({}).then(() => c.reload());
    const f = x.folder ? c.folderField("folder", "~/folder it may use") : null;
    const d = c.sheet(`Add ${x.title}`, `<div class="quiet">${c.esc(x.blurb)}</div>
      ${f ? `<label>Folder</label>${f.html}` : ""}${envFields(c, x.needs || [])}
      <div class="quiet">Kept in eki's mcp.json on this Mac; shown again only as its last 2 characters.</div>`,
      "Add", async (dlg) => {
        const env = envOf(dlg);
        const missing = (x.needs || []).filter((k) => !(env[k] || "").trim());
        if (missing.length) throw new Error(`${x.title} needs ${missing.join(", ")}.`);
        const folder = f ? dlg.querySelector('[name="folder"]').value.trim() : null;
        if (f && !folder) throw new Error(`${x.title} needs a folder.`);
        await post(env, folder);
      });
    if (f) f.wire(d);
    return d;
  }

  // Add, or Edit with the server's name, command or URL and env keys filled in.
  function add(c, old) {
    const spec = (old && old.spec) || {}, masked = spec.env || {};
    const v = (s) => c.esc(s || "");
    const d = c.sheet(old ? `Edit ${old.name}` : "Add a connector", `
      <label>Name</label><input name="name" value="${v(old && old.name)}" placeholder="brave-search" spellcheck="false"${old ? " readonly" : ""}>
      <label>Command and arguments</label><input name="command" value="${v(spec.url ? "" : line(spec))}" placeholder="npx -y @modelcontextprotocol/server-…" spellcheck="false">
      <label>or a URL</label><input name="url" value="${v(spec.url)}" placeholder="https://…/mcp" spellcheck="false">
      ${envFields(c, Object.keys(masked), masked)}
      <label>New env key</label><input name="newkey" placeholder="API_KEY" spellcheck="false">
      <input type="password" name="newval" autocomplete="off" placeholder="value">`,
      old ? "Save" : "Add connector", async (dlg) => {
        const q = (n) => dlg.querySelector(`[name="${n}"]`).value.trim();
        const name = q("name"), cmd = q("command"), url = q("url");
        if (!name) throw new Error("Give it a name.");
        if (!cmd && !url) throw new Error("Give a command or a URL.");
        if (cmd && url) throw new Error("A command or a URL — not both.");
        const env = envOf(dlg), nk = q("newkey");
        if (nk) env[nk] = dlg.querySelector('[name="newval"]').value;
        const body = { name, env };
        if (url) body.url = url;
        else { const parts = cmd.split(/\s+/); body.command = parts[0]; body.args = parts.slice(1); }
        await c.api("/api/connectors", body);
      });
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
    if (a === "edit") return add(c, (S.data.connectors || []).find((x) => x.name === b.dataset.name));
    if (a === "install") {
      try { await install(c, b.dataset.id); } catch (x) { S.errs.act = x.message; c.draw(); }
      return;
    }
    delete S.errs.act;
    b.disabled = true;
    try {
      if (a === "toggle") { await toggle(c, b.dataset.name, b.dataset.on !== "1"); await c.reload(); }
      else if (a === "remove") { if (await remove(c, b.dataset.name)) await c.reload(); }
    } catch (x) { S.errs.act = x.message; c.draw(); }
    b.disabled = false;
  }

  window.customizeConnectors = { load, html, add, click, install, toggle, remove };
})();
