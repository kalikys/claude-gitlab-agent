import json, sys, tempfile, unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from state import State  # noqa: E402
from runner import RunResult  # noqa: E402
from fakegl import FakeGL  # noqa: E402
import schedule  # noqa: E402

TODAY = date(2026, 11, 20)
KB = "acme/kb"


class Rec:
    def __init__(self, summary="ok"):
        self.calls, self.summary = [], summary

    def __call__(self, job, home, ctx):
        self.calls.append((job, ctx))
        return RunResult(True, 0.1, "s", self.summary)


def review_issue(iid, created, owner="bob", author="claude-bot"):
    return {"iid": iid, "created_at": f"{created}T10:00:00Z", "assignees": [{"username": owner}],
            "author": {"username": author},
            "description": "Проверь `projects/demo/reference/ci.md`: верно ли ещё?", "web_url": f"https://x/{iid}"}


def gl_reviews(issues, notes=None):
    routes = {"projects/7/issues": lambda p: issues if p.get("labels") == "kb-review" else []}
    for it in issues:
        routes[f"projects/7/issues/{it['iid']}/notes"] = (notes or {}).get(it["iid"], [])
    return FakeGL(routes)


def weekly(gl, state, archived=None, stale=None, rec=None):
    rec = rec or Rec()
    archived = archived if archived is not None else []
    schedule.run_weekly(gl, state, Path(tempfile.mkdtemp()), rec, TODAY, "claude-bot", KB,
                        stale_fn=lambda: stale or [], archive_fn=lambda doc, reason, url: archived.append(doc) or True)
    return rec, archived


class WeeklyTest(unittest.TestCase):
    def test_review_reminder_at_14_days(self):
        gl, s = gl_reviews([review_issue(1, "2026-11-05")]), State()
        weekly(gl, s)
        weekly(gl, s)
        notes = [p for p in gl.posts if p[0] == "projects/7/issues/1/notes"]
        self.assertEqual(len(notes), 1)
        self.assertIn("@bob", notes[0][1]["body"])

    def test_no_reminder_before_14_days(self):
        gl = gl_reviews([review_issue(1, "2026-11-10")])
        weekly(gl, State())
        self.assertEqual([p for p in gl.posts if "notes" in p[0]], [])

    def test_archive_at_28_days(self):
        gl = gl_reviews([review_issue(2, "2026-10-20")])
        _, archived = weekly(gl, State())
        self.assertEqual(archived, ["projects/demo/reference/ci.md"])
        self.assertIn(("projects/7/issues/2", {"state_event": "close"}), gl.puts)

    def test_review_issue_not_from_bot_ignored(self):
        gl = gl_reviews([review_issue(2, "2026-10-20", author="alice")])
        _, archived = weekly(gl, State())
        self.assertEqual(archived, [])

    def test_owner_replied_no_action(self):
        notes = {2: [{"id": 1, "author": {"username": "bob"}, "system": False,
                      "created_at": "2026-10-25T10:00:00Z", "body": "ещё верно"}]}
        gl = gl_reviews([review_issue(2, "2026-10-20")], notes)
        _, archived = weekly(gl, State())
        self.assertEqual(archived, [])

    def test_weekly_runs_stale_and_digest(self):
        rec, _ = weekly(gl_reviews([]), State(), stale=[{"id": "x"}])
        self.assertEqual([c[0].kind for c in rec.calls], ["stale", "digest"])
        self.assertIn('"id": "x"', rec.calls[0][1])

    def test_weekly_skips_stale_when_nothing(self):
        rec, _ = weekly(gl_reviews([]), State())
        self.assertEqual([c[0].kind for c in rec.calls], ["digest"])


class InboxTest(unittest.TestCase):
    def gl(self, closed=()):
        return FakeGL({f"projects/7/issues/{i}": {"state": "closed" if i in closed else "opened"} for i in (5, 6)})

    def run_inbox(self, files, state, gl=None, summary='{"waiting": {}}'):
        rec = Rec(summary)
        schedule.run_inbox(gl or self.gl(), state, Path(tempfile.mkdtemp()), rec, KB, files_fn=lambda: files,
                           author_fn=lambda f: ("abc123", "alice"))
        return rec

    def test_inbox_waiting_file_skipped(self):
        s = State(inbox_waiting={"inbox/a.md": 5})
        rec = self.run_inbox(["inbox/a.md", "inbox/b.md"], s)
        self.assertEqual(len(rec.calls), 1)
        ctx = rec.calls[0][1]
        self.assertIn("inbox/b.md", ctx)
        self.assertNotIn("inbox/a.md", ctx)
        self.assertIn("@alice", ctx)

    def test_nothing_to_do_no_run(self):
        self.assertEqual(self.run_inbox([], State()).calls, [])

    def test_closed_question_releases_note(self):
        s = State(inbox_waiting={"inbox/a.md": 5})
        rec = self.run_inbox(["inbox/a.md"], s, gl=self.gl(closed=(5,)))
        self.assertEqual(len(rec.calls), 1)

    def test_inbox_parses_waiting(self):
        s = State()
        self.run_inbox(["inbox/b.md"], s, summary='Разобрал.\n{"waiting": {"inbox/b.md": 6}}')
        self.assertEqual(s.inbox_waiting, {"inbox/b.md": 6})

    def test_author_from_noreply_email(self):
        self.assertEqual(schedule.username_from("123-bob@users.noreply.gitlab.com", "Vlad"), "bob")
        self.assertEqual(schedule.username_from("vlad@example.com", "Vlad G"), "Vlad G")


if __name__ == "__main__":
    unittest.main()
