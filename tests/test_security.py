"""Regression tests for the hardening of 2026-10-29: links planted by the agent, knowledge-base pushes,
document paths taken from issues, and the home directory gl.py trusts."""
import os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import gl as glcli  # noqa: E402
import poll  # noqa: E402
import runner  # noqa: E402
import schedule  # noqa: E402
from test_download import SECRET, client  # noqa: E402


def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Home(unittest.TestCase):
    def setUp(self):
        self.h = Path(tempfile.mkdtemp(prefix="glhome-")).resolve()
        (self.h / "work" / "jobs" / "j").mkdir(parents=True)
        self.outside = Path(tempfile.mkdtemp(prefix="outside-")).resolve()
        os.environ["AGENT_HOME"] = str(self.h)

    def tearDown(self):
        os.environ.pop("AGENT_HOME", None)
        os.environ.pop("GL_KB_GRANT", None)


class DownloadLinkTest(Home):
    def test_dangling_link_is_not_followed(self):
        """The agent plants work/jobs/j/report.pdf -> <outside>/planted; the download must not create it."""
        victim = self.outside / "planted"
        (self.h / "work/jobs/j/report.pdf").symlink_to(victim)
        g, _ = client()
        path = glcli.download(g, "acme/kb", f"/uploads/{SECRET}/report.pdf", str(self.h / "work/jobs/j"))
        self.assertFalse(victim.exists())
        self.assertEqual(Path(path).name, "report-1.pdf")
        self.assertEqual(Path(path).read_bytes(), b"%PDF-1.4 data")

    def test_link_to_existing_file_is_not_overwritten(self):
        victim = self.outside / "keep.txt"
        victim.write_text("original")
        (self.h / "work/jobs/j/report.pdf").symlink_to(victim)
        g, _ = client()
        glcli.download(g, "acme/kb", f"/uploads/{SECRET}/report.pdf", str(self.h / "work/jobs/j"))
        self.assertEqual(victim.read_text(), "original")

    def test_folder_link_is_refused(self):
        (self.h / "work/jobs/link").symlink_to(self.outside, target_is_directory=True)
        g, _ = client()
        with self.assertRaises((ValueError, OSError)):
            glcli.download(g, "acme/kb", f"/uploads/{SECRET}/report.pdf", str(self.h / "work/jobs/link"))
        self.assertEqual(list(self.outside.iterdir()), [])


class SwappedFolderTest(Home):
    """The path check passes, then the agent swaps a folder on the path for a link (a race with a background job).
    inside_work is patched to return the checked, unresolved path, as if the swap happened right after the check."""

    def swap(self):
        jobs = self.h / "work/jobs"
        (jobs / "j").rename(jobs / "j-old")
        (jobs / "j").symlink_to(self.outside, target_is_directory=True)
        return jobs / "j"

    def test_walk_refuses_swapped_component(self):
        d = self.swap()
        with mock.patch.object(glcli, "inside_work", lambda p: Path(p)):
            with self.assertRaises(OSError):
                glcli.open_work_dir(str(d))

    def test_body_file_never_read_through_a_link(self):
        secret = self.outside / "env"
        secret.write_text("GITLAB_TOKEN=x")
        (self.h / "work/jobs/j/body.md").symlink_to(secret)
        with self.assertRaises((ValueError, OSError)):
            glcli.read_body_file(str(self.h / "work/jobs/j/body.md"))
        d = self.swap()
        (self.outside / "body.md").write_text("secret")
        with mock.patch.object(glcli, "inside_work", lambda p: Path(p)):
            with self.assertRaises(OSError):
                glcli.read_body_file(str(d / "body.md"))


class BodyFileKindTest(Home):
    def test_hard_link_and_fifo_refused(self):
        secret = self.outside / "env"
        secret.write_text("GITLAB_TOKEN=x")
        os.link(secret, self.h / "work/jobs/j/hard.md")
        with self.assertRaises(ValueError):
            glcli.read_body_file(str(self.h / "work/jobs/j/hard.md"))
        os.mkfifo(self.h / "work/jobs/j/pipe.md")
        with self.assertRaises(ValueError):
            glcli.read_body_file(str(self.h / "work/jobs/j/pipe.md"))
        (self.h / "work/jobs/j/ok.md").write_text("hello")
        self.assertEqual(glcli.read_body_file(str(self.h / "work/jobs/j/ok.md")), "hello")


