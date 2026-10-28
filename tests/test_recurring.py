import sys, unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from state import State  # noqa: E402
from fakegl import FakeGL  # noqa: E402
import recurring  # noqa: E402

FRI_16 = datetime(2026, 10, 30, 16, 5)   # Friday
TASK = {"id": "weekly-status", "project": "acme/kb", "title": "Статус недели {date}",
        "description": "Собери статус по открытым задачам.", "every": "fri", "at": "16:00"}


def gl():
    return FakeGL(routes={"users": [{"id": 5}]}, pids={"acme/kb": 7})


class RecurringTest(unittest.TestCase):
    def test_created_once_per_period(self):
        g, s = gl(), State()
        recurring.run(g, s, [TASK], FRI_16)
        recurring.run(g, s, [TASK], FRI_16.replace(minute=30))
        created = [d for p, d in g.posts if p == "projects/7/issues"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["title"], "Статус недели 2026-10-30")
        self.assertIn("claude", created[0]["labels"])

    def test_not_before_time_or_wrong_day(self):
        g = gl()
        recurring.run(g, State(), [TASK], FRI_16.replace(hour=15))
        recurring.run(g, State(), [TASK], datetime(2026, 10, 29, 17, 0))
        self.assertEqual(g.posts, [])

    def test_daily_and_monthly(self):
        g, s = gl(), State()
        recurring.run(g, s, [dict(TASK, id="d", every="day")], FRI_16)
        recurring.run(g, s, [dict(TASK, id="m", every="month:30")], FRI_16)
        recurring.run(g, s, [dict(TASK, id="m2", every="month:1")], FRI_16)
        self.assertEqual(len(g.posts), 2)

    def test_assignee_resolved(self):
        g = gl()
        recurring.run(g, State(), [dict(TASK, assignee="alice")], FRI_16)
        self.assertEqual(g.posts[0][1]["assignee_ids"], [5])

    def test_bad_entry_skipped_not_crash(self):
        g = gl()
        recurring.run(g, State(), [{"id": "x", "every": "sometimes"}, TASK], FRI_16)
        self.assertEqual(len(g.posts), 1)

    def test_load_missing_file(self):
        self.assertEqual(recurring.load(Path("/nonexistent/recurring.json")), [])


if __name__ == "__main__":
    unittest.main()
