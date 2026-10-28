import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gitlab import GitLab  # noqa: E402
from fakehttp import FakeHTTP  # noqa: E402


class GitLabTest(unittest.TestCase):
    def test_pagination_follows_next_page(self):
        http = FakeHTTP({
            ("GET", "groups/g/issues?state=opened"): ([{"iid": 1}], {"X-Next-Page": "2"}),
            ("GET", "groups/g/issues?page=2&state=opened"): ([{"iid": 2}], {"X-Next-Page": ""}),
        })
        gl = GitLab("t", "g", opener=http)
        self.assertEqual([i["iid"] for i in gl.get("groups/g/issues", state="opened")], [1, 2])

    def test_token_header_sent(self):
        seen = {}

        def opener(req, timeout=None):
            seen.update(req.headers)
            return FakeHTTP({("GET", "user"): {"username": "b"}})(req, timeout)
        GitLab("secret", "g", opener=opener).get("user")
        self.assertEqual(seen.get("Private-token"), "secret")

    def test_project_outside_group_rejected(self):
        gl = GitLab("t", "acme", opener=FakeHTTP({}))
        with self.assertRaises(ValueError):
            gl.project_id("other/x")
        with self.assertRaises(ValueError):
            gl.project_id("acme-evil/x")

    def test_project_id_inside_group(self):
        http = FakeHTTP({("GET", "projects/acme%2Fkb"): {"id": 7}})
        gl = GitLab("t", "acme", opener=http)
        self.assertEqual(gl.project_id("acme/kb"), 7)
        self.assertEqual(gl.project_id("acme/kb"), 7)
        self.assertEqual(len(http.calls), 1)

    def test_members_cached(self):
        http = FakeHTTP({("GET", "groups/g/members/all"): [{"username": "a", "name": "A"}]})
        gl = GitLab("t", "g", opener=http)
        self.assertEqual(gl.members(), {"a": "A"})
        gl.members()
        self.assertEqual(len(http.calls), 1)

    def test_post_sends_json(self):
        http = FakeHTTP({("POST", "projects/1/issues/2/notes"): {"id": 9}})
        out = GitLab("t", "g", opener=http).post("projects/1/issues/2/notes", {"body": "hi"})
        self.assertEqual(out["id"], 9)
        self.assertEqual(http.calls[0][3], {"body": "hi"})


if __name__ == "__main__":
    unittest.main()


class ResolvedProjectTest(unittest.TestCase):
    def test_resolved_project_outside_group_rejected(self):
        http = FakeHTTP({("GET", "projects/acme%2Fold"): {"id": 9, "path_with_namespace": "evil/x"}})
        gl = GitLab("t", "acme", opener=http)
        with self.assertRaises(ValueError):
            gl.project_id("acme/old")

    def test_resolved_case_insensitive(self):
        http = FakeHTTP({("GET", "projects/acme%2Fkb"): {"id": 7, "path_with_namespace": "acme/kb"}})
        self.assertEqual(GitLab("t", "acme", opener=http).project_id("acme/kb"), 7)