class KbGrantTest(Home):
    def test_expired_grant_is_refused(self):
        token = runner.issue_kb_grant(self.h)
        os.environ["GL_KB_GRANT"] = token
        f = self.h / "state/kb-grants" / token
        old = f.stat().st_mtime - glcli.GRANT_TTL - 60
        os.utime(f, (old, old))
        self.assertFalse(glcli.kb_grant_valid())


    def test_grant_needs_env_and_file(self):
        self.assertFalse(glcli.kb_grant_valid())
        token = runner.issue_kb_grant(self.h)
        self.assertFalse(glcli.kb_grant_valid())                 # file alone is not enough
        os.environ["GL_KB_GRANT"] = token
        self.assertTrue(glcli.kb_grant_valid())
        os.environ["GL_KB_GRANT"] = "../../etc/passwd"
        self.assertFalse(glcli.kb_grant_valid())                 # only a bare 32-hex token
        os.environ["GL_KB_GRANT"] = "0" * 32
        self.assertFalse(glcli.kb_grant_valid())                 # a token the worker never issued

    def test_grant_only_for_kb_write_kinds(self):
        self.assertEqual(set(runner.KB_WRITE_KINDS), {"approved", "inbox", "stale"})
        self.assertNotIn("GL_KB_GRANT", runner.agent_env(self.h))
        self.assertEqual(runner.agent_env(self.h, "a" * 32)["GL_KB_GRANT"], "a" * 32)

    def test_grant_removed_after_run(self):
        from triggers import Job
        seen = {}

        def fake_run(cmd, cwd, env, **kw):
            token = env.get("GL_KB_GRANT")
            seen["token"] = token
            seen["during"] = bool(token) and (self.h / "state/kb-grants" / token).is_file()
            return subprocess.CompletedProcess(cmd, 0, '{"is_error": false, "result": "ok"}', "")

        (self.h / "app/prompts").mkdir(parents=True)
        with mock.patch.object(runner, "APP_DIR", self.h / "app"), \
                mock.patch.object(runner, "runtime_mcp_config", lambda home: home / "mcp.json"), \
                mock.patch.object(runner.subprocess, "run", fake_run):
            for kind, granted in (("issue", False), ("approved", True)):
                (self.h / f"app/prompts/{kind}.md").write_text("p")
                runner.run(Job(kind, "acme/kb", 1, "x", {}), self.h, "", pull=False)
                self.assertEqual(seen["during"], granted, kind)
                if granted:
                    self.assertFalse((self.h / "state/kb-grants" / seen["token"]).exists())


