"""Read worker secrets. The token lives in a file the agent may not read; it is not passed in Claude's environment."""
import os
from pathlib import Path


def secrets_path() -> Path:
    return Path(os.environ.get("AGENT_SECRETS", Path.home() / "gitlab-agent" / "secrets" / "env"))


def read_env_file(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("\"'")
    return out


def token() -> str:
    return os.environ.get("GITLAB_TOKEN") or read_env_file(secrets_path()).get("GITLAB_TOKEN", "")
