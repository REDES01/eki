"""status()['caps']: the build cap in force for each project with items building or waiting,
other projects' items counted against the machine's agents at once."""
from eki import api, machinecaps, projects

from test_buildcap import _repo, building


def test_two_projects_building_count_each_other(conn, tmp_path):
    machinecaps.save(agents=6)
    a = projects.add(conn, _repo(tmp_path / "alpha", parallel=24))
    b = projects.add(conn, _repo(tmp_path / "beta", parallel=24))
    building(conn, a, 3)
    building(conn, b, 2)
    building(conn, b, 1, state="waiting")
    caps = api.status(conn)["caps"]
    assert [(c["id"], c["name"]) for c in caps] == [(a, "alpha"), (b, "beta")]
    assert set(caps[0]) == {"id", "name", "building", "others", "machine", "project", "now", "by", "line"}
    assert caps[0]["project"] == 24                    # buildcap's own: the project's ceiling
    assert caps[0]["line"] == "3 building · up to 4 now (machine: 6, 2 in other projects) · this project allows 24"
    assert caps[1]["line"] == "2 building · up to 3 now (machine: 6, 3 in other projects) · this project allows 24"


def test_a_project_whose_own_cap_is_smaller(conn, tmp_path):
    machinecaps.save(agents=6)
    a = projects.add(conn, _repo(tmp_path / "alpha", parallel=2))
    building(conn, a, 1)
    building(conn, None, 1, state="waiting")            # eki, waiting only: listed, named "eki"
    caps = api.status(conn)["caps"]
    assert [(c["id"], c["name"]) for c in caps] == [(a, "alpha"), ("eki", "eki")]
    assert caps[0]["by"] == "project"
    assert caps[0]["line"] == "1 building · up to 2 now (this project) · the machine allows 6"
    assert caps[1]["building"] == 0 and caps[1]["others"] == 1


def test_nothing_building_or_waiting_is_empty(conn, tmp_path):
    machinecaps.save(agents=6)
    a = projects.add(conn, _repo(tmp_path / "alpha", parallel=4))
    assert api.status(conn)["caps"] == []
    building(conn, a, 2, state="judging")
    assert api.status(conn)["caps"] == []
