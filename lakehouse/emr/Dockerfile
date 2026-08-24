# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.11.30@sha256:93b61e21202b1dab861092748e46bbd6e0e41dd84f59b9174efd2353186e1b47 AS uv

FROM public.ecr.aws/amazonlinux/amazonlinux:2023-minimal@sha256:f536dd770d9f7065461c786418265a13f5ba548a11a57bbbf360e677b811ed39 AS build

WORKDIR /workspace
ENV UV_PYTHON=3.11 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

RUN dnf install -y --setopt=install_weak_deps=0 python3.11 \
    && dnf clean all
COPY --from=uv /uv /usr/local/bin/uv
COPY lakehouse/catalog/pyproject.toml ./lakehouse/catalog/pyproject.toml
COPY lakehouse/catalog/src ./lakehouse/catalog/src
COPY lakehouse/contracts ./lakehouse/contracts
COPY lakehouse/emr/pyproject.toml lakehouse/emr/uv.lock ./lakehouse/emr/
COPY lakehouse/emr/src ./lakehouse/emr/src
COPY lakehouse/emr/entrypoints ./lakehouse/emr/entrypoints

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project lakehouse/emr --frozen --no-dev --group build --no-editable
RUN mkdir -p /output/entrypoints \
    && lakehouse/emr/.venv/bin/venv-pack -p lakehouse/emr/.venv -o /output/python.tar.gz \
    && cp lakehouse/emr/entrypoints/*.py /output/entrypoints/ \
    && lakehouse/emr/.venv/bin/python -m lakehouse.contracts.bundle \
        --output /output/contracts.json

FROM scratch AS artifacts

COPY --from=build /output/ /
