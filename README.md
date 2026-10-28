# claude-gitlab-agent

Claude Code as a sandboxed member of a GitLab team. A small Python worker watches one GitLab group, and when an
issue, a merge request or a mention needs the bot, it starts a headless `claude -p` run inside Claude Code's OS
sandbox. The agent plans, asks questions, writes code in its own branches and opens merge requests. It never merges,
never sees the GitLab token, and talks to GitLab only through a narrow broker script.

It runs in production for a small team since October 2026 (one service account, one group, a knowledge-base
repository and a few work repositories). This repository is a cleaned-up, configurable copy of that worker.

Prompts and skills are in Russian, the language of the team it was built for; translate `prompts/` and
`agent/skills/` for your team. Code, configuration and this README are in English.

## How it works

```
systemd timers ──> poll.py ───────────────┐            (every 30 s after the previous poll)
                   schedule.py <kind> ────┤            (inbox / weekly / remind / report)
                                          v
                   triggers.py: what needs the agent (assigned / label `claude` / mention / MR review / `approved`)
                                          v
                   runner.py: fresh clones of kb + team, job folder, minimal env, sandbox settings
                                          v
                   claude -p  ── Bash ──> bubblewrap sandbox: no network (package registries only),
                      │                   no reads of secrets/, state/, ~/.claude, /proc; no writes to the worker
                      └─ Read/Edit/Write  permission rules deny the same paths
                                          v
                   gl.py (the only command allowed outside the sandbox): GitLab API and git over HTTPS,
                   token from the secrets file, group-scoped, argument-checked
```

- `gitlab.py`: tiny stdlib REST client; every project must resolve inside the configured group.
- `gl.py`: the broker the agent calls (`issue`, `mr`, `comment`, `state`, `new-issue`, `new-mr`, `label`,
  `members`, `activity`, `search`, `pipeline`, `job-log`, `mr-diff`, `mr-line-comment`, `download`, `clone`, `push`).
- `triggers.py`: pure functions that turn GitLab data into jobs; `state.py`: what is already handled.
- `runner.py`: builds the `claude -p` command, settings and environment, logs every run to `logs/<date>.jsonl`.
- `poll.py`: the main loop with limits, failure counting and escalation; `schedule.py`: timed jobs.
- `recurring.py`: issues opened on a schedule from `agent/recurring.json`.
- `agent/`: the agent's own configuration (`CLAUDE.md`, `mcp.json`, skills). It is read from the worker project's
  `main` branch, so a human approves every change to the agent's rules through a merge request. The worker's code
  changes only by `deploy.sh`.

## What triggers a run

| When | What |
|---|---|
| every 30 s after the previous poll (`poll.py`) | issues assigned to the bot or labelled `claude`, mentions of the bot, review notes on the bot's MRs, the `approved` label, recurring tasks |
| daily 20:00 (`schedule.py inbox`) | sort the knowledge base `inbox/` |
| Monday 09:00 (`schedule.py weekly`) | review reminders, staleness check, weekly digest of the knowledge base |
| weekdays 10:00 (`schedule.py remind`) | remind people on `claude-waiting` issues, nudge before and after due dates |
| daily 21:00 (`schedule.py report`) | daily report in a journal issue: runs, estimated cost, failures |

Only group members can trigger the bot; notes from anyone else are ignored.

## Guardrails and threat model

The working assumption: anything the agent reads (issues, comments, attachments, web pages, repository files) can
contain a prompt injection, and the agent can be talked into anything it is technically able to do. So the limits
are in the worker, not in the prompt.

| The agent cannot | Because |
|---|---|
| read or leak the GitLab token | the token is not in Claude's environment; `secrets/` is denied to the sandbox and to the Read tool; only `gl.py` and `askpass.sh` read it |
| reach the network from the shell | sandbox network is an allowlist of package registries; `allowUnsandboxedCommands: false`, `failIfUnavailable: true` |
| touch another group or host | `gl.py` and `gitlab.py` check the group and the resolved project path; `askpass.sh` answers only for `https://gitlab.com` |
| merge, or push to protected branches | `gl.py push` allows only `claude/*` branches. `main` of the knowledge base only in runs the worker granted (approved / inbox / stale): a one-run token in the run's environment that must match a file in `state/`, which the sandbox can neither read nor write |
| rewrite its own system prompt or tools | the knowledge base's `CLAUDE.md` is appended to every run's system prompt, so a push straight to `main` may never change `CLAUDE.md`, `scripts/`, `.gitlab-ci.yml` or `.gitlab/`; those change only through a merge request |
| escape through links it plants | `gl.py` runs outside the sandbox but never follows a link inside `work/`: downloads are created with `O_EXCL \| O_NOFOLLOW` relative to a folder opened with `O_NOFOLLOW`; paths are resolved before checks; the worker replaces `work/CLAUDE.md` instead of writing through it |
| move the boundaries `gl.py` checks | deployed as `<home>/app/gl.py`, it takes its home from its own location, not from `AGENT_HOME` |
| change its own code or rules | the worker is deployed read-only; `agent/` comes from `main` of the worker project, i.e. after a human merge |
| run hooks or git config while the token is in use | pushes go through a clean temporary bare repo with system/global config, hooks and credential helpers disabled |
| write outside its job folder with `gl.py` | body and target files must resolve inside `~/gitlab-agent/work` |
| spend without bound | at most 3 runs per poll, 40 per day, 30 minutes per run, `DAILY_BUDGET_USD` per day (unknown cost counts as $2) |
| retry a broken job forever | after 3 failures in a row the job stops and the bot asks for a human in the issue |
| surface private team data elsewhere | the team project is excluded from group-wide search and activity; titles of its items and of confidential items are hidden outside it |

