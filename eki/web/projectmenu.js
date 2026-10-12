// The "⋯" menu on a project — the project page header (project.js) and each card on the Projects
// list (projectspage.js). "Record chats in history" switches the project's chat_history; "Delete project…" asks first in a sheet that says
// in one line what happens, then sends DELETE /api/projects/<id>; the API's error shows in the
// sheet. eki's own project can't be deleted, so it gets no item and no button.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  let menu = null, sheet = null;

  // "Record chats in history": each chat turn's change a commit on the project's branch (eki/chatcommit.py)
  const toggle = (p) => `<button type="button" class="pm-item" role="menuitemcheckbox" aria-checked="${p.chat_history ? "true" : "false"}" data-pm-act="history">` +
    `${p.chat_history ? "✓ " : ""}Record chats in history</button>`;
  const items = (p) => p.id === "eki" ? "" : toggle(p) + '<button type="button" class="pm-item danger" data-pm-act="delete">Delete project…</button>';

  // the ⋯ button; the page catches its click on [data-pm] and calls open()
  function button(p) {
    return items(p) ? `<button type="button" class="pm-more" data-pm="${esc(p.id)}" title="More" aria-label="More">⋯</button>` : "";
  }

  // what the confirm says, from the listed `chats` and `folder`
  function sentence(p) {
    return `Stops its work, archives its ${p.chats || 0} chats, removes eki's worktrees. ` +
      `Your folder ${p.folder || "no folder"} is not touched.`;
  }

  function close() { if (menu) menu.hidden = true; }

  function open(p, anchor, done) {
    if (!menu) {
      menu = document.createElement("div");
      menu.className = "pm-menu";
      document.body.appendChild(menu);
      document.addEventListener("click", (e) => { if (!menu.contains || !menu.contains(e.target)) close(); });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    }
    menu.innerHTML = items(p);
    menu.onclick = (e) => {
      e.stopPropagation();
      const b = e.target.closest && e.target.closest("[data-pm-act]");
      if (b && b.dataset.pmAct === "delete") { close(); confirm(p, done); }
      if (b && b.dataset.pmAct === "history") { close(); history(p, !p.chat_history); }
    };
    if (anchor && anchor.getBoundingClientRect) {
      const r = anchor.getBoundingClientRect();
      menu.style.top = r.bottom + 4 + "px";
      menu.style.right = Math.max(8, window.innerWidth - r.right) + "px";
    }
    menu.hidden = false;
    return menu;
  }

  function confirm(p, done) {
    if (!sheet) {
      sheet = document.createElement("dialog");
      sheet.className = "sheet pm-confirm";
      document.body.appendChild(sheet);
    }
    sheet.innerHTML = `<h2>Delete ${esc(p.name || p.id)}?</h2>
      <p class="pm-line">${esc(sentence(p))}</p>
      <div class="error" id="pm-err"></div>
      <div class="row-end">
        <button type="button" class="btn" id="pm-cancel">Cancel</button>
        <button type="button" class="btn danger" id="pm-delete">Delete</button>
      </div>`;
    sheet.querySelector("#pm-cancel").addEventListener("click", () => sheet.close());
    sheet.querySelector("#pm-delete").addEventListener("click", () => remove(p, done));
    sheet.showModal();
    return sheet;
  }

  async function remove(p, done) {
    const b = sheet.querySelector("#pm-delete");
    b.disabled = true;
    sheet.querySelector("#pm-err").textContent = "";
    try {
      const r = await fetch("/api/projects/" + encodeURIComponent(p.id), { method: "DELETE", headers: { "X-Eki": "1" } });
      const got = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(got.error || r.statusText || "Can't delete the project");
      sheet.close();
      if (done) done(got);
      return got;
    } catch (x) {
      sheet.querySelector("#pm-err").textContent = x.message;
      return null;
    } finally { b.disabled = false; }
  }

  // the switch; the entry shows the answer the API gives back
  async function history(p, on) {
    try {
      const r = await fetch("/api/projects/" + encodeURIComponent(p.id) + "/chat-history", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify({ on }) });
      const got = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(got.error || r.statusText || "Can't change the setting");
      p.chat_history = got.chat_history;
      return got;
    } catch (x) {
      window.alert(x.message);
      return null;
    }
  }

  window.projectMenu = { button, items, sentence, open, confirm, remove, history };
})();
