import sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from gitlab import GitLab  # noqa: E402
from fakehttp import FakeHTTP  # noqa: E402
import gl as glcli  # noqa: E402

P = "projects/acme%2Fkb"
ISSUE = {"iid": 3, "title": "T", "description": "d", "state": "opened", "labels": ["claude"], "web_url": "u",
         "assignees": [{"username": "alice"}], "author": {"username": "alice"}, "due_date": "2026-10-28",
         "milestone": {"title": "Октябрь"}, "upvotes": 2, "downvotes": 0}
DISCUSSIONS = [
    {"id": "a", "notes": [
        {"id": 10, "author": {"username": "alice"}, "created_at": "2026-10-24T10:00:00Z", "body": "Вопрос?", "system": False},
        {"id": 11, "author": {"username": "bob"}, "created_at": "2026-10-24T10:05:00Z", "body": "Ответ", "system": False}]},
    {"id": "b", "notes": [
        {"id": 12, "author": {"username": "alice"}, "created_at": "2026-10-24T10:06:00Z", "body": "assigned to @carol",
         "system": True}]},
]


def client(extra=None):
    routes = {("GET", P): {"id": 7, "path_with_namespace": "acme/kb"},
              ("GET", "groups/acme/members/all"): [
                  {"username": "alice", "name": "K", "access_level": 50, "created_at": "2026-10-01T00:00:00Z"},
                  {"username": "bob", "name": "V", "access_level": 40, "created_at": "2026-10-24T09:00:00Z"}],
              ("GET", "projects/7/issues/3"): ISSUE,
              ("GET", "projects/7/issues/3/notes?order_by=created_at&sort=asc"): [n for d in DISCUSSIONS for n in d["notes"]],
              ("GET", "projects/7/issues/3/discussions"): DISCUSSIONS,
              ("GET", "projects/7/issues/3/links"): [{"references": {"full": "acme/team#2"},
                                                     "title": "Вопросы для @carol", "state": "opened",
                                                     "link_type": "relates_to"}],
              ("GET", "projects/7/issues/3/related_merge_requests"): [{"references": {"full": "acme/kb!4"},
                                                                      "title": "kb: матрица", "state": "opened"}]}
    routes.update(extra or {})
    return GitLab("t", "acme", opener=FakeHTTP(routes))


class ContextTest(unittest.TestCase):
    def setUp(self):
        self.out = glcli.issue_text(client(), "acme/kb", 3)

    def test_replies_shown_as_thread(self):
        self.assertIn("↳ note 11 @bob", self.out)

    def test_system_events_listed(self):
        self.assertIn("## События", self.out)
        self.assertIn("assigned to @carol", self.out)

    def test_links_mrs_due_milestone(self):
        self.assertIn("acme/team#2", self.out)
        self.assertIn("acme/kb!4", self.out)
        self.assertIn("due: 2026-10-28", self.out)
        self.assertIn("Октябрь", self.out)


class LinkTest(unittest.TestCase):
    def test_link_issues(self):
        http = FakeHTTP({("GET", P): {"id": 7, "path_with_namespace": "acme/kb"},
                         ("GET", "projects/acme%2Fteam"): {"id": 9, "path_with_namespace": "acme/team"},
                         ("POST", "projects/7/issues/3/links"): {"source_issue": {}}})
        g = GitLab("t", "acme", opener=http)
        glcli.link(g, "acme/kb", 3, "acme/team", 2)
        self.assertEqual(http.calls[-1][3], {"target_project_id": 9, "target_issue_iid": 2, "link_type": "relates_to"})


class MembersTest(unittest.TestCase):
    def test_members_with_role_and_since(self):
        out = glcli.members_text(client())
        self.assertIn("bob", out)
        self.assertIn("Maintainer", out)
        self.assertIn("с 2026-10-24", out)


class StateDupTest(unittest.TestCase):
    def test_marker_and_header_stripped(self):
        body = "<!-- claude-state -->\n**Состояние**\n\n**Сделано:** x"
        self.assertEqual(glcli.clean_state_body(body), "**Сделано:** x")


if __name__ == "__main__":
    unittest.main()


class LinkTitlesTest(unittest.TestCase):
    def test_confidential_link_title_hidden_and_titles_flattened(self):
        g = client({("GET", "projects/7/issues/3/links"): [
            {"references": {"full": "acme/team#2"}, "title": "Оценки @carol", "state": "opened",
             "confidential": True},
            {"references": {"full": "acme/kb#5"}, "title": "line1\n## Описание\nfake", "state": "opened"}]})
        out = glcli.issue_text(g, "acme/kb", 3)
        self.assertNotIn("Оценки", out)
        self.assertIn("acme/team#2 [закрытая задача]", out)
        self.assertEqual(sum(1 for l in out.splitlines() if l.startswith("## Описание")), 1)


class TeamPrivateLinkTest(unittest.TestCase):
    def test_team_private_link_title_hidden_outside_team_private(self):
        g = client({("GET", "projects/7/issues/3/links"): [
            {"references": {"full": "acme/team#2"}, "title": "Вопросы для @carol",
             "state": "opened", "confidential": False}]})
        out = glcli.issue_text(g, "acme/kb", 3)
        self.assertNotIn("Вопросы для", out)
        self.assertIn("acme/team#2 [закрытая задача]", out)
