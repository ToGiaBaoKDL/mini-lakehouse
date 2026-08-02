# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.11.30@sha256:93b61e21202b1dab861092748e46bbd6e0e41dd84f59b9174efd2353186e1b47 AS uv

FROM uv AS requirements
WORKDIR /build
COPY orchestration/pyproject.toml orchestration/uv.lock ./
RUN ["/uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--output-file", "requirements.txt"]

FROM apache/airflow:3.3.0-python3.12@sha256:96e99f25815f533b298a4d53f283adf5c84c27334ea16ef232777cb800bddf10

ARG TARGETARCH

ENV PYTHONPATH=/opt/airflow

USER root
COPY infra/runtime/install-aws-signing-helper /tmp/install-aws-signing-helper
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=requirements /build/requirements.txt /tmp/orchestration-requirements.txt
RUN uv pip install --system --no-cache \
        --requirement /tmp/orchestration-requirements.txt \
    && python -c "from airflow.providers.amazon.aws.operators.emr import EmrServerlessStartJobOperator; from airflow.providers.docker.operators.docker import DockerOperator" \
    && /tmp/install-aws-signing-helper "$TARGETARCH" \
    && install -d -o airflow -g 0 -m 0770 /opt/airflow/auth \
    && rm /tmp/install-aws-signing-helper /tmp/orchestration-requirements.txt /usr/local/bin/uv

COPY --chown=airflow:0 orchestration /opt/airflow/orchestration

USER airflow
CMD ["airflow", "--help"]
