FROM ubuntu:noble-20250529 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Install wget and Helm
RUN apt-get update && \
    apt-get install -y wget && \
    wget https://get.helm.sh/helm-v3.18.3-linux-amd64.tar.gz -O - | tar xz && \
    mv linux-amd64/helm /usr/local/bin/ && \
    rm -rf linux-amd64 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

USER ubuntu:ubuntu
WORKDIR /home/ubuntu/app

##########################

FROM base AS builder

# Install dependencies with UV
RUN --mount=from=ghcr.io/astral-sh/uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/home/ubuntu/.cache/uv,uid=1000,gid=1000 \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --link-mode=copy --no-editable --no-dev

# Copy source
COPY --chown=ubuntu:ubuntu main.py /home/ubuntu/app/

##########################

FROM base AS runtime

# Copy Python from UV
COPY --from=builder /home/ubuntu/.local/share/uv/python/ /home/ubuntu/.local/share/uv/python/

# Copy app
COPY --from=builder --chown=ubuntu:ubuntu /home/ubuntu/app/ /home/ubuntu/app/

ENV PATH="/home/ubuntu/app/.venv/bin:$PATH"

ENTRYPOINT ["python3", "main.py"] 