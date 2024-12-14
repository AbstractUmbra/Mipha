ARG PYTHON_BASE=3.12-slim

FROM python:${PYTHON_BASE} AS builder

# disable update check since we're "static" in an image
ENV PDM_CHECK_UPDATE=false
# install PDM
RUN pip install -U pdm

WORKDIR /project
COPY . /project/
RUN apt-get update -y \
    && apt-get install --no-install-recommends --no-install-suggests -y git \
    && rm -rf /var/lib/apt/lists/*

RUN pdm install --check --prod --no-editable

FROM python:${PYTHON_BASE}

LABEL org.opencontainers.image.source=https://github.com/abstractumbra/mipha
LABEL org.opencontainers.image.description="Mipha Discord Bot"
LABEL org.opencontainers.image.licenses=MPL2.0

# install latest ffmpeg
RUN apt-get update -y \
    && apt-get install --no-install-recommends --no-install-suggests -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /project/.venv/ /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY . /app/

ENTRYPOINT [ "python", "-O", "bot.py" ]
