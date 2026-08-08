#!/bin/sh
# Modell und Audio-Projektor sicherstellen, dann den Server starten. Beide liegen im
# gemounteten /models und überleben jeden Neustart — ein 2-GB-Modell gehört neben die Daten,
# nicht ins Image.
set -e

REPO="${ASR_REPO:-ggml-org/Qwen3-ASR-1.7B-GGUF}"
QUANT="${ASR_QUANT:-Q8_0}"
MODELL="/models/Qwen3-ASR-1.7B-${QUANT}.gguf"
PROJ="/models/mmproj-Qwen3-ASR-1.7B-${QUANT}.gguf"

hol() {   # $1 = Dateiname im Repo, $2 = Ziel
    [ -f "$2" ] && return 0
    echo "Lade $1 …"
    curl -fL --retry 3 -o "$2.teil" \
        "https://huggingface.co/${REPO}/resolve/main/$1?download=true"
    mv "$2.teil" "$2"      # erst umbenennen, wenn vollständig — ein Abbruch soll keine
}                          # halbe Datei hinterlassen, die beim nächsten Start „da" wirkt

hol "Qwen3-ASR-1.7B-${QUANT}.gguf" "$MODELL"
hol "mmproj-Qwen3-ASR-1.7B-${QUANT}.gguf" "$PROJ"

echo "Qwen3-ASR startet auf Vulkan (${QUANT})"
exec /app/bin/llama-server \
    --model "$MODELL" \
    --mmproj "$PROJ" \
    --host 0.0.0.0 --port 9100 \
    --n-gpu-layers "${ASR_GPU_LAYERS:-99}" \
    --ctx-size "${ASR_CTX:-8192}" \
    ${ASR_EXTRA_ARGS:-}
