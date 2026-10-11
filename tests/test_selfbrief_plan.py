"""The plan and build briefs: wide, shallow plans of short turns, and builders
that run only their own tests (eki runs the whole check after the turn)."""
from eki import projectbrief, projects, resolvebrief, selfbrief

BASE = "0123456789abcdef"


def _project(conn, proj):
    return projects.get(conn, projects.add(conn, proj))


def _build_kw():
    return dict(goal="g", title="t", spec="s", files=["eki/x.py"], branch="self/x", base=BASE,
                source="/src", others=[])


def _flat(text):
    return " ".join(text.split())


def test_plans_go_wide_and_short(conn, proj):
    for p in (selfbrief.plan("a goal", BASE), projectbrief.plan("a goal", BASE, _project(conn, proj))):
        p = _flat(p)
        assert "15 minutes" in p
        assert "Usually that is one item" in p
        assert "one root plus a few leaves" in p
        assert "no dependency chain is longer than two" in p
        assert "never a multi-hour run" in p
        assert "under an hour" not in p and "two levels deep" not in p


def test_draft_and_rank_carry_the_same_shape():
    for d in (selfbrief.draft("wish", BASE), selfbrief.rank([], BASE)):
        d = _flat(d)
        assert "15 minutes" in d and "usually one item" in d
        assert "no dependency chain longer than two" in d and "never a multi-hour run" in d


def test_the_builder_runs_only_its_own_tests():
    b = _flat(selfbrief.build(**_build_kw()))
    assert "run `bin/check` before you finish" not in b
    assert "don't run the whole suite" in b
    assert "run the tests for the files you touched" in b and "`eki slot -- bin/check tests/test_x.py`" in b
    assert "eki runs the whole check right after your turn" in b


def test_the_resolver_runs_only_its_own_tests():
    r = _flat(resolvebrief.resolve(goal="g", title="t", spec="s", summary="", head=BASE,
                                   conflicted=["eki/x.py"], head_changes="", landed=""))
    assert "run `bin/check`" not in r.replace("don't run the whole suite (plain `bin/check`)", "")
    assert "leave it green" not in r
    assert "run the tests for the files you touched" in r
    assert "eki runs the whole check right after your turn" in r


def test_a_project_builder_still_runs_the_projects_check(conn, proj):
    p = _project(conn, proj)
    b = projectbrief.build(**_build_kw(), project=p)
    assert projectbrief._check(p) in b and "before you finish" in b