What is policy rather than enforcement, and you should know it: inside a granted run the agent decides what it
writes to the knowledge base's documents. Results of issue work go in only after a group member adds the
`approved` label (that is what starts the granted run), and every commit carries `Approved-by` or `Source:` with a
link, so changes are auditable. Nightly inbox sorting and the staleness check write without a label, by design.
Protect `main` of the knowledge base in GitLab if you want every change to go through review.

`tests/test_security.py` pins these guardrails; the link-following download it covers was a real sandbox escape in
an earlier version (found by an automated review and fixed before this release).

Stop switches: `touch ~/gitlab-agent/STOP` pauses everything; the label `claude-pause` leaves one issue or MR alone.

## Setup

1. **GitLab**: a bot user in one group (a group service account works well) with an access token, scope `api`.
   Projects in the group: a knowledge base (`kb`), a private team project (`team`), and your copy of this worker
   (`gitlab-agent`), where `agent/CLAUDE.md` lives (start from `agent/CLAUDE.md.example`). The knowledge base is
   expected to have its own `CLAUDE.md` and a `scripts/kb.py` tool; `deploy.sh` vendors that tool into the worker.
2. **Host**: Linux with Python 3.12+, git, bubblewrap and Claude Code (`claude` in `~/.local/bin`), a dedicated
   Unix user (e.g. `claude`) with lingering enabled for systemd user units. If the host restricts unprivileged user
   namespaces (Ubuntu 24.04+), install the profile from `apparmor/bwrap.example`.
3. **Secrets**: copy `secrets.env.example` to `~/gitlab-agent/secrets/env`, `chmod 600`, fill it in.
4. **Deploy**: `DEPLOY_HOST=<ssh alias> DEPLOY_USER=claude KB_SCRIPTS=../kb/scripts ./deploy.sh`.
   It unpacks the worker into `~/gitlab-agent/app` (read-only) and installs the systemd user units.
5. **Start**: as that user, `systemctl --user enable --now gitlab-agent-poll.timer gitlab-agent-inbox.timer
   gitlab-agent-weekly.timer gitlab-agent-remind.timer gitlab-agent-report.timer`.

Dry run without starting Claude: `GITLAB_AGENT_DRY=1 python3 ~/gitlab-agent/app/poll.py`.
Logs: `~/gitlab-agent/logs/<date>.jsonl`, `journalctl --user -u gitlab-agent-poll`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GITLAB_GROUP` | `acme` | the only group the worker may touch |
| `BOT_USERNAME` / `BOT_EMAIL` | `claude-bot` / `<bot>@users.noreply.gitlab.com` | bot identity for GitLab and commits |
| `KB_PROJECT`, `TEAM_PROJECT`, `WORKER_PROJECT` | `kb`, `team`, `gitlab-agent` | project names inside the group |
| `OWNER_USERNAME` | `alice` | who is pinged in the daily report and about stuck jobs |
| `CLAUDE_MODEL`, `CLAUDE_MODEL_ROUTINE` | default, `sonnet` | models for work with people and for routine chores |
| `DAILY_BUDGET_USD` | `15` | estimated daily spend limit |
| `AGENT_HOME`, `AGENT_SECRETS`, `CLAUDE_BIN` | `~/gitlab-agent`, `<home>/secrets/env`, `~/.local/bin/claude` | paths |

## Tests

```sh
python3 -m pytest -q          # or: python3 -m unittest discover -s tests
```

The tests use fake GitLab and HTTP clients and a fake `claude`; `tests/test_sandbox.py` and
`tests/test_hardening.py` cover the path, branch, group and git hardening. `tests/sandbox_probe.py` is a manual
probe to run inside a real sandboxed session.

## License

MIT, see `LICENSE`.
