"""The Mac app takes its look from mac/Theme.swift, not from numbers in views.

These read the Swift source: the app's type, spacing and colours are one
design system, and a size or a raw colour typed into a view is how it drifts.
"""
import re
from pathlib import Path

MAC = Path(__file__).resolve().parent.parent / "mac"

# The chat and the rail — the everyday path — are held to the full system.
# Settings, Providers, Gallery and Goals come in a later slice.
EVERYDAY = ["Views.swift", "Live.swift", "Markdown.swift"]

RAW_COLOUR = re.compile(
    r"Color\.(?:white|black|gray|secondary|primary)\b"
    r"|(?<![\w.])\.(?:white|black|gray|secondary)\b(?!\s*\))"
    r"|NSColor\.\w+Color\b")


def source(name: str) -> str:
    return (MAC / name).read_text()


def test_theme_names_the_scale():
    theme = source("Theme.swift")
    for token in ["enum Space", "enum Radius", "static let hairline", "func raised()",
                  "static let hubDisplay", "static let hubTitle", "static let hubHeading",
                  "static let hubBody", "static let hubCallout", "static let hubCaption",
                  "static let hubMono"]:
        assert token in theme, token


def test_spacing_is_on_a_four_point_scale():
    steps = re.findall(r"static let \w+: Pt = (\d+)", source("Theme.swift").split("enum Space")[1]
                       .split("}")[0])
    assert steps and all(int(n) % 4 == 0 or int(n) == 2 for n in steps)


def test_the_everyday_views_type_from_the_scale():
    for name in EVERYDAY:
        sizes = re.findall(r"\.zoomed\(size: ([\d.]+)", source(name))
        # only the 7-pt dot that stands in for an icon in a rail row is left
        assert sizes in ([], ["7"]), (name, sizes)


def test_the_everyday_views_space_from_the_scale():
    for name in EVERYDAY:
        text = source(name)
        loose = re.findall(r"spacing: ([1-9]\d*)\b", text)
        loose += re.findall(r"\.padding\(\.\w+, ([1-9][\d.]*)\)", text)
        assert loose == [], (name, loose)


def test_no_view_draws_in_a_raw_colour():
    # the picture viewer and the passing notice are dark in both appearances,
    # which is Palette.scrim, not a raw black
    for name in EVERYDAY + ["ImageViewer.swift", "Swipes.swift", "ClaudeCode.swift",
                            "Usage.swift", "Models.swift", "Onboarding.swift"]:
        assert RAW_COLOUR.findall(source(name)) == [], name


def test_cards_and_rows_come_from_one_place():
    for name in EVERYDAY:
        text = source(name)
        # a card is `.card(...)`, a picked row is `.listRow(...)`
        assert "strokeBorder(Palette.accent.opacity" not in text, name
        assert "strokeBorder(Palette.warn.opacity" not in text, name
        assert "Palette.accent.opacity(0.16)" not in text, name
    views = source("Views.swift")
    assert views.count(".listRow(") >= 3            # rail rows, chat rows, the file menu
