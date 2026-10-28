import os, unittest
from unittest import mock

import config


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(config.kb_project(), "acme/kb")
            self.assertEqual(config.team_project(), "acme/team")
            self.assertEqual(config.worker_project(), "acme/gitlab-agent")
            self.assertEqual(config.bot_email(), "claude-bot@users.noreply.gitlab.com")

    def test_overrides(self):
        env = {"GITLAB_GROUP": "corp", "KB_PROJECT": "wiki", "TEAM_PROJECT": "people", "BOT_USERNAME": "helper"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(config.kb_project(), "corp/wiki")
            self.assertTrue(config.is_team("corp/people"))
            self.assertFalse(config.is_team("corp/wiki"))
            self.assertEqual(config.bot_name(), "helper")


if __name__ == "__main__":
    unittest.main()
