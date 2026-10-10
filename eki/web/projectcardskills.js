// A skills card's skills in its open panel (projectcardpanel.js calls draw for kind "skills"): each
// skill with its description and a × that deletes its folder (after a confirm), each broken one with
// its error, and an "Add skill" field for a folder with a SKILL.md or a git URL. A name already in the
// card is refused; the message then offers Replace. Routes: eki/api_cardskills.py.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));

  function list(card, ro) {
    const good = card.skills || [], bad = card.broken_skills || [];
    if (!good.length && !bad.length) return '<div class="dim">No skills yet: add a folder with a SKILL.md.</div>';
    const x = (n) => ro ? "" : `<button type="button" class="pj-btn" data-pcs-rm="${esc(n)}" title="Remove ${esc(n)}">×</button>`;
    return good.map((s) => `<div class="pj-item"><b>${esc(s.name)}</b>
        <span class="grow dim">${esc(s.description)}</span>${x(s.name)}</div>`).join("")
      + bad.map((b) => {
        const n = String(b.path || "").split("/").filter(Boolean).pop() || "";
        return `<div class="pj-item"><b>${esc(n)}</b><span class="grow pj-err">${esc(b.error)}</span>${x(n)}</div>`;
      }).join("");
  }

  function say(box, html) {
    const m = box.querySelector(".pcs-msg");
    if (m) m.innerHTML = html;
  }

  function show(box) {
    const html = list(box._card, box._o.ro);
    const l = box.querySelector(".pcs-list");
    if (l && box._last !== html) { l.innerHTML = html; box._last = html; }
    for (const el of box.querySelectorAll(".pcs-add input, .pcs-add button")) el.disabled = !!box._o.ro;
  }

  async function add(box, replace) {
    const input = box.querySelector(".pcs-src");
    const source = input ? input.value.trim() : "";
    if (!source) return say(box, '<div class="pj-err">Give a folder with a SKILL.md or a git URL.</div>');
    say(box, `<div class="dim">${replace ? "Replacing" : "Adding"}…</div>`);
    try {
      const got = await box._o.api(box._o.url("/skills"), { source, replace: !!replace });
      input.value = "";
      say(box, `<div class="dim">Added ${esc(got.added)}.</div>`);
      box._o.done(got.card);
    } catch (x) {
      const msg = String(x.message || "").replace(/;\s*use --replace.*$/, "");
      say(box, `<div class="pj-row"><span class="pj-err">${esc(msg)}</span>${/already there/.test(msg)
        ? '<button type="button" class="pj-btn" data-pcs-replace="1">Replace it</button>' : ""}</div>`);
    }
  }

  async function remove(box, name) {
    if (!confirm(`Remove the skill ${name} from this card? Its folder is deleted; runs in the project stop seeing it.`)) return;
    try {
      await box._o.api(box._o.url("/skills/" + encodeURIComponent(name)), {}, "DELETE");
      say(box, `<div class="dim">Removed ${esc(name)}.</div>`);
      box._card = Object.assign({}, box._card, {
        skills: (box._card.skills || []).filter((s) => s.name !== name),
        broken_skills: (box._card.broken_skills || []).filter((b) => !String(b.path || "").endsWith("/" + name)) });
      show(box);
      box._o.done(null);
    } catch (x) { say(box, `<div class="pj-err">${esc(x.message)}</div>`); }
  }

  function onEvent(e) {
    const box = e.currentTarget, t = e.target;
    if (e.type === "submit") {
      if (!t.matches(".pcs-add")) return;
      e.preventDefault(); e.stopPropagation();
      return add(box, false);
    }
    if (t.closest("[data-pcs-replace]")) return add(box, true);
    const rm = t.closest("[data-pcs-rm]");
    if (rm) remove(box, rm.dataset.pcsRm);
  }

  // o: { api(path, body, method), url(tail), ro, done(card or null) }
  function draw(box, card, o) {
    if (!box) return;
    box._card = card; box._o = o;
    if (!box.dataset.pcsWired) {
      box.dataset.pcsWired = "1";
      box.innerHTML = `<div class="pcs-list"></div>
        <form class="pj-row pcs-add" autocomplete="off">
          <input class="grow pp-args pcs-src" placeholder="Add skill: ~/path/to/skill-folder or a git URL" spellcheck="false">
          <button type="submit" class="pj-btn primary">Add skill</button></form>
        <div class="pcs-msg"></div>`;
      box.addEventListener("click", onEvent);
      box.addEventListener("submit", onEvent);
    }
    show(box);
  }

  window.projectCardSkills = { draw };
})();
