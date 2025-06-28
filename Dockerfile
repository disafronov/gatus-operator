FROM ubuntu:noble-20250529 AS foundation

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

##########################

FROM foundation AS loader

# install wget and ca-certificates
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    apt-get update && \
    apt-get install -y --no-install-recommends wget ca-certificates

ARG HELM_VERSION=3.18.3

# Install Helm
RUN --mount=type=cache,target=/var/tmp \
    wget -O /var/tmp/helm.tar.gz https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz && \
    tar xzf /var/tmp/helm.tar.gz -C /tmp && \
    mv /tmp/linux-amd64/helm /usr/local/bin/ && \
    rm -rf /tmp/linux-amd64

##########################

FROM foundation AS base

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

# Install ca-certificates for SSL support
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates

# Copy Helm from loader stage
COPY --from=loader /usr/local/bin/helm /usr/local/bin/helm

# Copy Python from UV
COPY --from=builder /home/ubuntu/.local/share/uv/python/ /home/ubuntu/.local/share/uv/python/

# Copy app
COPY --from=builder --chown=ubuntu:ubuntu /home/ubuntu/app/ /home/ubuntu/app/

ENV PATH="/home/ubuntu/app/.venv/bin:$PATH"

ENTRYPOINT ["python3", "main.py"] 