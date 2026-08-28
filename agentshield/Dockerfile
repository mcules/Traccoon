FROM node:20-slim

# ecc-agentshield (security auditor for agent/Claude Code configurations), pinned. The tool
# is the only reason this container exists: it wants a binary and the configuration
# directories of the host, neither of which belongs inside the backend image.
RUN npm install -g ecc-agentshield@1.4.0 && npm cache clean --force

WORKDIR /app
COPY scanner.mjs /app/scanner.mjs

EXPOSE 8790
ENTRYPOINT ["node", "/app/scanner.mjs"]
