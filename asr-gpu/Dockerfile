# Qwen3-ASR on the iGPU: llama.cpp with the Vulkan backend.
#
# Why not whisper: measured on 2026-08-07, whisper.cpp on the same GPU did deliver speed (0.7 s
# instead of 3.1 s for 7 s of audio) but mangled proper nouns ("Trakong" instead of "Traccoon")
# — it weighs the vocabulary prompt more weakly than faster-whisper does. Qwen3-ASR is a
# language model with an audio input: it understands context in the prompt instead of merely
# brushing past it as a word list. And it runs over the same ggml/Vulkan rail as the llama.cpp
# RPC container already running on this chip.
#
# Sources in the image instead of in the repository — a foreign project does not belong in our
# history.
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

# `llama-server` brings the audio path (libmtmd) and the OpenAI compatible API along.
# Shared libs + `--install`, because a binary copied on its own does not find its libggml.
# Tests and examples off: `--install` would otherwise collect binaries that the targeted server
# build never produced ("cmake_install.cmake:52: file … does not exist").
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
