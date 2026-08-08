# Qwen3-ASR auf der iGPU: llama.cpp mit Vulkan-Backend.
#
# Warum nicht whisper: gemessen am 2026-08-07 lieferte whisper.cpp auf derselben GPU zwar
# Tempo (0,7 s statt 3,1 s für 7 s Audio), verhunzte aber Eigennamen („Trakong" statt
# „Traccoon") — es gewichtet den Vokabel-Vorlauf schwächer als faster-whisper. Qwen3-ASR ist
# ein Sprachmodell mit Audio-Eingang: es versteht Kontext im Prompt, statt ihn nur als
# Wortliste zu streifen. Und es läuft über dieselbe ggml/Vulkan-Schiene wie der bereits
# laufende llama.cpp-RPC-Container auf diesem Chip.
#
# Quellen im Image statt im Repository — ein fremdes Projekt gehört nicht in unsere Historie.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    build-essential cmake git curl \
    libvulkan-dev vulkan-tools glslc glslang-tools spirv-headers spirv-tools \
    libcurl4-openssl-dev pkg-config ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG LLAMA_REF=master
WORKDIR /src
RUN git clone --depth 1 --branch ${LLAMA_REF} https://github.com/ggml-org/llama.cpp .

# `llama-server` bringt den Audio-Pfad (libmtmd) und die OpenAI-kompatible API mit.
# Shared Libs + `--install`, weil ein allein kopiertes Binär seine libggml nicht findet.
# Tests und Beispiele aus: `--install` wollte sonst Binärdateien einsammeln, die der
# gezielte Server-Build gar nicht erzeugt hat („cmake_install.cmake:52: file … does not exist").
RUN cmake -B build -DGGML_VULKAN=ON -DLLAMA_CURL=ON -DBUILD_SHARED_LIBS=ON \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=ON \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/app \
    && cmake --build build --config Release -j"$(nproc)" \
    && cmake --install build \
    && rm -rf /src/build
ENV LD_LIBRARY_PATH=/app/lib:/app/lib64

WORKDIR /app
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh
ENTRYPOINT ["/app/start.sh"]
