"""PR follow-ups (eki/prfollow.py): the person's PR comments come back as builds of the same item."""
import json

import pytest

from eki import chores, db, github, paths, prfollow, selfwork, store, workspace
from conftest import run_inline
from test_project_goals import built, planned, says, src  # noqa: F401  (src is a fixture)

URL = "https://github.com/me/proj/pull/7"
SEEN = "2026-09-28T10:00:00Z"


@pytest.fixture
def due(monkeypatch):
    monkeypatch.setattr(github, "due", lambda name, now=None: True)


def local_model(on=True):
    rp = paths.config("routing")
    cfg = json.loads(rp.read_text())
    cfg["self"] = {**cfg.get("self", {}), "local": "fake" if on else "off"}
    rp.write_text(json.dumps(cfg))


@pytest.fixture
def pr_item(conn, gh_repo, src, proj, tmp_path, monkeypatch, due):  # noqa: F811
    """A project item built, proposed, pushed to origin with an open PR."""
    _, it = planned(conn, tmp_path, monkeypatch, proj, None)
    it = built(conn, tmp_path, monkeypatch, it)
    assert it["state"] == "proposed"
    github.push(proj, it["commit_sha"], f"eki/{it['id']}")
    with db.tx(conn):
        selfwork._set(conn, it["id"], pr=URL, pr_state="open", pushed=it["commit_sha"], pr_seen=SEEN)
    return selfwork.store_item(conn, it["id"])


def view(gh, *comments):
    gh.answer(["pr", "view", URL], json.dumps({"state": "OPEN", "mergedAt": None, "reviews": [],
                                                "comments": list(comments)}))


def comment(body, at="2026-09-28T11:00:00Z", login="me"):
    return {"author": {"login": login}, "body": body, "createdAt": at}


def posted(gh):
    return [c[c.index("--body") + 1] for c in gh.calls() if c[:2] == ["pr", "comment"]]


def triage(conn, tmp_path, monkeypatch, answer):
    """Run the open prcomment chore inline with the fake model saying `answer`."""
    c = conn.execute("SELECT * FROM chores WHERE kind='prcomment' AND state='open'").fetchone()
    assert c is not None
    monkeypatch.delenv("EKI_FAKE_TOUCH", raising=False)
    says(tmp_path, monkeypatch, answer)
    run_inline(conn, c["run_id"])
    return c


def bare_tip(gh, iid):
    return workspace.git(gh.bare, "rev-parse", f"refs/heads/eki/{iid}")


def test_a_change_requested_rebuilds_pushes_and_answers_once(conn, pr_item, gh_repo, tmp_path, monkeypatch):
    local_model()
    ask = comment("please also add a docstring to greet.py")
    view(gh_repo, ask)
    said = prfollow.tick(conn)
    c = conn.execute("SELECT * FROM chores WHERE kind='prcomment'").fetchone()
    assert c["subject"] == f"{pr_item['id']}:{ask['createdAt']}" and c["state"] == "open"
    assert "docstring" in store.run(conn, c["run_id"])["prompt"]
    assert store.run(conn, c["run_id"])["priority"] == "background"
    assert any("triage" in s for s in said)
    prfollow.tick(conn)                                  # an open chore: nothing new is started
    assert conn.execute("SELECT COUNT(*) FROM chores WHERE kind='prcomment'").fetchone()[0] == 1

    triage(conn, tmp_path, monkeypatch, "It asks for a docstring.\nTRIAGE: change requested\n")
    prfollow.tick(conn)
    it = selfwork.store_item(conn, pr_item["id"])
    assert it["state"] == "waiting" and it["followups"] == 1 and it["tries"] == 0
    assert "Follow-up 1 — asked on the PR by @me:\nplease also add a docstring" in it["spec"]
    assert it["pr_seen"] == ask["createdAt"]
    assert chores.latest(conn, "prcomment", c["subject"])["state"] == "done"

    selfwork.tick(conn)                                  # the ordinary scheduler builds it again
    it = selfwork.store_item(conn, pr_item["id"])
    assert it["state"] == "building" and it["worktree"] == pr_item["worktree"]
    assert "Follow-up 1" in store.run(conn, it["run_id"])["prompt"]
    monkeypatch.setenv("EKI_FAKE_TOUCH", "doc.py")
    says(tmp_path, monkeypatch, "SUMMARY: added the docstring.\n\nITEM: done\n")
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    it = selfwork.store_item(conn, pr_item["id"])
    assert it["state"] == "proposed" and it["commit_sha"] != it["pushed"]
    assert workspace.git(it["worktree"], "merge-base", "--is-ancestor", pr_item["commit_sha"],
                         it["commit_sha"], check=False) == ""

    prfollow.tick(conn)
    it = selfwork.store_item(conn, pr_item["id"])
    sha7 = it["commit_sha"][:7]
    assert it["pushed"] == it["commit_sha"] and bare_tip(gh_repo, it["id"]) == it["commit_sha"]
    assert posted(gh_repo) == [f"Changed in {sha7}: added the docstring. (1 files) {github.MARK}"]

    view(gh_repo, ask, comment(posted(gh_repo)[0], at="2026-09-28T12:00:00Z"))
    prfollow.tick(conn)
    assert len(posted(gh_repo)) == 1 and selfwork.store_item(conn, it["id"])["state"] == "proposed"


