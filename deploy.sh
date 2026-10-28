#!/bin/bash
# Deploys the worker over ssh to DEPLOY_HOST (an ssh alias), as DEPLOY_USER, into ~/gitlab-agent/app.
# Also vendors the knowledge base tool (KB_SCRIPTS, the scripts/ folder of your KB repo) into app/kbtool, so the agent never runs
# a kb.py it could have edited. Never touches secrets/, state/, logs/ or work/.
# app/ is left read-only: the agent runs as the same user and must not be able to change the worker.
# Usage: DEPLOY_HOST=agent-vm DEPLOY_USER=claude KB_SCRIPTS=../kb/scripts ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"
KB_SCRIPTS=${KB_SCRIPTS:-../kb/scripts}
DEPLOY_HOST=${DEPLOY_HOST:?set DEPLOY_HOST to the ssh alias of the worker host}
DEPLOY_USER=${DEPLOY_USER:-claude}
[ -f "$KB_SCRIPTS/kb.py" ] || { echo "no $KB_SCRIPTS/kb.py: set KB_SCRIPTS to your knowledge base tool" >&2; exit 1; }

stage=$(mktemp -d)
trap 'chmod -R u+w "$stage"; find "$stage" -delete' EXIT
COPYFILE_DISABLE=1 tar --no-xattrs -cf - --exclude __pycache__ --exclude .superpowers --exclude .git \
  ./*.py askpass.sh agent prompts systemd tests | tar -xf - -C "$stage"
mkdir -p "$stage/kbtool"
COPYFILE_DISABLE=1 tar --no-xattrs -cf - --exclude __pycache__ -C "$KB_SCRIPTS" . | tar -xf - -C "$stage/kbtool"

COPYFILE_DISABLE=1 tar --no-xattrs -czf - -C "$stage" . | ssh "$DEPLOY_HOST" "sudo -n -u $DEPLOY_USER bash -lc '
set -euo pipefail
cd ~/gitlab-agent
mkdir -p app state logs work
chmod -R u+w app
find app -mindepth 1 -delete
tar -xzf - -C app
chmod 755 app/askpass.sh
mkdir -p ~/.config/systemd/user
cp app/systemd/*.service app/systemd/*.timer ~/.config/systemd/user/
chmod -R a-w app
export XDG_RUNTIME_DIR=/run/user/\$(id -u)
systemctl --user daemon-reload
ls app | xargs echo deployed:
'"
