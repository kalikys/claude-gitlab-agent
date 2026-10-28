import json, sys, tempfile, unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from state import State  # noqa: E402
from runner import RunResult  # noqa: E402
from triggers import Job  # noqa: E402
import poll, runner, schedule  # noqa: E402
from test_poll import gl_with, issue, Recorder, BOT, G, TODAY  # noqa: E402
from fakegl import FakeGL  # noqa: E402


class BudgetTest(unittest.TestCase):
    def test_cost_recorded_and_budget_stops_runs(self):
        s = State(cost={TODAY.isoformat(): 14.95})
        gl = gl_with([issue(1), issue(2)])
        rec = Recorder(gl=gl)
        poll.tick(gl, s, Path(tempfile.mkdtemp()), rec, TODAY, BOT, G, budget=15.0)
        self.assertEqual(len(rec.jobs), 1)               # 14.95 < 15 -> one run, then 15.05 >= 15
        self.assertAlmostEqual(s.cost[TODAY.isoformat()], 15.05)

    def test_budget_reached_no_runs(self):
        s = State(cost={TODAY.isoformat(): 15.0})
        rec = Recorder()
        poll.tick(gl_with([issue(1)]), s, Path(tempfile.mkdtemp()), rec, TODAY, BOT, G, budget=15.0)
        self.assertEqual(rec.jobs, [])


class ModelTest(unittest.TestCase):
    def test_routine_jobs_use_routine_model(self):
        self.assertEqual(runner.model_for(Job("inbox", "p", None, "r"), {"CLAUDE_MODEL_ROUTINE": "sonnet"}), "sonnet")
        self.assertEqual(runner.model_for(Job("digest", "p", None, "r"), {}), "sonnet")
        self.assertIsNone(runner.model_for(Job("issue", "p", 1, "r"), {}))
        self.assertEqual(runner.model_for(Job("issue", "p", 1, "r"), {"CLAUDE_MODEL": "opus"}), "opus")


class ReportTest(unittest.TestCase):
    def test_daily_report_posts_summary_once_issue(self):
        h = Path(tempfile.mkdtemp())
        (h / "logs").mkdir()
        lines = [{"job": "acme/kb:issue:1", "ok": True, "cost_usd": 0.3, "reason": "r"},
                 {"job": "acme/kb:issue:2", "ok": False, "cost_usd": 0.1, "reason": "r", "summary": "timeout"}]
        (h / "logs" / f"{TODAY.isoformat()}.jsonl").write_text("\n".join(json.dumps(l) for l in lines))
        gl = FakeGL(pids={"acme/gitlab-agent": 11})
        s = State(failures={"acme/kb:issue:2": 3})
        schedule.run_report(gl, s, h, TODAY, budget=15.0)
        schedule.run_report(gl, s, h, TODAY, budget=15.0)
        created = [p for p in gl.posts if p[0] == "projects/11/issues"]
        self.assertEqual(len(created), 1)               # journal issue created once
        notes = [d["body"] for p, d in gl.posts if p.endswith("/notes")]
        self.assertEqual(len(notes), 2)
        self.assertIn("Запусков: 2", notes[0])
        self.assertIn("$0.40", notes[0])
        self.assertIn("issue:2", notes[0])


if __name__ == "__main__":
    unittest.main()


class UnknownCostTest(unittest.TestCase):
    def test_unknown_cost_counted_conservatively(self):
        s, gl = State(), gl_with([issue(1)])

        def rec(job, h, ctx):
            return RunResult(False, None, None, "timeout after 1800s")
        poll.tick(gl, s, Path(tempfile.mkdtemp()), rec, TODAY, BOT, G, budget=15.0)
        self.assertEqual(s.cost[TODAY.isoformat()], runner.UNKNOWN_COST_USD)
