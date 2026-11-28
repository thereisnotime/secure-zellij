FROM ghcr.io/astral-sh/uv:0.13.10-python3.15-trixie-slim@sha256:3ee10471b12b57f6f86d03f68c44fbd4bb8d5540efa25ab3b89159e1216b1a70

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alerter.py .

RUN mkdir -p /data

EXPOSE 8083

CMD ["uv", "run", "--no-dev", "python", "-u", "alerter.py"]
