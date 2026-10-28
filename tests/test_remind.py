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
PARENT = "https://gitlab.com/acme/team/-/issues/1"


def person_issue(iid=5, who="carol"):
    return {"iid": iid, "project_id": 9, "assignees": [{"username": who}], "labels": ["claude", "claude-waiting"],
            "web_url": f"https://gitlab.com/acme/team/-/issues/{iid}",
            "description": f"Вопросы для @{who}.\n\nРодительская задача: {PARENT}"}


def note(id_, who, when, body="x"):
    return {"id": id_, "author": {"username": who}, "system": False, "created_at": f"{when}T09:00:00Z", "body": body}


def gl_for(notes, issues=None):
    issues = issues if issues is not None else [person_issue()]
    routes = {"groups/acme/issues": lambda p: issues if p.get("labels") == "claude-waiting" else [],
              "projects/9/issues/5/notes": notes,
              "projects/9/issues/1": {"iid": 1, "author": {"username": "alice"}}}
    g = FakeGL(routes, pids={"acme/team": 9})
    return g


def posts_to(g, path):
    return [d["body"] for p, d in g.posts if p == path]


class WorkingDaysTest(unittest.TestCase):
    def test_working_days_skip_weekend(self):
        # Thu 2026-10-22 -> Mon 2026-10-26: Fri, Mon = 2 working days
        self.assertEqual(schedule.working_days(date(2026, 10, 22), date(2026, 10, 26)), 2)


class RemindTest(unittest.TestCase):
    def test_reminder_after_two_working_days(self):
        g = gl_for([note(1, BOT, "2026-10-22", "Вопросы...")])
        schedule.run_reminders(g, State(), date(2026, 10, 26), BOT)
        body = posts_to(g, "projects/9/issues/5/notes")
        self.assertEqual(len(body), 1)
        self.assertIn("@carol", body[0])
        self.assertIn(schedule.REMINDER_MARK, body[0])

    def test_no_reminder_too_early(self):
        g = gl_for([note(1, BOT, "2026-10-23")])
        schedule.run_reminders(g, State(), date(2026, 10, 26), BOT)
        self.assertEqual(g.posts, [])

    def test_no_reminder_when_person_answered(self):
        g = gl_for([note(1, BOT, "2026-10-19"), note(2, "carol", "2026-10-20")])
        schedule.run_reminders(g, State(), date(2026, 10, 26), BOT)
        self.assertEqual(g.posts, [])

    def test_second_reminder_then_stop(self):
        mark = schedule.REMINDER_MARK
        g = gl_for([note(1, BOT, "2026-10-14"), note(2, BOT, "2026-10-16", mark), note(3, BOT, "2026-10-20", mark)])
        schedule.run_reminders(g, State(), date(2026, 10, 21), BOT)
        self.assertEqual(posts_to(g, "projects/9/issues/5/notes"), [])

    def test_escalation_to_parent_once(self):
        mark = schedule.REMINDER_MARK
        notes = [note(1, BOT, "2026-10-12"), note(2, BOT, "2026-10-14", mark), note(3, BOT, "2026-10-16", mark)]
        g, s = gl_for(notes), State()
        schedule.run_reminders(g, s, date(2026, 10, 21), BOT)
        schedule.run_reminders(g, s, date(2026, 10, 22), BOT)
        parent = posts_to(g, "projects/9/issues/1/notes")
        self.assertEqual(len(parent), 1)
        self.assertIn("@alice", parent[0])
        self.assertIn("@carol", parent[0])
        self.assertIn(("projects/9/issues/5", {"add_labels": "claude-no-reply", "remove_labels": "claude-waiting"}), g.puts)

    def test_issue_without_person_assignee_skipped(self):
        g = gl_for([note(1, BOT, "2026-10-14")], issues=[person_issue(who=BOT)])
        schedule.run_reminders(g, State(), date(2026, 10, 26), BOT)
        self.assertEqual(g.posts, [])


if __name__ == "__main__":
    unittest.main()
