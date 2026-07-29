FROM ghcr.io/astral-sh/uv:0.11.30 AS uv

FROM public.ecr.aws/amazonlinux/amazonlinux:2023-minimal@sha256:f536dd770d9f7065461c786418265a13f5ba548a11a57bbbf360e677b811ed39 AS build

WORKDIR /workspace
ENV UV_PYTHON=3.11 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

RUN dnf install -y python3.11 \
    && dnf clean all
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY ocr/pyproject.toml ./ocr/pyproject.toml
COPY src ./src
COPY contracts ./contracts
COPY jobs/emr/pyproject.toml jobs/emr/uv.lock ./jobs/emr/
COPY jobs/emr/src ./jobs/emr/src
COPY jobs/emr/entrypoints ./jobs/emr/entrypoints

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project jobs/emr --frozen --no-dev --group build --no-editable
RUN mkdir -p /output/entrypoints \
    && jobs/emr/.venv/bin/venv-pack -p jobs/emr/.venv -o /output/python.tar.gz \
    && cp jobs/emr/entrypoints/*.py /output/entrypoints/ \
    && jobs/emr/.venv/bin/python -m lakehouse_platform.contracts.bundle \
        --output /output/contracts.json

FROM scratch AS artifacts

COPY --from=build /output/ /
