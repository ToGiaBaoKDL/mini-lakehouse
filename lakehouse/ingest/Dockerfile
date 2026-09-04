# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.11.30@sha256:93b61e21202b1dab861092748e46bbd6e0e41dd84f59b9174efd2353186e1b47 AS uv

FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ARG TARGETARCH
ENV HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock .python-version ./
COPY lakehouse/catalog/pyproject.toml ./lakehouse/catalog/pyproject.toml
COPY lakehouse/catalog/src ./lakehouse/catalog/src
COPY lakehouse/ingest/pyproject.toml ./lakehouse/ingest/pyproject.toml
COPY lakehouse/ingest/src ./lakehouse/ingest/src
COPY infra/runtime/identity/install-aws-signing-helper /tmp/install-aws-signing-helper

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package lakehouse-ingest --no-editable \
    && /tmp/install-aws-signing-helper "$TARGETARCH" \
    && rm -f /usr/local/bin/uv /usr/local/bin/uvx /tmp/install-aws-signing-helper

RUN groupadd --gid 10001 ingest \
    && useradd --uid 10001 --gid ingest --create-home --shell /usr/sbin/nologin ingest \
    && chown -R ingest:0 /app \
    && chmod -R g=u /app

USER ingest
ENTRYPOINT ["lakehouse-ingest"]
CMD ["--help"]
