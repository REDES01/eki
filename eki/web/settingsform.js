// Settings: the pieces every section draws its form with (settings.css has their look). A group
// is a rounded box of lines with an optional title above it; a line is a label (and a dim
// sentence under it) on the left and its control on the right; a fold is one summary line that
// opens to its editor (settings.js turns a click on .set-sum into the section's act("fold")).
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));

  const memo = '<span class="set-mem" title="eki adds this in memory; it is written only if you edit it">not saved — in memory</span>';

  const check = (on, attrs, label, off) =>
    `<label class="set-check${off ? " off" : ""}"><input type="checkbox" ${attrs}${on ? " checked" : ""}${off ? " disabled" : ""}> ${esc(label)}</label>`;
  const text = (f, v, ph) => `<input class="set-in" data-f="${f}" value="${esc(v)}" placeholder="${esc(ph || "")}" spellcheck="false">`;
  const field = (label, html) => `<label class="set-field"><span>${label}</span>${html}</label>`;

  // label and sub are html (esc what you pass); ctlHtml is the control
  const line = (label, ctlHtml, sub) => `<div class="set-line">
      <div class="set-label">${label}${sub ? `<div class="set-sub">${sub}</div>` : ""}</div>
      <div class="set-ctl">${ctlHtml || ""}</div></div>`;

  const group = (title, linesHtml) =>
    `${title ? `<h3 class="set-gtitle">${title}</h3>` : ""}<div class="set-group">${linesHtml}</div>`;

  // one summary line; the body is drawn only when open
  const fold = (id, summaryHtml, bodyHtml, open) => `<div class="set-fold${open ? " open" : ""}" data-fold="${esc(id)}">
      <div class="set-sum" role="button" tabindex="0" aria-expanded="${open ? "true" : "false"}">${summaryHtml}</div>
      ${open ? `<div class="set-fbody">${bodyHtml}</div>` : ""}</div>`;

  window.settingsForm = { group, line, fold, check, text, field, memo };
})();