class KbPushTest(Home):
    def setUp(self):
        super().setUp()
        self.remote = Path(tempfile.mkdtemp(prefix="remote-"))
        bare = self.remote / "acme" / "kb.git"
        bare.parent.mkdir(parents=True)
        git("init", "-q", "--bare", "-b", "main", str(bare))
        seed = Path(tempfile.mkdtemp())
        git("init", "-q", "-b", "main", cwd=seed)
        (seed / "CLAUDE.md").write_text("rules")
        (seed / "doc.md").write_text("v1")
        git("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=seed)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=seed)
        git("push", "-q", str(bare), "main", cwd=seed)
        self.base = f"file://{self.remote}"
        self.kb = glcli.clone_repo("acme/kb", str(self.h / "work/kb"), remote_base=self.base)

    def commit(self, name, text):
        (Path(self.kb) / name).parent.mkdir(parents=True, exist_ok=True)
        (Path(self.kb) / name).write_text(text)
        git("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=self.kb)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c", cwd=self.kb)

    def test_no_grant_no_main(self):
        self.commit("doc.md", "v2")
        with self.assertRaises(ValueError):
            glcli.push_repo(self.kb, "acme/kb", "main", remote_base=self.base)

    def test_granted_doc_change_goes_in(self):
        self.commit("doc.md", "v2")
        glcli.push_repo(self.kb, "acme/kb", "main", remote_base=self.base, kb_main=True)
        self.assertIn("c", git("log", "--format=%s", "-1", "main", cwd=self.remote / "acme/kb.git"))

    def test_granted_push_may_not_touch_the_prompt_or_tools(self):
        for name in ("CLAUDE.md", "scripts/kb.py", ".gitlab-ci.yml"):
            with self.subTest(name=name):
                self.commit(name, "injected")
                with self.assertRaises(ValueError):
                    glcli.push_repo(self.kb, "acme/kb", "main", remote_base=self.base, kb_main=True)
                git("reset", "-q", "--hard", "HEAD~1", cwd=self.kb)
        self.assertEqual(git("show", "main:CLAUDE.md", cwd=self.remote / "acme/kb.git"), "rules")

    def refused(self):
        with self.assertRaises(ValueError):
            glcli.push_repo(self.kb, "acme/kb", "main", remote_base=self.base, kb_main=True)
        git("reset", "-q", "--hard", "origin/main", cwd=self.kb)

    def test_rename_or_delete_of_the_prompt_is_refused(self):
        git("mv", "CLAUDE.md", "notes.md", cwd=self.kb)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "rename", cwd=self.kb)
        self.refused()
        git("rm", "-q", "CLAUDE.md", cwd=self.kb)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "delete", cwd=self.kb)
        self.refused()

    def test_only_documents_go_to_main(self):
        for name in ("scripts/скрипт.py", "docs/CLAUDE.md", "docs/claude.local.md", ".claude/skills/x/SKILL.md",
                     "CODEOWNERS", "taxonomy.yaml", "tests/x.md", "ci/include.yml"):
            with self.subTest(name=name):
                self.commit(name, "x")
                self.refused()
        self.commit("docs/new-doc.md", "ok")
        glcli.push_repo(self.kb, "acme/kb", "main", remote_base=self.base, kb_main=True)

    def test_link_disguised_as_document_is_refused(self):
        os.symlink("../../secrets/env", Path(self.kb) / "evil.md")
        git("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=self.kb)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "link", cwd=self.kb)
        self.refused()

    def test_cli_push_uses_the_grant(self):
        self.commit("doc.md", "v2")
        with mock.patch.object(glcli, "push_repo") as push:
            push.return_value = "sha"
            glcli.main(["push", self.kb, "--project", "acme/kb", "--branch", "main"], g=object())
            self.assertFalse(push.call_args.kwargs["kb_main"])
            os.environ["GL_KB_GRANT"] = runner.issue_kb_grant(self.h)
            glcli.main(["push", self.kb, "--project", "acme/kb", "--branch", "main"], g=object())
            self.assertTrue(push.call_args.kwargs["kb_main"])


class WriteFreshTest(Home):
    def test_replaces_link_without_touching_target(self):
        victim = self.outside / "victim"
        victim.write_text("keep")
        victim.chmod(0o644)
        target = self.h / "work/CLAUDE.md"
        target.symlink_to(victim)
        poll.write_fresh(target, b"rules")
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_bytes(), b"rules")
        self.assertEqual(victim.read_text(), "keep")
        self.assertTrue(os.access(victim, os.W_OK))
        self.assertFalse(os.access(target, os.W_OK))

    def test_replaces_folder(self):
        target = self.h / "work/CLAUDE.md"
        (target / "x").mkdir(parents=True)
        poll.write_fresh(target, b"rules")
        self.assertEqual(target.read_bytes(), b"rules")


class KbDocPathTest(Home):
    def test_paths(self):
        kb = self.h / "work/kb"
        (kb / "docs").mkdir(parents=True)
        self.assertTrue(schedule.kb_doc_ok(kb, "docs/a.md"))
        for bad in ("../app/x.md", "docs/../../x.md", "/etc/x.md", "-x.md", "--reason.md", ""):
            self.assertFalse(schedule.kb_doc_ok(kb, bad), bad)
        (kb / "docs/link.md").symlink_to(self.outside / "x.md")
        self.assertFalse(schedule.kb_doc_ok(kb, "docs/link.md"))

    def test_regex_does_not_pick_options(self):
        self.assertIsNone(schedule.DOC_PATH.search("see -x.md"))
        self.assertEqual(schedule.DOC_PATH.search("документ docs/a.md ждёт").group(0), "docs/a.md")

    def test_archive_refuses_bad_path_and_separates_args(self):
        kb = self.h / "work/kb"
        kb.mkdir(parents=True)
        calls = []
        with mock.patch.object(schedule.subprocess, "run",
                               lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 1)):
            self.assertFalse(schedule.archive_doc(self.h, "../x.md", "r", "u"))
            self.assertEqual(calls, [])
            schedule.archive_doc(self.h, "a.md", "r", "u")
        self.assertEqual(calls[0][-2:], ["--", "a.md"])


class HomeTest(unittest.TestCase):
    def test_deployed_gl_ignores_agent_home(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AGENT_HOME": "/"}):
            with mock.patch.object(glcli, "__file__", f"{d}/app/gl.py"):
                self.assertEqual(glcli.agent_home(), Path(d).resolve())


if __name__ == "__main__":
    unittest.main()
