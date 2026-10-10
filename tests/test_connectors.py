import pytest

from eki import connectors, mcp


def test_catalog_shape():
    ids = [c["id"] for c in connectors.CATALOG]
    assert len(ids) == len(set(ids)) == 9
    for c in connectors.CATALOG:
        assert {"id", "title", "blurb", "needs", "spec"} <= set(c)
        assert c["spec"]["command"] in ("npx", "uvx")


def test_install_brave_with_key():
    assert not connectors.added("brave-search")
    connectors.install("brave-search", {"BRAVE_API_KEY": "bk-123456"})
    assert mcp.servers()["brave-search"] == {
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env": {"BRAVE_API_KEY": "bk-123456"}}
    assert connectors.added("brave-search")
    row = next(c for c in connectors.listing() if c["id"] == "brave-search")
    assert row["added"] and "bk-123456" not in str(row)


def test_install_needs_key():
    with pytest.raises(ValueError):
        connectors.install("brave-search", {"BRAVE_API_KEY": " "})
    assert mcp.all_servers() == []


def test_filesystem_needs_folder(tmp_path):
    with pytest.raises(ValueError):
        connectors.install("filesystem", {})
    connectors.install("filesystem", {}, folder=str(tmp_path))
    assert mcp.servers()["filesystem"]["args"] == ["-y", "@modelcontextprotocol/server-filesystem", str(tmp_path)]
    assert "env" not in mcp.servers()["filesystem"]


def test_unknown_connector():
    with pytest.raises(KeyError):
        connectors.install("nope", {})
