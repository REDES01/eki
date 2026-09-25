// A small Markdown renderer: enough for agent answers, nothing clever.
(function () {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  function inline(s) {
    const codes = [];
    s = s.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
    s = esc(s)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => "<code>" + esc(codes[+i]) + "</code>");
  }

  function render(src) {
    const lines = (src || "").replace(/\r/g, "").split("\n");
    const out = [];
    let para = [], list = null;
    const flush = () => {
      if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; }
      if (list) { out.push(`<${list.tag}>` + list.items.map((i) => "<li>" + inline(i) + "</li>").join("") + `</${list.tag}>`); list = null; }
    };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const fence = line.match(/^```(\w*)/);
      if (fence) {
        flush();
        const body = [];
        for (i++; i < lines.length && !/^```/.test(lines[i]); i++) body.push(lines[i]);
        out.push(`<pre><code>${esc(body.join("\n"))}</code></pre>`);
        continue;
      }
      const h = line.match(/^(#{1,4})\s+(.*)/);
      if (h) { flush(); out.push(`<h${h[1].length + 2}>${inline(h[2])}</h${h[1].length + 2}>`); continue; }
      const li = line.match(/^\s*(?:([-*])|(\d+)[.)])\s+(.*)/);
      if (li) {
        const tag = li[1] ? "ul" : "ol";
        if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; }
        if (!list || list.tag !== tag) { if (list) flush(); list = { tag, items: [] }; }
        list.items.push(li[3]);
        continue;
      }
      if (!line.trim()) { flush(); continue; }
      if (list) flush();
      para.push(line);
    }
    flush();
    return out.join("");
  }

  window.md = { render, esc };
})();
