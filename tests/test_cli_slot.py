"""`eki slot -- <cmd>` runs the command in a test slot and exits with its code."""
from eki import testslots
from eki.cli import main


def test_slot_passes_the_exit_code_through():
    assert main(["slot", "--", "sh", "-c", "exit 3"]) == 3
    assert testslots.held() == 0


def test_slot_passes_output_through(capfd):
    assert main(["slot", "--", "sh", "-c", "echo out; echo err >&2"]) == 0
    out, err = capfd.readouterr()
    assert out == "out\n" and err == "err\n"


def test_slot_without_a_command_says_how():
    assert main(["slot"]) == 2
