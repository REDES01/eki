"""Answer what an agent asked: a question, a permission, a form."""
from __future__ import annotations

from .. import asks
from .common import conn

NAME = "answer"
HELP = "list open questions, or answer one"


def add(p) -> None:
    p.add_argument("ask", nargs="?", help="the ask id (leave out to list open ones)")
    p.add_argument("choice", nargs="*", help="your answer: an option's label, or your own words")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--allow", action="store_true")
    g.add_argument("--always", action="store_true", help="allow, and don't ask again for the same")
    g.add_argument("--deny", action="store_true")


def response_for(a: dict, choice: str, allow: bool = True, always: bool = False) -> dict:
    if a["kind"] == "question":
        answers = {}
        for q in a.get("questions") or []:
            labels = [o.get("label", "") for o in q.get("options") or []]
            pick = choice
            if choice.isdigit() and 0 < int(choice) <= len(labels):
                pick = labels[int(choice) - 1]
            answers[q["question"]] = [pick] if q.get("multiSelect") else pick
        return {"allow": True, "answers": answers}
    return {"allow": allow, "always": always}


def show(a: dict) -> str:
    if a["kind"] == "question":
        lines = []
        for q in a.get("questions") or []:
            lines.append(f"? {q.get('question')}")
            lines += [f"    {i}. {o.get('label')}" + (f" — {o['description']}" if o.get("description") else "")
                      for i, o in enumerate(q.get("options") or [], 1)]
        return "\n".join(lines)
    if a["kind"] == "permission":
        return f"? {a.get('title') or a.get('tool')}: {a.get('detail', '')}"
    return f"? {a.get('server', '')}: {a.get('message', '')} {a.get('url', '')}"


def run(args) -> int:
    c = conn()
    if not args.ask:
        open_ = [asks.view(a) for a in asks.open_asks(c)]
        for a in open_:
            print(f"[{a['id']}] run {a['run']}\n{show(a)}\n")
        if not open_:
            print("nothing is waiting for you")
        return 0
    row = asks.get(c, args.ask)
    if row is None:
        raise KeyError(f"no ask {args.ask}")
    a = asks.view(row)
    resp = response_for(a, " ".join(args.choice), allow=not args.deny, always=args.always)
    print(asks.answer(c, a["id"], resp))
    return 0
