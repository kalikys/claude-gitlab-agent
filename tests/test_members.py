import sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from state import State  # noqa: E402
from fakegl import FakeGL  # noqa: E402
import poll  # noqa: E402

BOT = "claude-bot"
WATCH = [{"id": 1, "iid": 13, "project_id": 5, "labels": ["claude", "claude-members"],
          "web_url": "https://gitlab.com/acme/devsecops-research/-/issues/13"}]


def gl(members):
    g = FakeGL({"groups/acme/issues": lambda p: WATCH if p.get("labels") == "claude-members" else []},
               members={m: m for m in members + [BOT]})
    return g


class MemberJobsTest(unittest.TestCase):
    def test_first_run_only_remembers(self):
        s = State()
        self.assertEqual(poll.member_jobs(gl(["alice"]), s, BOT, "acme"), [])
        self.assertEqual(s.known_members, ["alice"])

    def test_new_member_wakes_watching_issues(self):
        s = State(known_members=["alice"])
        jobs = poll.member_jobs(gl(["alice", "newbie"]), s, BOT, "acme")
        self.assertEqual([(j.project, j.iid) for j in jobs], [("acme/devsecops-research", 13)])
        self.assertIn("@newbie", jobs[0].reason)
        self.assertEqual(sorted(s.known_members), ["alice", "newbie"])

    def test_no_change_no_jobs(self):
        s = State(known_members=["alice"])
        self.assertEqual(poll.member_jobs(gl(["alice"]), s, BOT, "acme"), [])


if __name__ == "__main__":
    unittest.main()
