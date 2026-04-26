FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Dep-install layer — cached unless pyproject.toml or package root changes.
COPY pyproject.toml README.md ./
COPY nudgr ./nudgr
RUN uv pip install --system -e ".[dev]"

# Remaining source.
COPY . .

CMD ["bash"]
