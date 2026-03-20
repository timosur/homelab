FROM python:3.13-alpine

# Install SSH client and uv
RUN apk add --no-cache openssh-client
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY wol_proxy/ wol_proxy/
RUN uv sync --no-dev --frozen

# Create non-root user
RUN adduser -D -u 1000 appuser
USER appuser

ENTRYPOINT ["uv", "run", "python", "-m", "wol_proxy"]
CMD ["-config", "/config/config.yaml"]
