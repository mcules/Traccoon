# One interactive Claude Code session per (user, project).
#
# The deployer starts one container of this image for every person who opens a session in a
# project running in CLI mode (`projects.cli_mode`). One container per person and not one
# tmux window each in a shared one: the CLI has a shell, and in a shared container one
# session could read the config directory (and with it the subscription token) of another.
#
# Inside: tmux holds the session, so it outlives every browser tab; ttyd serves that tmux on
# port 7681 to the backend, which passes it on to the browser after checking who is asking.
# The port is never published — only the backend on the `traccoon-cli` network reaches it.
FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git openssh-client tmux ripgrep less procps python3 \
    && rm -rf /var/lib/apt/lists/*

# ttyd: Debian bookworm does not package it. The upstream static build, checked by hash.
ARG TTYD_VERSION=1.7.7
ARG TTYD_SHA256=8a217c968aba172e0dbf3f34447218dc015bc4d5e59bf51db2f2cd12b7be4f55
RUN curl -fsSL -o /usr/local/bin/ttyd \
        "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.x86_64" \
    && echo "${TTYD_SHA256}  /usr/local/bin/ttyd" | sha256sum -c - \
    && chmod +x /usr/local/bin/ttyd

# Pinned: a new CLI version is a deliberate rebuild, not something a container restart pulls in.
ARG CLAUDE_CODE_VERSION=2.1.283
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" && claude --version

ENV DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR=/cfg/claude TERM=xterm-256color LANG=C.UTF-8
COPY entrypoint.sh seed.js session.sh tmux.conf /opt/traccoon/
RUN chmod +x /opt/traccoon/entrypoint.sh /opt/traccoon/session.sh

EXPOSE 7681
ENTRYPOINT ["/opt/traccoon/entrypoint.sh"]
