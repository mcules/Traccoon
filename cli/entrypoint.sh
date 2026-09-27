#!/bin/sh
# Start of a session container: prepare the config, start the tmux session, serve it.
#
# Environment (set by the deployer, see services/cli_sessions.py):
#   CLAUDE_CODE_OAUTH_TOKEN  the subscription token of THIS person
#   SESSION_WORKDIR          where claude works (the person's worktree or the project checkout)
#   TRACCOON_MCP_URL/_TOKEN  the way back into Traccoon (ticket tools)
#   GIT_NAME / GIT_EMAIL     the author of the commits made in here
set -e

mkdir -p "$CLAUDE_CONFIG_DIR"
node /opt/traccoon/seed.js

git config --global --add safe.directory '*'
[ -n "$GIT_NAME" ] && git config --global user.name "$GIT_NAME"
[ -n "$GIT_EMAIL" ] && git config --global user.email "$GIT_EMAIL"

# The session itself. Started detached; ttyd attaches every browser tab to the same one.
tmux -f /opt/traccoon/tmux.conf new-session -d -s main -x 200 -y 50 \
    -c "${SESSION_WORKDIR:-/workspace}" /opt/traccoon/session.sh

# -W: writable. Every connection runs its own `tmux attach`, all of them see one session.
exec ttyd -W -p 7681 -t disableLeaveAlert=true \
    tmux -f /opt/traccoon/tmux.conf attach -t main
