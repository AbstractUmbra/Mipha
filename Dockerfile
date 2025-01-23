ARG PYTHON_BASE=3.12-slim

FROM python:${PYTHON_BASE} AS builder

# disable update check since we're "static" in an image
ENV PDM_CHECK_UPDATE=false
# install PDM
RUN pip install -U pdm

WORKDIR /project

RUN apt-get update -y \
    && apt-get install --no-install-recommends --no-install-suggests -y git \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/project/.venv/,sharing=locked \
    --mount=type=bind,source=pyproject.toml,target=/project/pyproject.toml,readwrite \
    --mount=type=bind,source=pdm.lock,target=/project/pdm.lock,readwrite \
    pdm install --check --prod --no-editable && \
    cp -R /project/.venv /project/.ready-venv

FROM python:${PYTHON_BASE}

LABEL org.opencontainers.image.source=https://github.com/abstractumbra/mipha
LABEL org.opencontainers.image.description="Mipha Discord Bot"
LABEL org.opencontainers.image.licenses=MPL2.0

# install latest ffmpeg
RUN apt-get update -y \
    && apt-get install --no-install-recommends --no-install-suggests -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

USER 1000:1000

WORKDIR /app

COPY --from=builder --chown=1000:1000 /project/.ready-venv/ /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY . /app/

CMD [ "python", "-O", "bot.py" ]
