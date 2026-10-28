"""Deployment settings, read from the environment (see config.env.example).

GITLAB_GROUP    the only GitLab group the worker may touch
BOT_USERNAME    the bot's GitLab user (service account)
BOT_EMAIL       commit e-mail of the bot
KB_PROJECT      knowledge base project inside the group (the agent may push to its main after approval)
TEAM_PROJECT    private team data inside the group: never searched or shown from other projects
WORKER_PROJECT  this worker's own project (daily journal issue, schedule changes by merge request)
"""
import os


def group() -> str:
    return os.environ.get("GITLAB_GROUP", "acme")


def bot_name() -> str:
    return os.environ.get("BOT_USERNAME", "claude-bot")


def bot_email() -> str:
    return os.environ.get("BOT_EMAIL", f"{bot_name()}@users.noreply.gitlab.com")


def kb_project() -> str:
    return f"{group()}/{os.environ.get('KB_PROJECT', 'kb')}"


def team_project() -> str:
    return f"{group()}/{os.environ.get('TEAM_PROJECT', 'team')}"


def worker_project() -> str:
    return f"{group()}/{os.environ.get('WORKER_PROJECT', 'gitlab-agent')}"


def is_team(path: str) -> bool:
    """True for the private team project (by its project name, in any group)."""
    return path.rstrip("/").split("/")[-1] == team_project().split("/")[-1]


def owner() -> str:
    """Who gets the daily report pings and stuck-job alerts."""
    return os.environ.get("OWNER_USERNAME", "alice")
