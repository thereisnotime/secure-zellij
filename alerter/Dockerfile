FROM ghcr.io/astral-sh/uv:0.13.9-python3.15-trixie-slim@sha256:980d6aa868431fd4ef2b32481e934ef5ee3b662c5decade3aa2d7e7ffb8075e4

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alerter.py .

RUN mkdir -p /data

EXPOSE 8083

CMD ["uv", "run", "--no-dev", "python", "-u", "alerter.py"]
