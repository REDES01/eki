"""The composer never shows a scrollbar or scrolls sideways: long text wraps or ends in an ellipsis."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
CSS = re.sub(r"/\*.*?\*/", "", (WEB / "chat.css").read_text(), flags=re.S)
RULES = [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)]
# everything in and right under #composer, by the names index.html gives them
PARTS = ("#composer", "#strip", ".thumb", "#prompt", ".bar", "#options", "#chip", ".optchip",
         ".menu", "#menu", ".mrow", ".check", "#send", "#attach", ".round", ".icon")


def _composer_rules():
    out = []
    for sel, body in RULES:
        if sel.startswith("@"):
            continue
        for one in sel.split(","):
            if any(re.search(re.escape(p) + r"(?![\w-])", one) for p in PARTS):
                out.append((one.strip(), body))
    return out


def _decls(body):
    return {k.strip(): v.strip() for k, _, v in (d.partition(":") for d in body.split(";")) if v.strip()}


def test_nothing_in_the_composer_scrolls_sideways():
    rules = _composer_rules()
    assert any(s == "#prompt" for s, _ in rules)
    for sel, body in rules:
        d = _decls(body)
        assert not re.search(r"\b(auto|scroll)\b", d.get("overflow", "")), sel
        assert not re.search(r"\b(auto|scroll)\b", d.get("overflow-x", "")), sel


def test_no_range_slider():
    for sel, _ in _composer_rules():
        assert "range" not in sel, sel
    html = (WEB / "index.html").read_text()
    form = html[html.index('<form id="composer"'):html.index("</form>")]
    assert 'type="range"' not in form


def test_scrollbars_hidden_inside_the_composer():
    flat = re.sub(r"\s+", " ", CSS)
    assert re.search(r"#composer \* \{[^}]*scrollbar-width: none", flat) or \
        re.search(r"#composer, #composer \* \{[^}]*scrollbar-width: none", flat)
    assert re.search(r"#composer ::-webkit-scrollbar[^{]*\{[^}]*display: none", flat)


def test_textarea_wraps_and_never_overflows_sideways():
    d = {}
    for sel, body in _composer_rules():
        if sel == "#prompt":
            d.update(_decls(body))
    assert d["overflow-x"] == "hidden"
    assert d["overflow-wrap"] == "anywhere"
    assert d["max-height"] == "240px"  # it still grows with what you type, up to here


def test_chip_keeps_the_ellipsis():
    d = {}
    for sel, body in _composer_rules():
        if sel in (".optchip", "#chip"):
            d.update(_decls(body))
    assert d["white-space"] == "nowrap"
    assert d["overflow"] == "hidden"
    assert d["text-overflow"] == "ellipsis"
    assert "100%" in d["max-width"]  # shrinks with a narrow window instead of poking out of the card
    opts = _decls(next(b for s, b in _composer_rules() if s == "#options"))
    assert opts["min-width"] == "0"


def test_attachments_wrap_and_the_box_fits_its_column():
    strip = _decls(next(b for s, b in _composer_rules() if s == "#strip"))
    assert strip["flex-wrap"] == "wrap"
    box = _decls(next(b for s, b in _composer_rules() if s == "#composer"))
    assert box["width"] == "calc(100% - 40px)" and box["min-width"] == "0"