def test_an_answer_already_on_the_pr_is_not_posted_again(conn, pr_item, gh_repo):
    with db.tx(conn):                                    # a restart after the comment, before `pushed`
        selfwork._set(conn, pr_item["id"], pushed=pr_item["base"])
    view(gh_repo, comment(f"Changed in {pr_item['commit_sha'][:7]}: x (1 files) {github.MARK}"))
    prfollow.tick(conn)
    assert posted(gh_repo) == []
    assert selfwork.store_item(conn, pr_item["id"])["pushed"] == pr_item["commit_sha"]


def test_triage_not_only_advances_pr_seen(conn, pr_item, gh_repo, tmp_path, monkeypatch):
    local_model()
    view(gh_repo, comment("why did you pick this name?"))
    prfollow.tick(conn)
    triage(conn, tmp_path, monkeypatch, "Just a question.\nTRIAGE: not\n")
    prfollow.tick(conn)
    it = selfwork.store_item(conn, pr_item["id"])
    assert it["state"] == "proposed" and it["followups"] == 0 and it["spec"] == pr_item["spec"]
    assert it["pr_seen"] == "2026-09-28T11:00:00Z"
    assert posted(gh_repo) == []


def test_approval_words_start_no_chore(conn, pr_item, gh_repo):
    local_model()
    view(gh_repo, comment("LGTM!"), comment("Thanks, ship it 👍", at="2026-09-28T11:30:00Z"))
    prfollow.tick(conn)
    assert conn.execute("SELECT COUNT(*) FROM chores").fetchone()[0] == 0
    it = selfwork.store_item(conn, pr_item["id"])
    assert it["state"] == "proposed" and it["pr_seen"] == "2026-09-28T11:30:00Z"
    assert prfollow.approval("ok.") and not prfollow.approval("ok but rename it")


def test_other_logins_eki_own_old_and_empty_comments_are_ignored(conn, pr_item, gh_repo):
    local_model()
    view(gh_repo, comment("rename everything", login="stranger"),
         comment(f"rename everything {github.MARK}"),
         comment("rename everything", at="2026-09-28T09:00:00Z"),
         comment("   "))
    prfollow.tick(conn)
    assert conn.execute("SELECT COUNT(*) FROM chores").fetchone()[0] == 0
    it = selfwork.store_item(conn, pr_item["id"])
    assert it["state"] == "proposed" and it["pr_seen"] == SEEN


def test_no_local_model_counts_as_change_requested(conn, pr_item, gh_repo):
    local_model(False)
    view_reviews = json.dumps({"state": "OPEN", "comments": [], "reviews": [
        {"author": {"login": "me"}, "body": "use f-strings", "submittedAt": "2026-09-28T11:00:00Z"}]})
    gh_repo.answer(["pr", "view", URL], view_reviews)     # a review, not a comment
    prfollow.tick(conn)
    it = selfwork.store_item(conn, pr_item["id"])
    assert it["state"] == "waiting" and it["followups"] == 1 and "use f-strings" in it["spec"]
    assert chores.latest(conn, "prcomment", f"{it['id']}:2026-09-28T11:00:00Z")["state"] == "skipped"


