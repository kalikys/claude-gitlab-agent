import sys, unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from state import State  # noqa: E402
from fakegl import FakeGL  # noqa: E402
import schedule  # noqa: E402

BOT = "claude-bot"
TODAY = date(2026, 10, 26)


def it(due, desc="- [x] сделано\n- [ ] черновик\n- [ ] ревью", assignees=("alice",), iid=4):
    return {"iid": iid, "project_id": 7, "due_date": due, "description": desc, "author": {"username": "bob"},
            "assignees": [{"username": a} for a in assignees], "web_url": f"u{iid}"}


def gl(issues):
    return FakeGL({"groups/acme/issues": lambda p: issues if p.get("labels") == "claude" else []})


def notes(g):
    return [d["body"] for p, d in g.posts if p.endswith("/notes")]


class DueTest(unittest.TestCase):
    def test_nudge_two_days_before_once(self):
        g, s = gl([it("2026-10-28")]), State()
        schedule.run_due(g, s, TODAY, BOT)
        schedule.run_due(g, s, TODAY, BOT)
        out = notes(g)
        self.assertEqual(len(out), 1)
        self.assertIn("@alice", out[0])
        self.assertIn("2026-10-28", out[0])
        self.assertIn("черновик", out[0])
        self.assertNotIn("сделано", out[0])

    def test_overdue_gets_second_nudge(self):
        g, s = gl([it("2026-10-28")]), State()
        schedule.run_due(g, s, TODAY, BOT)
        schedule.run_due(g, s, date(2026, 10, 29), BOT)
        self.assertEqual(len(notes(g)), 2)
        self.assertIn("просроч", notes(g)[1])

    def test_far_or_no_due_ignored(self):
        g = gl([it("2026-11-10"), it(None, iid=5)])
        schedule.run_due(g, State(), TODAY, BOT)
        self.assertEqual(g.posts, [])

    def test_author_when_only_bot_assigned(self):
        g = gl([it("2026-10-27", assignees=(BOT,))])
        schedule.run_due(g, State(), TODAY, BOT)
        self.assertIn("@bob", notes(g)[0])


if __name__ == "__main__":
    unittest.main()
