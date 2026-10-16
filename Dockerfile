FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATCHRIGHT_CLI_MCP_HEADLESS=1 \
    PATCHRIGHT_CLI_MCP_ROOT=/patchright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY skills ./skills

RUN uv sync --frozen --extra mcp --no-dev

RUN uv run python -m patchright install --with-deps chrome \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /patchright/output /root/.patchright-cli

EXPOSE 9090

CMD ["uv", "run", "patchright-cli", "mcp", "--http", "--host=0.0.0.0", "--port=9090"]
