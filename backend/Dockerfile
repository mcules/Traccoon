FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git \
        # ffmpeg: Telegram liefert Sprachnachrichten als OGG/Opus, der Audio-Pfad von
        # llama.cpp (miniaudio) kann das nicht — er nimmt WAV/MP3/FLAC. Ohne Umwandlung
        # antwortet der ASR-Server mit „Failed to load image or audio file".
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# codegraph: Code-Wissensgraph pro Worktree (spart Agent-Tokens). Self-contained Binary
# (kein Node nötig); Symlink nach /usr/local/bin, damit es unabhängig vom PATH aufrufbar ist.
RUN curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | sh \
    && ln -sf /root/.codegraph/versions/*/bin/codegraph /usr/local/bin/codegraph \
    && codegraph version
ENV CODEGRAPH_NO_DAEMON=1 CODEGRAPH_TELEMETRY=0 DO_NOT_TRACK=1

# node/npm für das Build-Gate: `verify_command` läuft als Shell IM Worker, mit dem Worktree
# als Arbeitsverzeichnis. Ohne node kann ein Agent bei einem TypeScript-Projekt nicht prüfen,
# ob sein Code überhaupt übersetzt — der Fehler fiele erst beim Deploy auf.
# Kopiert statt paketiert: Debian liefert ein zu altes node, und eine zweite Paketquelle
# einzurichten wäre mehr Angriffsfläche als drei COPY-Zeilen. Node ist gegen ein älteres
# glibc gebaut als das Basis-Image — das läuft, umgekehrt nicht.
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
