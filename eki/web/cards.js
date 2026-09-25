// Cards for what an agent asks you mid-run: a question, a permission, a form.
// Your answer goes to the engine; the worker hands it to the program.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));

  function question(a) {
    const qs = a.questions || [];
    return qs.map((q, i) => `
      <div class="q" data-q="${i}" data-multi="${q.multiSelect ? 1 : 0}">
        ${q.header ? `<div class="chip">${esc(q.header)}</div>` : ""}
        <div class="qtext">${esc(q.question)}</div>
        <div class="opts">${(q.options || []).map((o) => `
          <button type="button" class="opt" data-label="${esc(o.label)}" title="${esc(o.description || "")}">
            <b>${esc(o.label)}</b>${o.description ? `<span>${esc(o.description)}</span>` : ""}</button>`).join("")}
        </div>
        <input class="other" placeholder="Something else…">
      </div>`).join("") + `<div class="acts"><button class="primary" data-act="send">Send</button></div>`;
  }

  function permission(a) {
    return `<div class="qtext">${esc(a.title || (a.tool + " wants to run"))}</div>
      ${a.description ? `<div class="dim">${esc(a.description)}</div>` : ""}
      ${a.detail ? `<pre class="detail">${esc(a.detail)}</pre>` : ""}
      <div class="acts"><button data-act="deny">Deny</button>
        ${a.can_always ? '<button data-act="always">Always allow</button>' : ""}
        <button class="primary" data-act="allow">Allow</button></div>`;
  }

  function form(a) {
    const props = ((a.schema || {}).properties) || {};
    return `<div class="qtext">${esc(a.server ? a.server + " asks" : "A tool asks")}</div>
      <div>${esc(a.message)}</div>
      ${a.url ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.url)}</a>` : ""}
      ${Object.entries(props).map(([k, p]) => `<label class="field">${esc(p.title || k)}
        <input data-field="${esc(k)}" placeholder="${esc(p.description || "")}"></label>`).join("")}
      <div class="acts"><button data-act="deny">Decline</button><button class="primary" data-act="allow">Accept</button></div>`;
  }

  function answered(a) {
    const ans = a.answer || {};
    let said = ans.allow === false ? "declined" : "allowed";
    if (ans.answers) said = Object.values(ans.answers).map((v) => (Array.isArray(v) ? v.join(", ") : v)).join(" · ");
    return `<div class="ask done"><span class="dim">${a.kind === "question" ? "You answered" : "You"}:</span> ${esc(said)}</div>`;
  }

  function render(a) {
    if (a.state !== "open") return answered(a);
    const body = a.kind === "question" ? question(a) : a.kind === "permission" ? permission(a) : form(a);
    return `<div class="ask" data-ask="${a.id}" data-kind="${a.kind}"><div class="ask-tag">needs you</div>${body}</div>`;
  }

  function collect(card, act) {
    const kind = card.dataset.kind;
    if (kind === "question") {
      const answers = {};
      card.querySelectorAll(".q").forEach((q) => {
        const text = q.querySelector(".qtext").textContent;
        const picked = [...q.querySelectorAll(".opt.on")].map((b) => b.dataset.label);
        const other = q.querySelector(".other").value.trim();
        if (other) picked.push(other);
        answers[text] = q.dataset.multi === "1" ? picked : (picked[0] || "");
      });
      return { allow: true, answers };
    }
    if (kind === "form") {
      const content = {};
      card.querySelectorAll("[data-field]").forEach((i) => { if (i.value) content[i.dataset.field] = i.value; });
      return { allow: act === "allow", content };
    }
    return { allow: act !== "deny", always: act === "always" };
  }

  document.addEventListener("click", async (e) => {
    const opt = e.target.closest(".opt");
    if (opt) {
      const q = opt.closest(".q");
      if (q.dataset.multi !== "1") q.querySelectorAll(".opt").forEach((b) => b !== opt && b.classList.remove("on"));
      opt.classList.toggle("on");
      return;
    }
    const btn = e.target.closest(".ask [data-act]");
    if (!btn) return;
    const card = btn.closest(".ask");
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    await fetch(`/api/asks/${card.dataset.ask}/answer`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" },
      body: JSON.stringify(collect(card, btn.dataset.act)),
    });
    window.dispatchEvent(new Event("eki:refresh"));
  });

  window.cards = { render };
})();
