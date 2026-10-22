# patchright-cli MCP server with real Google Chrome under a virtual display.
#
#   docker run --rm -p 8000:8000 --shm-size=1g \
#     -e PATCHRIGHT_CLI_MCP_TOKEN=change-me \
#     -v patchright-data:/data \
#     ghcr.io/ahaimk01/patchright-cli
#
# Google Chrome ships for linux/amd64 only, so the image is amd64-only.

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATCHRIGHT_CLI_NO_SKILL_CHECK=1 \
    PATCHRIGHT_CLI_NO_VERSION_CHECK=1 \
    PATCHRIGHT_CLI_MCP_ROOT=/data \
    DISPLAY=:99 \
    HOME=/home/app

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /usr/local/bin/uv

# Xvfb lets Chrome run headed (harder to detect than headless) without a real display.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb tini \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY skills ./skills
RUN uv pip install --system --no-cache ".[mcp]" \
    && patchright install --with-deps chrome \
    && rm -rf /var/lib/apt/lists/* /src

RUN useradd --create-home --home-dir /home/app --uid 1000 app \
    && mkdir -p /data /home/app/.patchright-cli \
    && chown -R app:app /data /home/app

COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh

USER app
WORKDIR /data
# /data: MCP file root (screenshots, uploads, state). Profiles: cookies and logins.
VOLUME ["/data", "/home/app/.patchright-cli"]
EXPOSE 8000

ENTRYPOINT ["tini", "--", "entrypoint.sh"]
CMD ["mcp", "--http", "--host=0.0.0.0", "--port=8000"]
