FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git \
        # ffmpeg: Telegram delivers voice messages as OGG/Opus, and the audio path of
        # llama.cpp (miniaudio) cannot read that — it takes WAV/MP3/FLAC. Without a conversion
        # the ASR server answers with "Failed to load image or audio file".
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# codegraph: a code knowledge graph per worktree (saves agent tokens). A self-contained binary
# (no Node needed); symlinked into /usr/local/bin so it is callable regardless of the PATH.
RUN curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | sh \
    && ln -sf /root/.codegraph/versions/*/bin/codegraph /usr/local/bin/codegraph \
    && codegraph version
ENV CODEGRAPH_NO_DAEMON=1 CODEGRAPH_TELEMETRY=0 DO_NOT_TRACK=1

# node/npm for the build gate: `verify_command` runs as a shell INSIDE the worker, with the
# worktree as its working directory. Without node an agent cannot check in a TypeScript project
# whether its code compiles at all — the error would only show up at deploy time.
# Copied instead of packaged: Debian ships a node that is too old, and setting up a second
# package source would be more attack surface than three COPY lines. Node is built against an
# older glibc than the base image — that works, the other way round does not.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/entrypoint.sh

EXPOSE 8800
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8800"]
