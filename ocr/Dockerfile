# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.11.30@sha256:93b61e21202b1dab861092748e46bbd6e0e41dd84f59b9174efd2353186e1b47 AS uv

FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock .python-version ./
COPY apps/arxiv_inspector/pyproject.toml ./apps/arxiv_inspector/pyproject.toml
COPY ocr/pyproject.toml ./ocr/pyproject.toml
COPY platform/pyproject.toml ./platform/pyproject.toml

FROM base AS platform-wheel
COPY platform/src ./platform/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --package lakehouse --out-dir /dist

FROM base AS ocr-wheel
COPY ocr/src ./ocr/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --package document-ocr --out-dir /dist

FROM base AS dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package document-ocr \
    --extra worker --no-install-workspace

FROM dependencies
ARG TARGETARCH
ENV HOME=/tmp
RUN groupadd --gid 10001 worker \
    && useradd --uid 10001 --gid worker --create-home --shell /usr/sbin/nologin worker
COPY --from=platform-wheel /dist /dist
COPY --from=ocr-wheel /dist /dist
COPY infra/runtime/install-aws-signing-helper /tmp/install-aws-signing-helper
COPY --chown=worker:worker platform/contracts ./platform/contracts
COPY --chown=worker:worker ocr/config ./ocr/config
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python --no-deps \
        /dist/lakehouse-*.whl /dist/document_ocr-*.whl \
    && /tmp/install-aws-signing-helper "$TARGETARCH" \
    && rm -rf /dist \
    && rm -f /bin/uv /bin/uvx /tmp/install-aws-signing-helper

USER worker
ENTRYPOINT ["document-ocr"]
CMD ["--help"]
