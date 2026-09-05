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
COPY arxiv-lens/pyproject.toml ./arxiv-lens/pyproject.toml
COPY lakehouse/catalog/pyproject.toml ./lakehouse/catalog/pyproject.toml
COPY ocr-engine/pyproject.toml ./ocr-engine/pyproject.toml
COPY t0-trading/pyproject.toml ./t0-trading/pyproject.toml
COPY t0-trading/config ./t0-trading/config
COPY t0-trading/src ./t0-trading/src
COPY infra/runtime/identity/install-aws-signing-helper /tmp/install-aws-signing-helper

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package t0-trading --no-editable \
    && /tmp/install-aws-signing-helper "$TARGETARCH" \
    && rm -f /usr/local/bin/uv /usr/local/bin/uvx /tmp/install-aws-signing-helper

RUN groupadd --gid 10001 t0 \
    && useradd --uid 10001 --gid t0 --create-home --shell /usr/sbin/nologin t0 \
    && chown -R t0:0 /app \
    && chmod -R g=u /app

USER t0
ENTRYPOINT ["t0-trading"]
CMD ["--help"]
