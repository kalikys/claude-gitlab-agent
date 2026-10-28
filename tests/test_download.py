import os, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from gitlab import GitLab  # noqa: E402
from fakehttp import FakeHTTP  # noqa: E402
import gl as glcli  # noqa: E402

SECRET = "cc2fdcb6ff086bc718296e088d06dad8"
P = "projects/acme%2Fkb"


def client():
    http = FakeHTTP({("GET", P): {"id": 7, "path_with_namespace": "acme/kb"},
                     ("GET", f"projects/7/uploads/{SECRET}/report.pdf"): b"%PDF-1.4 data",
                     ("GET", "groups/acme/members/all"): [{"username": "alice", "name": "K"}]})
    return GitLab("t", "acme", opener=http), http


class ParseTest(unittest.TestCase):
    def test_parse_markdown_url(self):
        self.assertEqual(glcli.parse_upload(f"/uploads/{SECRET}/report.pdf"), (None, SECRET, "report.pdf"))

    def test_parse_full_url(self):
        self.assertEqual(glcli.parse_upload(f"https://gitlab.com/-/project/7/uploads/{SECRET}/a%20b.pdf"),
                         ("7", SECRET, "a b.pdf"))

    def test_parse_bad(self):
        with self.assertRaises(ValueError):
            glcli.parse_upload("https://evil.example/x.pdf")


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.h = Path(tempfile.mkdtemp())
        (self.h / "work" / "jobs" / "j").mkdir(parents=True)
        os.environ["AGENT_HOME"] = str(self.h)

    def tearDown(self):
        os.environ.pop("AGENT_HOME")

    def test_download_into_work(self):
        g, _ = client()
        out = glcli.download(g, "acme/kb", f"/uploads/{SECRET}/report.pdf", str(self.h / "work/jobs/j"))
        self.assertEqual(Path(out).read_bytes(), b"%PDF-1.4 data")
        self.assertEqual(Path(out).parent, (self.h / "work/jobs/j").resolve())

    def test_download_outside_work_refused(self):
        g, _ = client()
        with self.assertRaises(ValueError):
            glcli.download(g, "acme/kb", f"/uploads/{SECRET}/report.pdf", str(self.h))

    def test_project_id_mismatch_refused(self):
        g, _ = client()
        with self.assertRaises(ValueError):
            glcli.download(g, "acme/kb", f"https://gitlab.com/-/project/999/uploads/{SECRET}/report.pdf",
                           str(self.h / "work/jobs/j"))

    def test_filename_cannot_escape(self):
        self.assertEqual(glcli.safe_name("../../.bashrc"), "bashrc")
        self.assertEqual(glcli.safe_name("отчёт 2026.pdf"), "отчёт 2026.pdf")


class ListAttachmentsTest(unittest.TestCase):
    def test_issue_text_lists_attachments(self):
        issue = {"iid": 3, "title": "T", "description": f"см. [report.pdf](/uploads/{SECRET}/report.pdf)",
                 "state": "opened", "labels": [], "assignees": [], "author": {"username": "alice"}, "web_url": "u"}
        http = FakeHTTP({("GET", P): {"id": 7, "path_with_namespace": "acme/kb"},
                         ("GET", "groups/acme/members/all"): [{"username": "alice", "name": "K"}],
                         ("GET", "projects/7/issues/3"): issue,
                         ("GET", "projects/7/issues/3/notes?order_by=created_at&sort=asc"): []})
        out = glcli.issue_text(GitLab("t", "acme", opener=http), "acme/kb", 3)
        self.assertIn("## Вложения", out)
        self.assertIn(f"/uploads/{SECRET}/report.pdf", out)


class TraversalTest(unittest.TestCase):
    def test_name_with_slash_or_dots_refused(self):
        for ref in (f"/uploads/{SECRET}/..%2F..%2Fgroups%2Fx", f"/uploads/{SECRET}/a%2Fb.pdf", f"/uploads/{SECRET}/..",
                    f"/uploads/{SECRET}/a%5Cb.pdf"):
            with self.assertRaises(ValueError, msg=ref):
                glcli.parse_upload(ref)
