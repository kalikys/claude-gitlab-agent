import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from state import State  # noqa: E402
from triggers import Job, issue_jobs, mr_jobs, mention_jobs, approved_jobs, dedupe, project_path  # noqa: E402

BOT = "claude-bot"
MEMBERS = {"alice": "K", "bob": "V", BOT: "Bot"}
URL = "https://gitlab.com/acme/kb/-/issues/3"


def issue(labels=(), assignees=(BOT,), iid=3):
    return {"iid": iid, "project_id": 7, "web_url": URL.replace("/3", f"/{iid}"), "labels": list(labels),
            "assignees": [{"username": a} for a in assignees]}


def note(id_, who, system=False):
    return {"id": id_, "author": {"username": who}, "system": system}


class StateTest(unittest.TestCase):
    def test_roundtrip(self):
        p = Path(tempfile.mkdtemp()) / "state.json"
        s = State.load(p)
        s.seen["7:issue:3"] = 12
        s.approved_done.append(5)
        s.save(p)
        s2 = State.load(p)
        self.assertEqual((s2.seen, s2.approved_done), ({"7:issue:3": 12}, [5]))


class IssueJobsTest(unittest.TestCase):
    def test_project_path(self):
        self.assertEqual(project_path(URL), "acme/kb")

    def test_new_assigned_issue_is_job(self):
        jobs = issue_jobs([issue()], {}, MEMBERS, BOT, State())
        self.assertEqual([(j.kind, j.project, j.iid) for j in jobs], [("issue", "acme/kb", 3)])

    def test_label_claude_is_job(self):
        self.assertEqual(len(issue_jobs([issue(labels=["claude"], assignees=())], {}, MEMBERS, BOT, State())), 1)

    def test_unrelated_issue_ignored(self):
        self.assertEqual(issue_jobs([issue(assignees=())], {}, MEMBERS, BOT, State()), [])

    def test_bot_own_note_no_job(self):
        s = State(seen={"7:issue:3": 10})
        jobs = issue_jobs([issue()], {(7, 3): [note(10, "alice"), note(11, BOT)]}, MEMBERS, BOT, s)
        self.assertEqual(jobs, [])

    def test_new_human_note_is_job(self):
        s = State(seen={"7:issue:3": 10})
        jobs = issue_jobs([issue()], {(7, 3): [note(11, BOT), note(12, "bob")]}, MEMBERS, BOT, s)
        self.assertEqual(len(jobs), 1)

    def test_system_note_ignored(self):
        s = State(seen={"7:issue:3": 10})
        self.assertEqual(issue_jobs([issue()], {(7, 3): [note(12, "alice", system=True)]}, MEMBERS, BOT, s), [])

    def test_non_member_note_ignored(self):
        s = State(seen={"7:issue:3": 10})
        self.assertEqual(issue_jobs([issue()], {(7, 3): [note(12, "stranger")]}, MEMBERS, BOT, s), [])

    def test_pause_label_skips(self):
        self.assertEqual(issue_jobs([issue(labels=["claude-pause"])], {}, MEMBERS, BOT, State()), [])


class OtherJobsTest(unittest.TestCase):
    def test_mr_new_human_note(self):
        mr = {"iid": 4, "project_id": 7, "web_url": "https://gitlab.com/acme/kb/-/merge_requests/4",
              "labels": []}
        jobs = mr_jobs([mr], {(7, 4): [note(30, "alice")]}, MEMBERS, BOT, State())
        self.assertEqual([(j.kind, j.iid) for j in jobs], [("mr", 4)])
        self.assertEqual(mr_jobs([mr], {(7, 4): [note(30, BOT)]}, MEMBERS, BOT, State()), [])

    def test_mention_todo(self):
        todos = [{"id": 1, "action_name": "mentioned", "target_type": "Issue", "author": {"username": "alice"},
                  "project": {"path_with_namespace": "acme/kb"}, "target": {"iid": 8}},
                 {"id": 2, "action_name": "marked", "target_type": "Issue", "author": {"username": "alice"},
                  "project": {"path_with_namespace": "acme/kb"}, "target": {"iid": 9}}]
        jobs = mention_jobs(todos, MEMBERS, BOT, "acme")
        self.assertEqual([(j.kind, j.iid, j.extra["todo_ids"]) for j in jobs], [("issue", 8, [1])])

    def test_mention_from_non_member_or_outside_group_ignored(self):
        base = {"action_name": "mentioned", "target_type": "Issue", "target": {"iid": 8}}
        todos = [dict(base, id=1, author={"username": "stranger"}, project={"path_with_namespace": "acme/kb"}),
                 dict(base, id=2, author={"username": "alice"}, project={"path_with_namespace": "evil/kb"})]
        self.assertEqual(mention_jobs(todos, MEMBERS, BOT, "acme"), [])

    def test_mention_and_assignment_deduplicated(self):
        a = Job("issue", "acme/kb", 3, "assigned", {})
        b = Job("issue", "acme/kb", 3, "mentioned", {"todo_ids": [1]})
        out = dedupe([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].extra["todo_ids"], [1])


class ApprovedTest(unittest.TestCase):
    def ev(self, id_, who, action="add", name="approved"):
        return {"id": id_, "action": action, "label": {"name": name}, "user": {"username": who},
                "created_at": "2026-10-24T12:00:00Z"}

    def test_approved_job_carries_approval_time(self):
        jobs = approved_jobs({("acme/kb", 3): [self.ev(50, "alice")]}, MEMBERS, BOT, State())
        self.assertEqual(jobs[0].extra["approved_at"], "2026-10-24T12:00:00Z")

    def test_approved_by_member(self):
        jobs = approved_jobs({("acme/kb", 3): [self.ev(50, "alice")]}, MEMBERS, BOT, State())
        self.assertEqual([(j.kind, j.extra["approver"], j.extra["event_id"]) for j in jobs], [("approved", "alice", 50)])

    def test_approved_by_bot_ignored(self):
        self.assertEqual(approved_jobs({("acme/kb", 3): [self.ev(50, BOT)]}, MEMBERS, BOT, State()), [])

    def test_approved_by_non_member_ignored(self):
        self.assertEqual(approved_jobs({("acme/kb", 3): [self.ev(50, "x")]}, MEMBERS, BOT, State()), [])

    def test_approved_processed_once(self):
        s = State(approved_done=[50])
        self.assertEqual(approved_jobs({("acme/kb", 3): [self.ev(50, "alice")]}, MEMBERS, BOT, s), [])

    def test_other_label_or_remove_ignored(self):
        evs = [self.ev(51, "alice", name="claude"), self.ev(52, "alice", action="remove")]
        self.assertEqual(approved_jobs({("acme/kb", 3): evs}, MEMBERS, BOT, State()), [])


if __name__ == "__main__":
    unittest.main()
