"""A service card offering an MCP server ("mcp") on the project page: Test (POST …/cards/<name>/test,
eki/api_cardmcp.py) starts that server for a handshake like an mcp card's, and the card payload
masks its mcp.env values the way an mcp card's server.env is masked (eki/api_cards.py)."""
import json
import sys

import pytest

from test_component_mcpprobe import SECRET, SERVER, call, card, project


@pytest.fixture
def cards(conn, proj, tmp_path):
    project(conn, proj)
    script = tmp_path / "dice.py"
    script.write_text(SERVER)
    base = proj / "components"
    card(base / "ableton", {"kind": "service", "about": "Live", "access": "exclusive",
                            "run": ["node", "tools/music/auto.mjs", "--all-new"],
                            "mcp": {"command": sys.executable, "args": [str(script)], "env": {"TOKEN": SECRET}}})
    card(base / "voice", {"kind": "service", "about": "speech", "run": ["tts"]})
    return base


def _card(conn, name):
    return call(conn, "GET", f"/api/projects/p1/cards/{name}")["card"]


def test_a_service_cards_mcp_server_is_probed_and_no_env_value_leaves(conn, cards):
    got = call(conn, "POST", "/api/projects/p1/cards/ableton/test")
    assert got["ok"] and [t["name"] for t in got["tools"]] == ["roll", "flip"]
    assert SECRET not in json.dumps(got)


def test_a_service_card_without_mcp_has_nothing_to_test(conn, cards):
    with pytest.raises(ValueError, match="no MCP server"):
        call(conn, "POST", "/api/projects/p1/cards/voice/test")


def test_the_card_payload_masks_the_services_mcp_env(conn, cards):
    c = _card(conn, "ableton")
    assert SECRET not in json.dumps(c)
    assert c["spec"]["mcp"]["env"] == {"TOKEN": "••••23"}
    assert c["spec"]["mcp"]["command"] == sys.executable and c["spec"]["run"][0] == "node"
    stored = json.loads((cards / "ableton" / "component.json").read_text())
    assert stored["mcp"]["env"]["TOKEN"] == SECRET                     # only the payload is masked
    assert _card(conn, "voice")["spec"] == {"kind": "service", "about": "speech", "run": ["tts"]}
