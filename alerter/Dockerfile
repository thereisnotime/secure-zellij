FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alerter.py .

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD kill -0 1 || exit 1

CMD ["uv", "run", "--no-dev", "python", "-u", "alerter.py"]
