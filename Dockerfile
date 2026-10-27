# Base images are pinned by multi-arch index digest; Dependabot (.github/dependabot.yml) bumps them.
# uv is a named stage because Dependabot only reads image references on FROM lines.
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

FROM python:3.12-slim@sha256:5b8b1421ccb6a525860a77863c049eab5847076cda34e030c75a80c58faf3f3c

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATCHRIGHT_CLI_MCP_HEADLESS=1 \
    PATCHRIGHT_CLI_MCP_ROOT=/patchright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --extra mcp --no-dev

RUN uv run --no-sync python -m patchright install --with-deps chrome \
    && rm -rf /var/lib/apt/lists/*

COPY README.md LICENSE ./
COPY src ./src
COPY skills ./skills
RUN uv sync --frozen --extra mcp --no-dev

RUN mkdir -p /patchright/output /root/.patchright-cli

EXPOSE 9090

ENTRYPOINT ["sh", "-c", "case \"$PATCHRIGHT_CLI_MODE\" in mcp-http) exec uv run patchright-cli mcp --http --host=0.0.0.0 --port=\"${PATCHRIGHT_CLI_MCP_PORT:-9090}\" ;; mcp-stdio) exec uv run patchright-cli mcp ;; \"\"|standalone) exec uv run patchright-cli \"$@\" ;; *) echo \"Unknown PATCHRIGHT_CLI_MODE: $PATCHRIGHT_CLI_MODE\" >&2; exit 2 ;; esac", "--"]
CMD ["--help"]
