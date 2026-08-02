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

COPY --from=uv /uv /uvx /bin/
COPY infra/runtime/install-aws-signing-helper /tmp/install-aws-signing-helper
COPY dbt/analytics/pyproject.toml dbt/analytics/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

COPY dbt/analytics/dbt_project.yml dbt/analytics/profiles.yml ./
COPY dbt/analytics/packages.yml dbt/analytics/package-lock.yml ./
COPY dbt/analytics/models ./models
RUN --mount=type=cache,target=/tmp/.cache/dbt dbt deps \
    && /tmp/install-aws-signing-helper "$TARGETARCH" \
    && rm -f /bin/uv /bin/uvx /tmp/install-aws-signing-helper

RUN groupadd --gid 10001 dbt \
    && useradd --uid 10001 --gid dbt --create-home --shell /usr/sbin/nologin dbt \
    && chown -R dbt:0 /app \
    && chmod -R g=u /app

USER dbt
ENTRYPOINT ["dbt"]
CMD ["--help"]
