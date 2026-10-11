"""md.js, the one Markdown renderer for answers and side-panel docs: tables, heading ids and the outline,
links and images resolved against the file's folder, and doc paths in text that open in the panel."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# md.js only needs a window to hang itself on.
STUB = """
global.window = {};
require(process.argv[2]);
const cases = JSON.parse(process.argv[3]);
const out = cases.map(([fn, src, opts]) => window.md[fn](src, opts));
process.stdout.write(JSON.stringify(out));
"""


def md(tmp_path, *cases):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "md.js"), json.dumps([list(c) for c in cases])],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def render(tmp_path, src, base=None):
    return md(tmp_path, ("render", src, {"base": base} if base else None))[0]


def test_a_table_has_a_header_row_and_alignment(tmp_path):
    html = render(tmp_path, "| Name | Score |\n|:-----|------:|\n| a | **1** |\n| b | 2 |")
    assert "<table>" in html and "<th" in html and ">Name</th>" in html
    assert 'style="text-align:right"' in html and "<td" in html and "<strong>1</strong>" in html
    assert html.count("<tr>") == 3


def test_heading_ids_match_the_outline(tmp_path):
    src = "# Play test\n\ntext\n\n## Notes & bugs\n\n## Notes & bugs\n\n### `code` *here*\n\n```\n# not a heading\n```"
    html, heads = md(tmp_path, ("render", src, None), ("outline", src, None))
    assert [h["id"] for h in heads] == ["h-play-test", "h-notes-bugs", "h-notes-bugs-2", "h-code-here"]
    assert [h["level"] for h in heads] == [1, 2, 2, 3]
    assert heads[3]["text"] == "code here"
    for h in heads:
        assert f'id="{h["id"]}"' in html
    assert '<h3 id="h-play-test">Play test</h3>' in html     # still shifted for display, as answers were
    assert "# not a heading" in html and "h-not-a-heading" not in html


def test_relative_doc_links_resolve_against_the_files_folder(tmp_path):
    html = render(tmp_path, "See [b](./b.md), [c](../x/c.md#some-part) and [web](https://x.org/a.md) and [top](#Play%20Test).",
                  "/u/notes")
    assert 'href="#" data-file="/u/notes/b.md"' in html
    assert 'data-file="/u/x/c.md" data-anchor="h-some-part"' in html
    assert '<a href="https://x.org/a.md" target="_blank" rel="noopener">web</a>' in html
    assert 'data-anchor="h-play-test"' in html


def test_without_a_base_relative_links_and_images_are_plain_text(tmp_path):
    html = render(tmp_path, "See [b](./b.md) and ![pic](img.png).")
    assert "data-file" not in html and "<img" not in html and "<a" not in html
    assert "See b and pic." in html


def test_images_are_served_from_the_file_api_and_remote_ones_are_links(tmp_path):
    html = render(tmp_path, "![a shot](img.png)\n\n![far](https://x.org/p.png)\n\n![home](~/p/q.png)", "/u/notes")
    assert '<img src="/api/file?path=%2Fu%2Fnotes%2Fimg.png" alt="a shot" loading="lazy">' in html
    assert '<a href="https://x.org/p.png" target="_blank" rel="noopener">far</a>' in html
    assert "/api/file?path=~%2Fp%2Fq.png" in html
    assert html.count("<img") == 2


def test_paths_in_an_answer_open_in_the_panel(tmp_path):
    html = render(tmp_path, "I wrote ~/game/PLAYTEST.md. Read @/u/AGENTS.md, and `/u/DECISIONS.md`.\n\n- see /u/a.txt")
    assert '<button type="button" class="file inline" data-file="~/game/PLAYTEST.md">~/game/PLAYTEST.md</button>.' in html
    assert 'data-file="/u/AGENTS.md">@/u/AGENTS.md</button>' in html
    assert 'data-file="/u/DECISIONS.md"' in html and "<code>/u/DECISIONS.md</code>" not in html
    assert 'data-file="/u/a.txt"' in html


def test_paths_in_fenced_code_and_urls_stay_plain(tmp_path):
    html = render(tmp_path, "```\ncat ~/game/PLAYTEST.md\n```\n\nhttps://x.org/a/b.md and `ls /u/a.md -l` and /u/a.py")
    assert "data-file" not in html
    assert "cat ~/game/PLAYTEST.md" in html


def test_link_paths_skips_tags_and_code(tmp_path):
    got = md(tmp_path, ("linkPaths", '<p title="/u/x.md">see /u/y.md</p><code>/u/z.md</code><a href="#">/u/w.md</a>', None))[0]
    assert got.count("data-file") == 1 and 'data-file="/u/y.md"' in got and 'title="/u/x.md"' in got


def test_nested_lists_blockquotes_and_rules(tmp_path):
    html = render(tmp_path, "- one\n  - inner\n  - inner2\n- two\n\n> quoted **bit**\n> more\n\n---\n\n1. a\n\n2. b")
    assert html.startswith("<ul><li>one<ul><li>inner</li><li>inner2</li></ul></li><li>two</li></ul>")
    assert "<blockquote><p>quoted <strong>bit</strong><br>more</p></blockquote>" in html
    assert "<hr>" in html and "<ol><li>a</li><li>b</li></ol>" in html


def test_answers_render_as_before(tmp_path):
    html = render(tmp_path, "Hello **bold** and *it* with `x<y`.\nline two\n\n- a\n- b\n\n1. c\n\n```py\nif a < b:\n```\n\n[l](https://e.com)")
    assert "<p>Hello <strong>bold</strong> and <em>it</em> with <code>x&lt;y</code>.<br>line two</p>" in html
    assert "<ul><li>a</li><li>b</li></ul>" in html and "<ol><li>c</li></ol>" in html
    assert "<pre><code>if a &lt; b:</code></pre>" in html
    assert '<a href="https://e.com" target="_blank" rel="noopener">l</a>' in html
    assert md(tmp_path, ("esc", "<a&b>", None))[0] == "&lt;a&amp;b&gt;"


def test_the_page_loads_md_js():
    assert '<script src="/ui/md.js"></script>' in (WEB / "index.html").read_text()
    assert len((WEB / "md.js").read_text().splitlines()) < 400
