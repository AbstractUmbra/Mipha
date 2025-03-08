ARG PYTHON_BASE=3.12-slim-bookworm
ARG UV_BASE=python3.12-bookworm-slim

FROM ghcr.io/astral-sh/uv:${UV_BASE} AS builder

ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /project
RUN apt-get update -y \
    && apt-get install --no-install-recommends --no-install-suggests -y git \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=/project/pyproject.toml \
    --mount=type=bind,source=uv.lock,target=/project/uv.lock \
    uv sync --frozen --no-install-project --no-dev --group minecraft

ADD --chown=1000:1000 . /project

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --group minecraft

FROM python:${PYTHON_BASE}

LABEL org.opencontainers.image.source=https://github.com/abstractumbra/mipha
LABEL org.opencontainers.image.description="Mipha Discord Bot"
LABEL org.opencontainers.image.licenses=MPL2.0

# install latest ffmpeg
RUN apt-get update -y \
    && apt-get upgrade -y \
    && apt-get install --no-install-recommends --no-install-suggests -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

USER 1000:1000

WORKDIR /app

COPY --from=builder --chown=1000:1000 /project /app
ENV PATH="/app/.venv/bin:$PATH"

CMD [ "python", "-O", "bot.py" ]
