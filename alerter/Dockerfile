FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim@sha256:7cf77f594be8042dab6daa9fe326f90962252268b4f120a7f5dccce4d947e6c1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alerter.py .

RUN mkdir -p /data

EXPOSE 8083

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD kill -0 1 || exit 1

CMD ["uv", "run", "--no-dev", "python", "-u", "alerter.py"]
