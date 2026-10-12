"""Both build briefs say how card calls end: the foreground blocks, the background wakes you."""
from eki import projectbrief, selfbrief


def test_both_build_briefs_say_how_to_wait_for_card_calls():
    for brief in (selfbrief.BUILD, projectbrief.BUILD):
        flat = " ".join(brief.split())
        assert "`eki use <card>` run in the foreground blocks until the answer is back" in flat
        assert ("Started in the background, end your turn and you will be woken with the answers "
                "(up to an hour, or the card's own limit)") in flat
        assert "Don't end a turn saying you'll wait for notices any other way" in flat
