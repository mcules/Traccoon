#!/bin/sh
# Modell sicherstellen, dann den Server starten. Der Download läuft nur beim allerersten
# Start (danach liegt die Datei im Volume) — deshalb kein eigener Build-Schritt dafür: ein
# 3-GB-Modell gehört nicht ins Image, sondern neben die Daten.
set -e

MODELL="${WHISPER_MODEL:-large-v3}"
DATEI="/models/ggml-${MODELL}.bin"

if [ ! -f "$DATEI" ]; then
    echo "Modell ${MODELL} fehlt — wird geladen (einmalig, mehrere GB)…"
    sh /app/download-ggml-model.sh "$MODELL" /models
fi

echo "whisper.cpp startet mit ${DATEI} auf Vulkan"
exec /app/bin/whisper-server \
    --model "$DATEI" \
    --host 0.0.0.0 --port 9000 \
    --language "${WHISPER_LANG:-de}" \
    --threads "${WHISPER_THREADS:-8}" \
    ${WHISPER_EXTRA_ARGS:-}