def test_at_the_cap_one_comment_and_no_more(conn, pr_item, gh_repo):
    local_model(False)
    with db.tx(conn):
        selfwork._set(conn, pr_item["id"], followups=3)
    view(gh_repo, comment("and another thing"))
    prfollow.tick(conn)
    view(gh_repo, comment("and another thing"), comment("and one more", at="2026-09-28T12:00:00Z"))
    prfollow.tick(conn)
    assert posted(gh_repo) == [f"eki has made 3 follow-ups here; the next change is yours {github.MARK}"]
    it = selfwork.store_item(conn, pr_item["id"])
    assert it["state"] == "proposed" and it["why"] == prfollow.CAP_WHY and it["followups"] == 3
    assert it["pr_seen"] == "2026-09-28T12:00:00Z"


def test_a_follow_up_ending_unfit_goes_back_to_what_was_pushed(conn, pr_item, gh_repo, tmp_path, monkeypatch):
    local_model(False)
    view(gh_repo, comment("make it shout"))
    prfollow.tick(conn)
    selfwork.tick(conn)
    it = selfwork.store_item(conn, pr_item["id"])
    monkeypatch.setenv("EKI_FAKE_TOUCH", "shout.py")
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    it = selfwork.store_item(conn, pr_item["id"])
    assert it["commit_sha"] != it["pushed"]
    with db.tx(conn):
        selfwork._set(conn, it["id"], state="unfit", error="checks failed\nmore")
    prfollow.tick(conn)
    it = selfwork.store_item(conn, pr_item["id"])
    assert it["state"] == "proposed" and it["commit_sha"] == it["pushed"] == pr_item["commit_sha"]
    assert workspace.head(it["worktree"]) == it["pushed"]
    assert workspace.git(it["worktree"], "rev-parse", "HEAD") == \
        workspace.git(it["worktree"], "rev-parse", f"refs/heads/eki/{it['id']}")
    assert posted(gh_repo) == [f"Couldn't do that: checks failed — the PR is as it was {github.MARK}"]
    assert bare_tip(gh_repo, it["id"]) == pr_item["commit_sha"]
    prfollow.tick(conn)
    assert len(posted(gh_repo)) == 1


def test_a_swept_worktree_is_made_again_at_what_was_pushed(conn, pr_item, gh_repo, proj):
    local_model(False)
    workspace.remove(proj, pr_item["id"])
    view(gh_repo, comment("one more change"))
    prfollow.tick(conn)
    it = selfwork.store_item(conn, pr_item["id"])
    assert it["state"] == "waiting" and workspace.head(it["worktree"]) == pr_item["pushed"]


def test_a_gh_failure_is_said_on_the_item(conn, pr_item, gh_repo):
    gh_repo.answer(["pr", "view", URL], "HTTP 502 bad gateway\nmore", code=1)
    said = prfollow.tick(conn)
    it = selfwork.store_item(conn, pr_item["id"])
    assert it["why"] == "GitHub: HTTP 502 bad gateway" and it["state"] == "proposed"
    assert any("GitHub:" in s for s in said)


def test_a_project_off_github_never_calls_gh(conn, src, proj, fake_gh, tmp_path, monkeypatch, due):  # noqa: F811
    _, it = planned(conn, tmp_path, monkeypatch, proj, None)
    it = built(conn, tmp_path, monkeypatch, it)
    with db.tx(conn):
        selfwork._set(conn, it["id"], pr=URL, pr_state="open", pushed=it["commit_sha"], pr_seen=SEEN)
    assert prfollow.tick(conn) == []
    assert fake_gh.calls() == []


def test_no_open_pr_no_gh_and_not_due_nothing(conn, gh_repo):
    assert prfollow.tick(conn) == []                     # due, but nothing to look at
    assert not github.due("prfollow")                     # within self.issues_minutes of that pass
    assert gh_repo.calls() == []
