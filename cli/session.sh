#!/bin/bash
# What runs inside the tmux window. Claude is restarted when it ends, so the session (and with
# it the ticket delivery) does not quietly die when somebody types /exit. `--continue` picks up
# the last conversation of this working directory after a container restart.
cd "${SESSION_WORKDIR:-/workspace}" || cd /
while true; do
    claude --continue || claude
    echo
    echo "claude ended. Restarting in 5 s (Ctrl-C for a shell, 'exit' there to come back)."
    if ! sleep 5; then
        bash -l
    fi
done
