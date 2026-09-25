"""The command line is a package with a module per command (eki/cli/): each
registers itself, main() finds them, and nothing a change adds has to go
into a shared list."""
from pathlib import Path

from eki import cli, selfwork

#: `eki --help` lists them in this order, as it did when they were one file
ORDER = ["ask", "image", "write", "submit", "wait", "capabilities", "runs", "project", "remember",
         "recall", "watch", "cancel", "diff", "allow", "backends", "history", "show", "cost",
         "summary", "policy", "models", "skills", "context", "agent", "self", "routing", "lineup",
         "observe", "builds", "swap", "goals", "serve", "mcp"]


def _subcommands():
    ap = cli.parser()
    sub = next(a for a in ap._actions if a.dest == "cmd")
    return sub.choices


def test_every_command_registers_itself_in_the_old_order():
    got = _subcommands()
    assert list(got) == ORDER
    for name, p in got.items():                    # each says what runs it
        assert callable(p.get_default("func")), name


def test_the_package_is_what_python_imports_and_the_old_file_is_only_a_marker():
    assert Path(cli.__file__).name == "__init__.py"
    old = Path(cli.__file__).parent.parent / "cli.py"
    assert old.exists()                              # older builds and apps look for it
    assert "def " not in old.read_text()


def test_a_command_module_is_found_without_being_listed(monkeypatch):
    names = [m.__name__.rsplit(".", 1)[-1] for m in cli.commands()]
    assert "common" not in names and "self_" in names and "goals" in names
    assert names == sorted(names, key=lambda n: getattr(__import__(f"eki.cli.{n}", fromlist=["x"]),
                                                        "ORDER", 1000))


def test_stray_words_are_only_taken_by_a_command_that_asks_for_them(monkeypatch):
    from eki.cli import self_
    seen = {}
    monkeypatch.setattr(self_, "cmd_self", lambda a: seen.update(vars(a)) or 0)
    assert cli.main(["self", "apply", "--yes", "abc"]) == 0
    assert seen["request"] == ["apply", "abc"]


def test_a_resolver_is_told_the_old_file_was_split():
    c = {"id": "ab12", "title": "a flag for eki goals", "root": "/x"}
    told = selfwork.resolve_brief(c, ["eki/cli.py", "README.md"], "", "python", "/w")
    assert "eki/cli/" in told and "module for that command" in told
    assert "eki/cli/" not in selfwork.resolve_brief(c, ["README.md"], "", "python", "/w")
