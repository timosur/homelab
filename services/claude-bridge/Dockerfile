FROM python:3.14-slim-bookworm AS bridge

ARG CLAUDE_BRIDGE_REF=b94bf404c6c004d34a8f1d7683aea121deef19dc

RUN apt-get update && apt-get install --yes --no-install-recommends git \
    && pip install --no-cache-dir "git+https://github.com/tivaliy/claude-bridge.git@${CLAUDE_BRIDGE_REF}" \
    && rm -rf /var/lib/apt/lists/*

FROM node:22-bookworm-slim AS claude-code

ARG CLAUDE_CODE_VERSION=2.1.296

RUN npm install --global "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"

FROM python:3.14-slim-bookworm

COPY --from=bridge /usr/local /usr/local
COPY --from=claude-code /usr/local/bin/claude /usr/local/bin/claude
COPY --from=claude-code /usr/local/bin/node /usr/local/bin/node
COPY --from=claude-code /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN groupadd --gid 1000 claude \
    && useradd --uid 1000 --gid claude --create-home --home-dir /home/claude claude

USER 1000:1000
ENV HOME=/home/claude

EXPOSE 8000
CMD ["claude-bridge"]
