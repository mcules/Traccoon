# whisper.cpp mit Vulkan — Spracherkennung auf der iGPU (Radeon 8060S, Strix Halo).
#
# Warum nicht faster-whisper: dessen Unterbau CTranslate2 kann ausschließlich CUDA, auf einer
# AMD-Karte bleibt nur die CPU. whisper.cpp bringt mit ggml einen Vulkan-Pfad mit und läuft
# damit auf genau der GPU, die hier ohnehin im Gehäuse steckt. Bauweise 1:1 wie der bereits
# laufende `rpc-vulkan-8060s-ai1` (llama.cpp) — dieselben Pakete, dieselbe Geräte-Einbindung.
#
# Die Quellen werden IM Image geholt statt ins Repository gelegt: ein fremdes Projekt gehört
# nicht in unsere Historie, und die Version steht nachvollziehbar als Argument hier.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    build-essential cmake git curl \
    libvulkan-dev vulkan-tools glslc glslang-tools spirv-headers spirv-tools \
    pkg-config ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG WHISPER_REF=master
WORKDIR /src
RUN git clone --depth 1 --branch ${WHISPER_REF} https://github.com/ggml-org/whisper.cpp .

RUN cmake -B build -DGGML_VULKAN=ON -DWHISPER_BUILD_SERVER=ON -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=ON -DCMAKE_INSTALL_PREFIX=/app \
    && cmake --build build --config Release -j"$(nproc)" \
    # `--install` statt „Binär kopieren": whisper.cpp baut gegen libwhisper/libggml, und ein
    # allein kopiertes Binär startet nicht („libwhisper.so.1: cannot open shared object file").
    && cmake --install build \
    && cp models/download-ggml-model.sh /app/ \
    && rm -rf /src/build
ENV LD_LIBRARY_PATH=/app/lib:/app/lib64

WORKDIR /app
# Modell beim Start holen, falls es im Volume fehlt (sonst startet der Server ins Leere).
# Der Download liegt im gemounteten /models und überlebt jeden Neustart.
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh
ENTRYPOINT ["/app/start.sh"]
