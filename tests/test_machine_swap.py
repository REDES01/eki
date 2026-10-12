"""machine.swap_pct and the wider free_gb, with the subprocess faked."""
from eki import machine

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               1000.
Pages active:                           500000.
Pages inactive:                           2000.
Pages speculative:                         300.
Pages throttled:                             0.
Pages wired down:                       100000.
Pages purgeable:                           700.
"""


def fake(monkeypatch, out):
    monkeypatch.setattr(machine, "_run", lambda *argv: out)


def test_swap_pct_reads_vm_swapusage(monkeypatch):
    monkeypatch.delenv("EKI_SWAP_PCT")
    fake(monkeypatch, "total = 18432.00M  used = 16896.00M  free = 1536.00M  (encrypted)\n")
    assert machine.swap_pct() == 92
    fake(monkeypatch, "total = 2.00G  used = 512.00M  free = 1.50G  (encrypted)\n")
    assert machine.swap_pct() == 25


def test_no_swap_or_no_reading_is_zero(monkeypatch):
    monkeypatch.delenv("EKI_SWAP_PCT")
    fake(monkeypatch, "total = 0.00M  used = 0.00M  free = 0.00M  (encrypted)\n")
    assert machine.swap_pct() == 0
    fake(monkeypatch, "")
    assert machine.swap_pct() == 0


def test_swap_pct_override(monkeypatch):
    monkeypatch.setenv("EKI_SWAP_PCT", "70")
    assert machine.swap_pct() == 70


def test_free_gb_counts_purgeable_and_speculative(monkeypatch):
    monkeypatch.delenv("EKI_FREE_GB")
    fake(monkeypatch, VM_STAT)
    assert machine.free_gb() == (1000 + 2000 + 300 + 700) * 16384 / 2 ** 30
    monkeypatch.setenv("EKI_FREE_GB", "3")
    assert machine.free_gb() == 3.0
