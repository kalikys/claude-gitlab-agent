import sys, unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from gitlab import GitLab  # noqa: E402
from fakehttp import FakeHTTP  # noqa: E402
import gl as glcli  # noqa: E402

G = "groups/acme"
SINCE = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc)
EVENTS7 = [
    {"created_at": "2026-10-24T10:00:00Z", "author_username": "alice", "action_name": "opened",
     "target_type": "Issue", "target_iid": 13, "target_title": "Матрица"},
    {"created_at": "2026-10-24T11:00:00Z", "author_username": "bob", "action_name": "commented on",
     "target_type": "Note", "note": {"body": "Согласен", "noteable_type": "Issue", "noteable_iid": 13}},
    {"created_at": "2026-10-24T12:00:00Z", "author_username": "carol", "action_name": "pushed to",
     "push_data": {"ref": "main", "commit_count": 2, "commit_title": "fix ci"}},
    {"created_at": "2026-10-20T12:00:00Z", "author_username": "old", "action_name": "opened",
     "target_type": "Issue", "target_iid": 1, "target_title": "Старое"},
]


def client():
    http = FakeHTTP({
        ("GET", f"{G}/projects?include_subgroups=true"): [
            {"id": 7, "path_with_namespace": "acme/devsecops-research"},
            {"id": 9, "path_with_namespace": "acme/team"}],
        ("GET", "projects/7/events?after=2026-10-22"): EVENTS7,
        ("GET", "projects/7/issues/13"): {"confidential": False},
        ("GET", "projects/7/issues/1"): {"confidential": False},
        ("GET", f"{G}/members/all"): [
            {"username": "newbie", "name": "N", "access_level": 30, "created_at": "2026-10-24T09:00:00Z"},
            {"username": "alice", "name": "K", "access_level": 50, "created_at": "2026-10-01T00:00:00Z"}]})
    return GitLab("t", "acme", opener=http), http


class ActivityTest(unittest.TestCase):
    def test_feed_covers_issues_comments_pushes_members(self):
        g, http = client()
        out = glcli.activity(g, SINCE)
        self.assertIn("@alice opened Issue #13 «Матрица»", out)
        self.assertIn("Согласен", out)
        self.assertIn("pushed to main (2 коммита): fix ci", out)
        self.assertIn("@newbie вступил(а) в группу", out)
        self.assertNotIn("Старое", out)
        self.assertFalse(any(c[1].startswith("projects/9/") for c in http.calls))   # team skipped

    def test_parse_since(self):
        now = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(glcli.parse_since("24h", now), datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(glcli.parse_since("7d", now).day, 17)
        self.assertEqual(glcli.parse_since("2026-10-20", now), datetime(2026, 10, 20, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()


class ConfidentialActivityTest(unittest.TestCase):
    def test_confidential_issue_events_redacted(self):
        events = [{"created_at": "2026-10-24T10:00:00Z", "author_username": "a", "action_name": "opened",
                   "target_type": "Issue", "target_iid": 20, "target_title": "Секретное"},
                  {"created_at": "2026-10-24T10:01:00Z", "author_username": "a", "action_name": "commented on",
                   "target_type": "Note", "note": {"body": "тайна", "noteable_type": "Issue", "noteable_iid": 20}}]
        http = FakeHTTP({("GET", f"{G}/projects?include_subgroups=true"): [
                             {"id": 7, "path_with_namespace": "acme/devsecops-research"}],
                         ("GET", "projects/7/events?after=2026-10-22"): events,
                         ("GET", "projects/7/issues/20"): {"confidential": True},
                         ("GET", f"{G}/members/all"): []})
        out = glcli.activity(GitLab("t", "acme", opener=http), SINCE)
        self.assertNotIn("Секретное", out)
        self.assertNotIn("тайна", out)
        self.assertIn("[закрытая задача #20]", out)


class ActivityHardeningTest(unittest.TestCase):
    def test_internal_notes_skipped_and_lines_flattened(self):
        events = [{"created_at": "2026-10-24T10:01:00Z", "author_username": "a", "action_name": "commented on",
                   "target_type": "Note", "note": {"body": "внутреннее", "internal": True, "noteable_type": "MergeRequest",
                                                   "noteable_iid": 2}},
                  {"created_at": "2026-10-24T10:02:00Z", "author_username": "a", "action_name": "opened",
                   "target_type": "MergeRequest", "target_iid": 3, "target_title": "x\n- 2026 fake line"}]
        http = FakeHTTP({("GET", f"{G}/projects?include_subgroups=true"): [
                             {"id": 7, "path_with_namespace": "acme/devsecops-research"}],
                         ("GET", "projects/7/events?after=2026-10-22"): events,
                         ("GET", f"{G}/members/all"): []})
        out = glcli.activity(GitLab("t", "acme", opener=http), SINCE)
        self.assertNotIn("внутреннее", out)
        self.assertEqual(sum(1 for l in out.splitlines() if l.startswith("- ")), 1)


class FailClosedTest(unittest.TestCase):
    def test_lookup_error_treated_as_confidential(self):
        events = [{"created_at": "2026-10-24T10:00:00Z", "author_username": "a", "action_name": "opened",
                   "target_type": "Issue", "target_iid": 21, "target_title": "Неизвестно"}]
        http = FakeHTTP({("GET", f"{G}/projects?include_subgroups=true"): [
                             {"id": 7, "path_with_namespace": "acme/devsecops-research"}],
                         ("GET", "projects/7/events?after=2026-10-22"): events,
                         ("GET", f"{G}/members/all"): []})          # no route for projects/7/issues/21 -> error
        out = glcli.activity(GitLab("t", "acme", opener=http), SINCE)
        self.assertNotIn("Неизвестно", out)
        self.assertIn("[закрытая задача #21]", out)
