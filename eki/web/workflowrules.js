// The check command in the workflow stage panel (workflowinspect.js draws it, for the Check command and
// Batch stages): either one line of text, or path rules as rows — name, paths (separated by commas or new
// lines, .gitignore patterns as eki/pathrules.py reads them) and the command — with add row, remove row and
// a switch between the two. A rule that names tests ({paths, tests}: test-file patterns, or "all" for the
// whole suite) keeps its tests in place of the command, and saves them back as tests. It holds the rows being edited; what it hands back is the settings to save
// into the workflow card (POST …/cards/<card> {"settings": …}): {command: "text"}, or {command: [{name?,
// paths, run | tests}]} with no name key when the name is blank — and nothing while a row still lacks paths or a
// command, so a half-typed row is never sent. Plus the batch_command field: what gate 2 runs together. eki's
// own rules are drawn read-only, each as its paths → its tests ("whole suite" for "all") or its command.
//   window.workflowRules = { start(command), html(state, opts), batch(spec, opts), change(state, ds, value),
//                            click(state, ds), list(rows), hint(state), runs(rule) }
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const row = (r) => ({ name: (r && typeof r.name === "string") ? r.name : "",
                        paths: (r && Array.isArray(r.paths) ? r.paths : []).join("\n"),
                        run: (r && typeof r.run === "string") ? r.run : "",
                        ...(named(r) ? { tests: tests(r.tests).join("\n") } : {}) });
  // a rule that names tests rather than a command
  const named = (r) => !!r && (Array.isArray(r.tests) ? r.tests.length > 0 : typeof r.tests === "string" && !!r.tests.trim());
  // a rule's tests as a list: "all" (or ["all"]) is ["all"], the whole suite
  const tests = (t) => (Array.isArray(t) ? t.map((p) => String(p).trim()).filter(Boolean) : split(t));
  // what a rule runs, in plain words (eki/workflow.py rule_runs): its tests, "whole suite", or its command
  function runs(r) {
    if (named(r)) {
      const t = tests(r.tests);
      return t.length === 1 && t[0] === "all" ? "whole suite" : t.join(", ");
    }
    return String((r && r.run) || "");
  }

  // the editing state for a card's `command`: text or rules
  function start(command) {
    if (Array.isArray(command)) return { mode: "rules", rows: command.map(row), text: "" };
    return { mode: "text", rows: [], text: typeof command === "string" ? command : "" };
  }

  const split = (t) => String(t || "").split(/[,\n]/).map((p) => p.trim()).filter(Boolean);

  // the rows as the card's list, or null while one is incomplete (or there are none)
  function list(rows) {
    if (!rows.length) return null;
    const out = [];
    for (const r of rows) {
      const paths = split(r.paths), name = String(r.name || "").trim();
      if (!paths.length) return null;
      if ("tests" in r) {
        const t = tests(r.tests);
        if (!t.length) return null;
        const named = t.length === 1 && t[0] === "all" ? "all" : t;
        out.push(name ? { name, paths, tests: named } : { paths, tests: named });
        continue;
      }
      const run = String(r.run || "").trim();
      if (!run) return null;
      out.push(name ? { name, paths, run } : { paths, run });
    }
    return out;
  }

  // why nothing is saved yet, '' when the rows are whole
  function hint(st) {
    if (st.mode !== "rules") return "";
    if (!st.rows.length) return "add a rule, or switch to one command";
    return list(st.rows) ? "" : "give each rule its paths and its command to save";
  }

  function rows(st, dis) {
    return st.rows.map((r, i) => `<div class="wr-row">
      <input class="set-in wr-name" data-wr-row="${i}" data-wr-key="name" value="${esc(r.name)}" placeholder="name (optional)" spellcheck="false"${dis}>
      <textarea class="set-in wr-paths" data-wr-row="${i}" data-wr-key="paths" rows="2" placeholder="design/script/**, *.md" spellcheck="false"${dis}>${esc(r.paths)}</textarea>
      ${"tests" in r
        ? `<textarea class="set-in wr-tests" data-wr-row="${i}" data-wr-key="tests" rows="2" placeholder="tests/test_x*.py, or all" spellcheck="false"${dis}>${esc(r.tests)}</textarea>`
        : `<input class="set-in wi-text wr-run" data-wr-row="${i}" data-wr-key="run" value="${esc(r.run)}" placeholder="npm run check" spellcheck="false"${dis}>`}
      <button type="button" class="wr-del" data-wr-del="${i}" title="remove this rule"${dis}>×</button></div>`).join("");
  }

  // the command field's control: the switch, then the line or the rows (opts: dis, eki, wait)
  function html(st, opts) {
    const o = opts || {}, dis = o.dis || "";
    if (o.eki && st.mode === "rules" && st.rows.length) {
      return `<div class="wr-rows">${st.rows.map((r) => `<div class="wr-ro"><code>${esc(split(r.paths).join(", "))}</code> → ${esc(runs(r))}</div>`).join("")}</div>`;
    }
    if (o.eki) {
      return `<input class="set-in wi-text" data-wi="command" value="${esc(st.text)}" placeholder="bin/check" spellcheck="false"${dis}>`;
    }
    const tab = (m, words) => `<button type="button" class="wr-mode${st.mode === m ? " wr-on" : ""}" data-wr-mode="${m}"${dis}>${words}</button>`;
    const body = st.mode === "rules"
      ? `<div class="wr-rows">${rows(st, dis) || '<div class="dim">no rules yet</div>'}</div>
         <button type="button" class="wr-add" data-wr-add="1"${dis}>+ add rule</button>`
      : `<input class="set-in wi-text" data-wi="command" value="${esc(st.text)}" placeholder="the project's check command" spellcheck="false"${dis}>`;
    const why = hint(st);
    return `<div class="wr-modes">${tab("text", "one command")}${tab("rules", "rules by path")}</div>${body}` +
      (why ? `<div class="dim wi-wait">${esc(why)}</div>` : "") + (o.wait || "");
  }

  // the batch_command field (opts: dis, command — the card's text command, its default)
  function batch(spec, opts) {
    const o = opts || {}, v = typeof spec.batch_command === "string" ? spec.batch_command : "";
    const ph = typeof spec.command === "string" && spec.command ? `same as each change: ${spec.command}` : "npm run build";
    return `<input class="set-in wi-text" data-wi="batch_command" value="${esc(v)}" placeholder="${esc(ph)}" spellcheck="false"${o.dis || ""}>`;
  }

  // a field in the rows changed: the settings to save, or null
  function change(st, ds, value) {
    const r = st.rows[Number(ds.wrRow)];
    if (!r || !(ds.wrKey in r)) return null;
    r[ds.wrKey] = value;
    const l = list(st.rows);
    return l ? { command: l } : null;
  }

  // a switch, add or remove: the settings to save, or null (redraw only)
  function click(st, ds) {
    if (ds.wrMode && ds.wrMode !== st.mode) {
      st.mode = ds.wrMode;
      if (st.mode === "rules") {
        if (!st.rows.length) st.rows = [{ name: "", paths: "", run: st.text }];
        return null;
      }
      if (!st.text) st.text = (st.rows.find((r) => String(r.run || "").trim()) || {}).run || "";
      return st.text.trim() ? { command: st.text.trim() } : null;
    }
    if (ds.wrAdd) { st.rows.push({ name: "", paths: "", run: "" }); return null; }
    if (ds.wrDel != null) {
      st.rows.splice(Number(ds.wrDel), 1);
      const l = list(st.rows);
      return l ? { command: l } : null;
    }
    return null;
  }

  window.workflowRules = { start, html, batch, change, click, list, hint, runs };
})();
