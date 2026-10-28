FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends rclone ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.7.13 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY rules.yaml config.example.yaml ./
ENV PATH="/app/.venv/bin:$PATH" LPA_CONFIG=/config/config.yaml
CMD ["local-paper-ai", "run"]
