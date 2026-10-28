#!/bin/sh
# Git asks for HTTPS credentials. Answer only for gitlab.com, so the token never goes to another host.
# The token comes from the worker's environment or from the secrets file (the agent itself has neither).
secrets="${AGENT_SECRETS:-$HOME/gitlab-agent/secrets/env}"
from_file() { sed -n "s/^$1=//p" "$secrets" 2>/dev/null | head -1; }
token() {
  if [ -n "$GITLAB_TOKEN" ]; then echo "$GITLAB_TOKEN"; return; fi
  from_file GITLAB_TOKEN
}
bot="${BOT_USERNAME:-$(from_file BOT_USERNAME)}"
bot="${bot:-claude-bot}"
case "$1" in
  "Username for 'https://gitlab.com': ") echo "$bot" ;;
  "Password for 'https://$bot@gitlab.com': ") token ;;
  *) exit 1 ;;
esac
