// A small Markdown renderer: enough for agent answers and the side panel's docs, nothing clever.
// md.render(src, {base}) — base is the folder of the file shown, so its relative links and images resolve;
// md.outline(src) — the headings with the ids render gives them; md.linkPaths(html) — local doc paths in
// text become buttons that open in the side panel.
(function () {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const attr = (s) => esc(String(s)).replace(/"/g, "&quot;");
  const DOC = /\.(?:md|markdown|txt)$/i;

  // ---- paths ----
  // A path in text: absolute or ~/, ending in a doc extension, maybe an @mention; never a URL's //host.
  const PATH = /(^|[\s(\[{"'])(@?)((?:~\/|\/(?!\/))[^\s<>"'`&\u0000]*?\.(?:md|markdown|txt))(?=$|[\s)\]}.,;:!?"']|&[a-z]+;)/gi;
  const fileButton = (p, shown) => `<button type="button" class="file inline" data-file="${attr(p)}">${shown}</button>`;

  // Paths in the text of already-escaped HTML, not inside tags, links, buttons or code.
  function linkPaths(html) {
    let skip = 0;
    return String(html).split(/(<[^>]*>)/).map((part) => {
      if (part[0] === "<") {
        const t = part.match(/^<(\/?)(a|button|code|pre)\b/i);
        if (t) skip = Math.max(0, skip + (t[1] ? -1 : 1));
        return part;
      }
      if (skip) return part;
      return part.replace(PATH, (_, pre, at, p) => pre + fileButton(p, at + p));
    }).join("");
  }

  // base + a relative path, with . and .. handled; absolute and ~/ paths stay as they are.
  function resolve(base, rel) {
    try { rel = decodeURIComponent(rel); } catch (e) { /* keep as written */ }
    if (rel[0] === "/" || rel.startsWith("~/")) return rel;
    if (!base) return null;
    const parts = base.replace(/\/+$/, "").split("/");
    for (const seg of rel.split("/")) {
      if (seg === "..") { if (parts.length > 1 && parts[parts.length - 1] !== "~") parts.pop(); }
      else if (seg && seg !== ".") parts.push(seg);
    }
    return parts.join("/") || "/";
  }

  // ---- headings ----
  const plain = (s) => s.replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/[`*_]/g, "").trim();
  function slug(text, seen) {
    const s = plain(text).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "") || "section";
    if (!seen) return s;
    seen[s] = (seen[s] || 0) + 1;
    return seen[s] > 1 ? `${s}-${seen[s]}` : s;
  }

  // ---- inline ----
  const IMAGE = /!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/g;
  const LINK = /\[([^\]]+)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/g;
  const emph = (s) => s
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>");

  function image(alt, src, o) {
    if (/^https?:\/\//i.test(src))           // remote pictures are not loaded, only offered
      return `<a href="${attr(src)}" target="_blank" rel="noopener">${esc(alt || src)}</a>`;
    if (/^[a-z][\w+.-]*:/i.test(src)) return esc(alt);
    const p = resolve(o.base, src);
    return p ? `<img src="/api/file?path=${encodeURIComponent(p)}" alt="${attr(alt)}" loading="lazy">` : esc(alt);
  }

  function link(text, href, o) {
    if (/^https?:\/\//i.test(href)) return `<a href="${attr(href)}" target="_blank" rel="noopener">${text}</a>`;
    if (href[0] === "#") {
      let a = href.slice(1);
      try { a = decodeURIComponent(a); } catch (e) { /* keep */ }
      return `<a href="#" data-anchor="h-${attr(slug(a))}">${text}</a>`;
    }
    const m = href.match(/^([^#?]+)(?:#(.*))?$/);
    if (m && DOC.test(m[1]) && !/^[a-z][\w+.-]*:/i.test(m[1])) {
      const p = resolve(o.base, m[1]);
      if (p) return `<a href="#" data-file="${attr(p)}"${m[2] ? ` data-anchor="h-${attr(slug(m[2]))}"` : ""}>${text}</a>`;
    }
    return text;                              // a link we can't follow here reads as its words
  }

  function inline(s, o) {
    const keep = [];
    const put = (h) => "\u0000" + (keep.push(h) - 1) + "\u0000";
    s = s.replace(/`([^`]+)`/g, (_, c) => {
      const p = c.trim().match(/^(@?)((?:~\/|\/(?!\/))[^\s<>"'`&]*\.(?:md|markdown|txt))$/i);
      return put(p ? fileButton(p[2], esc(p[1] + p[2])) : "<code>" + esc(c) + "</code>");   // agents backtick paths
    });
    s = s.replace(IMAGE, (_, alt, src) => put(image(alt, src, o)));
    s = s.replace(LINK, (_, t, href) => put(link(emph(esc(t)), href, o)));
    s = linkPaths(emph(esc(s)));
    for (let n = 0; n < 3 && s.includes("\u0000"); n++) s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => keep[+i]);
    return s;
  }

  // ---- tables ----
  const ALIGN = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
  function cells(line) {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
    return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
  }
  function table(head, align, rows, o) {
    const al = cells(align).map((c) => c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : "");
    const td = (tag, c, i) => `<${tag}${al[i] ? ` style="text-align:${al[i]}"` : ""}>${inline(c, o)}</${tag}>`;
    const hs = cells(head);
    const body = rows.map((r) => {
      const cs = cells(r);
      return "<tr>" + hs.map((_, i) => td("td", cs[i] || "", i)).join("") + "</tr>";
    }).join("");
    return `<table><thead><tr>${hs.map((c, i) => td("th", c, i)).join("")}</tr></thead>${body ? `<tbody>${body}</tbody>` : ""}</table>`;
  }

  // ---- lists: items nest by their indent ----
  const LI = /^(\s*)(?:([-*+])|(\d+)[.)])\s+(.*)/;
  function list(items, o) {
    let html = "";
    const stack = [];
    const open = (it) => {
      stack.push({ indent: it.indent, tag: it.tag });
      return `<${it.tag}${it.tag === "ol" && it.start !== 1 ? ` start="${it.start}"` : ""}>`;
    };
    for (const it of items) {
      while (stack.length && it.indent < stack[stack.length - 1].indent) html += `</li></${stack.pop().tag}>`;
      const top = stack[stack.length - 1];
      if (!top || it.indent > top.indent) html += open(it);
      else if (top.tag !== it.tag) { stack.pop(); html += `</li></${top.tag}>` + open(it); }
      else html += "</li>";
      const text = it.text.replace(/^\[ \]\s+/, "☐ ").replace(/^\[[xX]\]\s+/, "☑ ");
      html += "<li>" + inline(text, o);
    }
    while (stack.length) html += `</li></${stack.pop().tag}>`;
    return html;
  }

  // ---- blocks ----
  function blocks(lines, o, st) {
    const out = [];
    let para = [], items = null;
    const endPara = () => { if (para.length) { out.push("<p>" + para.map((l) => inline(l, o)).join("<br>") + "</p>"); para = []; } };
    const endList = () => { if (items) { out.push(list(items, o)); items = null; } };
    const flush = () => { endPara(); endList(); };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const fence = line.match(/^\s*(```|~~~)/);
      if (fence) {
        flush();
        const body = [];
        for (i++; i < lines.length && !lines[i].trim().startsWith(fence[1]); i++) body.push(lines[i]);
        out.push(`<pre><code>${esc(body.join("\n"))}</code></pre>`);
        continue;
      }
      const h = line.match(/^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/);
      if (h) {
        flush();
        const level = h[1].length, id = "h-" + slug(h[2], st.seen), shown = Math.min(level + 2, 6);
        st.heads.push({ level, text: plain(h[2]), id });
        out.push(`<h${shown} id="${attr(id)}">${inline(h[2], o)}</h${shown}>`);
        continue;
      }
      if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push("<hr>"); continue; }
      if (/^\s{0,3}>/.test(line)) {
        flush();
        const q = [];
        for (; i < lines.length && /^\s{0,3}>/.test(lines[i]); i++) q.push(lines[i].replace(/^\s{0,3}>\s?/, ""));
        i--;
        out.push("<blockquote>" + blocks(q, o, st) + "</blockquote>");
        continue;
      }
      if (line.includes("|") && i + 1 < lines.length && lines[i + 1].includes("-") && ALIGN.test(lines[i + 1])
          && (lines[i + 1].includes("|") || cells(line).length > 1)) {
        flush();
        const head = line, align = lines[++i], rows = [];
        for (i++; i < lines.length && lines[i].trim() && lines[i].includes("|"); i++) rows.push(lines[i]);
        i--;
        out.push(table(head, align, rows, o));
        continue;
      }
      const li = line.match(LI);
      if (li) {
        endPara();
        if (!items) items = [];
        items.push({ indent: li[1].replace(/\t/g, "    ").length, tag: li[2] ? "ul" : "ol", start: li[3] ? +li[3] : 1, text: li[4] });
        continue;
      }
      if (!line.trim()) {
        if (items) {                          // a blank line inside a list keeps it going if more items follow
          let j = i + 1;
          while (j < lines.length && !lines[j].trim()) j++;
          if (j < lines.length && LI.test(lines[j])) continue;
        }
        flush();
        continue;
      }
      if (items && /^\s{2,}\S/.test(line)) { items[items.length - 1].text += " " + line.trim(); continue; }
      endList();
      para.push(line);
    }
    flush();
    return out.join("");
  }

  function parse(src, opts) {
    const st = { seen: {}, heads: [] };
    const html = blocks((src || "").replace(/\r/g, "").split("\n"), opts || {}, st);
    return { html, heads: st.heads };
  }

  const render = (src, opts) => parse(src, opts).html;
  const outline = (src) => parse(src).heads;

  window.md = { render, outline, linkPaths, esc };
})();
