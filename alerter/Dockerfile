FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alerter.py .

CMD ["uv", "run", "--no-dev", "python", "-u", "alerter.py"]
