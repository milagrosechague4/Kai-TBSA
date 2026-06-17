# Kai Layer-2 company MCP — production image.
# uv-based, no DB. The company brain lives on a mounted volume at KAI_DATA_ROOT.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Faster, hermetic installs; don't try to manage Python itself.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# Install deps first (layer caching) — project code copied after.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# App code.
COPY . .
RUN uv sync --frozen --no-dev

# Bind to all interfaces; the brain defaults to the mounted volume.
# Auth stays fail-closed: the container needs KAI_TOKENS_JSON (or a tokens file)
# at runtime or it refuses to start.
ENV KAI_MCP_HOST=0.0.0.0 \
    KAI_DATA_ROOT=/data

EXPOSE 8080

# Railway injects $PORT; map it to the server's port (fallback 8080 locally).
CMD ["sh", "-c", "KAI_MCP_PORT=${PORT:-8080} exec uv run --no-dev kai-mcp-empresa"]
