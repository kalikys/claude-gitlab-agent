import sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from gitlab import GitLab  # noqa: E402
from fakehttp import FakeHTTP  # noqa: E402
import gl as glcli  # noqa: E402
from urllib.parse import quote

G = "groups/acme"


def client(routes):
    http = FakeHTTP({("GET", "projects/acme%2Fkb"): {"id": 7, "path_with_namespace": "acme/kb"},
                     **routes})
    return GitLab("t", "acme", opener=http), http


class SearchTest(unittest.TestCase):
    def test_issues_group_wide(self):
        g, _ = client({("GET", f"{G}/search?scope=issues&search={quote('матрица')}"): [
            {"references": {"full": "acme/kb#1"}, "title": "Матрица", "state": "opened",
             "web_url": "u1"}]})
        out = glcli.search(g, "матрица", "issues")
        self.assertIn("acme/kb#1", out)
        self.assertIn("Матрица", out)

    def test_code_searches_each_project(self):
        g, http = client({("GET", f"{G}/projects?include_subgroups=true"): [
                              {"id": 7, "path_with_namespace": "acme/kb"},
                              {"id": 8, "path_with_namespace": "acme/x"}],
                          ("GET", "projects/7/search?scope=blobs&search=trivy"): [
                              {"path": "ci.yml", "startline": 3, "data": "  - trivy image app\\n"}],
                          ("GET", "projects/8/search?scope=blobs&search=trivy"): []})
        out = glcli.search(g, "trivy", "blobs")
        self.assertIn("acme/kb:ci.yml:3", out)
        self.assertIn("trivy image app", out)

    def test_project_scoped_notes(self):
        g, _ = client({("GET", f"projects/7/search?scope=notes&search={quote('решили')}"): [
            {"noteable_type": "Issue", "noteable_iid": 5, "author": {"username": "alice"}, "body": "решили так"}]})
        out = glcli.search(g, "решили", "notes", project="acme/kb")
        self.assertIn("Issue #5", out)
        self.assertIn("@alice", out)

    def test_nothing_found(self):
        g, _ = client({("GET", f"{G}/search?scope=merge_requests&search=zzz"): []})
        self.assertIn("ничего", glcli.search(g, "zzz", "merge_requests"))

    def test_bad_scope(self):
        g, _ = client({})
        with self.assertRaises(ValueError):
            glcli.search(g, "x", "users")


if __name__ == "__main__":
    unittest.main()


class SearchPrivacyTest(unittest.TestCase):
    def test_confidential_issues_hidden_and_team_private_skipped(self):
        g, http = client({("GET", f"{G}/search?scope=issues&search=x"): [
                              {"references": {"full": "acme/kb#1"}, "title": "open", "state": "opened",
                               "confidential": False},
                              {"references": {"full": "acme/team#2"}, "title": "secret",
                               "state": "opened", "confidential": True}],
                          ("GET", f"{G}/projects?include_subgroups=true"): [
                              {"id": 7, "path_with_namespace": "acme/kb"},
                              {"id": 9, "path_with_namespace": "acme/team"}],
                          ("GET", "projects/7/search?scope=blobs&search=x"): []})
        out = glcli.search(g, "x", "issues")
        self.assertIn("open", out)
        self.assertNotIn("secret", out)
        glcli.search(g, "x", "blobs")
        self.assertFalse(any(c[1].startswith("projects/9/") for c in http.calls))
        self.assertIn("данные", glcli.search(g, "x", "issues"))


class SearchPrivacy2Test(unittest.TestCase):
    def test_group_results_from_team_private_dropped(self):
        g, _ = client({("GET", f"{G}/search?scope=issues&search=x"): [
            {"references": {"full": "acme/team#3"}, "title": "anketa", "state": "opened",
             "confidential": False}]})
        self.assertNotIn("anketa", glcli.search(g, "x", "issues"))

    def test_notes_on_confidential_issue_dropped(self):
        g, _ = client({("GET", f"{G}/projects?include_subgroups=true"): [
                           {"id": 7, "path_with_namespace": "acme/kb"}],
                       ("GET", "projects/7/search?scope=notes&search=x"): [
                           {"noteable_type": "Issue", "noteable_iid": 5, "author": {"username": "a"}, "body": "hidden"},
                           {"noteable_type": "Issue", "noteable_iid": 6, "author": {"username": "a"}, "body": "visible"},
                           {"noteable_type": "Issue", "noteable_iid": 7, "author": {"username": "a"}, "body": "internal",
                            "internal": True}],
                       ("GET", "projects/7/issues/5"): {"confidential": True},
                       ("GET", "projects/7/issues/6"): {"confidential": False}})
        out = glcli.search(g, "x", "notes")
        self.assertIn("visible", out)
        self.assertNotIn("hidden", out)
        self.assertNotIn("internal", out)
