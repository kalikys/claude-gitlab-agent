import sys, tempfile, unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from state import State  # noqa: E402
from runner import RunResult  # noqa: E402
from fakegl import FakeGL  # noqa: E402
import poll  # noqa: E402

BOT, G = "claude-bot", "acme"
TODAY = date(2026, 10, 24)


def issue(iid):
    return {"id": 100 + iid, "iid": iid, "project_id": 7, "labels": ["claude"], "assignees": [],
            "web_url": f"https://gitlab.com/acme/kb/-/issues/{iid}"}


def gl_with(issues, notes=None, pipeline=None):
    routes = {f"groups/{G}/issues": lambda p: issues if p.get("labels") == "claude" else [],
              f"groups/{G}/merge_requests": [], "todos": [],
              "projects/7/pipelines": [pipeline] if pipeline else []}
    for it in issues:
        routes[f"projects/7/issues/{it['iid']}"] = dict(it, title="T", author={"username": "alice"}, state="opened")
        routes[f"projects/7/issues/{it['iid']}/notes"] = (notes or {}).get(it["iid"], [])
    return FakeGL(routes)


class Recorder:
    """Fake run. With gl given, it also leaves a fresh bot comment, like a real successful run."""

    def __init__(self, ok=True, gl=None):
        self.ok, self.jobs, self.gl = ok, [], gl

    def __call__(self, job, home, context):
        self.jobs.append(job)
        if self.gl is not None:
            key = f"projects/7/issues/{job.iid}/notes"
            self.gl.routes[key] = list(self.gl.routes.get(key, [])) + [
                {"id": 100 + len(self.jobs), "author": {"username": BOT}, "system": False, "body": "state",
                 "created_at": "2999-01-01T00:00:00Z"}]
        return RunResult(self.ok, 0.1, "s", "done")


def home():
    return Path(tempfile.mkdtemp(prefix="poll-"))


class TickTest(unittest.TestCase):
    def test_stop_file_exits(self):
        h = home()
        (h / "STOP").write_text("")
        self.assertEqual(poll.main(home=h, gl=gl_with([issue(1)]), run_fn=Recorder()), "stopped")

    def test_max_three_jobs_per_tick(self):
        rec = Recorder()
        poll.tick(gl_with([issue(i) for i in range(1, 6)]), State(), home(), rec, TODAY, BOT, G)
        self.assertEqual(len(rec.jobs), 3)

    def test_daily_cap_40(self):
        rec = Recorder()
        s = State(runs={TODAY.isoformat(): 39})
        poll.tick(gl_with([issue(i) for i in range(1, 6)]), s, home(), rec, TODAY, BOT, G)
        self.assertEqual(len(rec.jobs), 1)
        self.assertEqual(s.runs[TODAY.isoformat()], 40)

    def test_success_marks_seen_after_run(self):
        notes = {1: [{"id": 5, "author": {"username": "alice"}, "system": False, "body": "x", "created_at": "t"}]}
        s, gl = State(), gl_with([issue(1)], notes)
        poll.tick(gl, s, home(), Recorder(gl=gl), TODAY, BOT, G)
        self.assertEqual(s.seen["7:issue:1"], 101)
        rec = Recorder(gl=gl)
        poll.tick(gl, s, home(), rec, TODAY, BOT, G)
        self.assertEqual(rec.jobs, [])

    def test_failure_retried_then_comments_once(self):
        s, gl = State(), gl_with([issue(1)])
        for _ in range(3):
            poll.tick(gl, s, home(), Recorder(ok=False), TODAY, BOT, G)
        comments = [p for p in gl.posts if p[0] == "projects/7/issues/1/notes"]
        self.assertEqual(len(comments), 1)
        self.assertIn("нужен человек", comments[0][1]["body"])
        self.assertNotIn("done", comments[0][1]["body"])
        rec = Recorder(ok=False)
        poll.tick(gl, s, home(), rec, TODAY, BOT, G)
        self.assertEqual(rec.jobs, [])

    def test_lint_failure_creates_issue_once(self):
        gl = gl_with([], pipeline={"status": "failed", "sha": "abc", "web_url": "p"})
        s = State()
        poll.check_lint(gl, s, BOT, "acme/kb")
        poll.check_lint(gl, s, BOT, "acme/kb")
        created = [p for p in gl.posts if p[0] == "projects/7/issues"]
        self.assertEqual(len(created), 1)
        self.assertIn("kb-lint", created[0][1]["labels"])

    def test_dry_run_does_not_run(self):
        rec = Recorder()
        out = poll.tick(gl_with([issue(1)]), State(), home(), rec, TODAY, BOT, G, dry=True)
        self.assertEqual(rec.jobs, [])
        self.assertEqual(len(out), 1)


class SyncTest(unittest.TestCase):
    def test_sync_copies_only_agent_dir(self):
        h = home()
        src = h / "src" / "gitlab-agent"
        (src / "agent" / "skills" / "x").mkdir(parents=True)
        (src / "agent" / "skills" / "x" / "SKILL.md").write_text("skill")
        (src / "agent" / "CLAUDE.md").write_text("rules v2")
        (src / "gl.py").write_text("evil")
        (h / "app").mkdir()
        (h / "app" / "gl.py").write_text("good")
        poll.sync_agent_config(h, pull=False)
        self.assertEqual((h / "app/agent/skills/x/SKILL.md").read_text(), "skill")
        self.assertEqual((h / "work/CLAUDE.md").read_text(), "rules v2")
        self.assertEqual((h / "app/gl.py").read_text(), "good")
        import os
        self.assertFalse(os.access(h / "app/agent/skills/x/SKILL.md", os.W_OK))
        self.assertFalse(os.access(h / "app/agent", os.W_OK))
        (src / "agent" / "CLAUDE.md").write_text("rules v3")
        poll.sync_agent_config(h, pull=False)
        self.assertEqual((h / "work/CLAUDE.md").read_text(), "rules v3")


if __name__ == "__main__":
    unittest.main()


class SilentRunTest(unittest.TestCase):
    def test_run_without_bot_note_counts_as_failure(self):
        # The agent "succeeded" but nothing reached the issue (e.g. gl.py blocked): not handled, retried.
        s, gl = State(), gl_with([issue(1)])
        poll.tick(gl, s, home(), Recorder(ok=True), TODAY, BOT, G)
        self.assertNotIn("7:issue:1", s.seen)
        self.assertEqual(s.failures.get("acme/kb:issue:1"), 1)

    def test_run_with_fresh_bot_note_is_success(self):
        s = State()
        gl = gl_with([issue(1)])

        def rec(job, h, ctx):
            gl.routes["projects/7/issues/1/notes"] = [{"id": 9, "author": {"username": BOT}, "system": False,
                                                       "body": "x", "created_at": "t", "updated_at": "2999-01-01T00:00:00Z"}]
            return Recorder(ok=True)(job, h, ctx)
        poll.tick(gl, s, home(), rec, TODAY, BOT, G)
        self.assertEqual(s.seen.get("7:issue:1"), 9)
