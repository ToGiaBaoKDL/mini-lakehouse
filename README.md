# Mini Lakehouse

Mini Lakehouse is a production-shaped, open-source data platform for learning from and running a
modern lakehouse without hiding the operational details. It combines an AWS data plane with a
small self-hosted OCI runtime and keeps infrastructure, data contracts, processing, analytics, and
applications independently deployable.

The included reference workloads ingest GitHub Archive and ArXiv data, extract PDF content, build
Iceberg tables and dbt models, and expose the results through Lightdash and ArXiv Lens.

## What is included

- Infrastructure as code for AWS, OCI, Tailscale, Cloudflare, and GitHub Actions.
- Contract-driven Glue and Apache Iceberg catalogs defined in YAML.
- Spark ingestion and transformation on EMR Serverless.
- Self-hosted Airflow with versioned DAG bundles and isolated task images.
- Modal-native GLM-OCR extraction with pinned models and durable curated artifacts.
- Athena and dbt analytics projects for engineering and research datasets.
- Self-hosted Lightdash and a read-only ArXiv inspection application.
- Immutable image and job releases, workload-scoped identities, and reproducible local checks.

## Architecture

```mermaid
flowchart LR
    S[GitHub Archive and ArXiv] --> A[Airflow]
    A --> E[EMR Serverless]
    A --> O[Document extraction]
    E --> I[(S3 + Iceberg)]
    O --> I
    I --> Q[Athena + dbt]
    Q --> D[Lightdash and data apps]
    G[Glue Data Catalog] --- I
    T[Terraform + YAML contracts] -. manages .-> G
    C[GitHub Actions] -. releases .-> A & E & O & D
```

AWS owns the durable data plane. A private OCI A1 host runs Airflow, PostgreSQL, Lightdash, and the
application services behind Tailscale and Cloudflare Access. GPU OCR is optional and remote; the
GLM-OCR runs on a scale-to-zero Modal GPU runtime.

## Demo

### Airflow orchestration

![Airflow DAGs orchestrating ingestion, document extraction, maintenance, and analytics](docs/assets/airflow-dags.png)

### Lightdash analytics

![Lightdash Engineering dashboard showing GitHub activity metrics and trends](docs/assets/lightdash-engineering-dashboard.png)

### ArXiv Lens

![ArXiv Lens showing an annotated PDF page alongside extracted Markdown](docs/assets/arxiv-lens.png)

## Repository layout

| Path | Purpose |
|---|---|
| `lakehouse/catalog/` | PyIceberg catalog control plane |
| `lakehouse/contracts/` | YAML contracts for sources, curated products, and analytics domains |
| `lakehouse/emr/` | Spark source ingestion, curated transformations, and maintenance jobs |
| `automation/airflow/` | Airflow runtime, DAG bundle, and deployment |
| `ocr-engine/` | Local CLI and scale-to-zero Modal GLM-OCR runtime |
| `arxiv-lens/` | Read-only Streamlit application and its deployment boundary |
| `analytics/dbt-project/` | Athena analytics models and their isolated runtime |
| `analytics/lightdash/` | Domain-owned BI content and Lightdash runtime/deployment |
| `sysops/` | Observability runtime, telemetry collection, dashboards, and alerts |
| `infra/` | Terraform, shared services, delivery, and operations |

Ownership follows these boundaries: Terraform provisions infrastructure, YAML contracts own the
catalog, Spark and OCR own curated data, dbt owns analytics tables, Lightdash owns BI semantics and
content, each top-level application owns its runtime, and Airflow only orchestrates.

## Getting started

Explore the available development and operations commands:

```bash
make help
```

Run the complete local quality gate before submitting a change:

```bash
make check
```

The canonical deployment and operations guide is [infra/README.md](infra/README.md). It covers the
bootstrap order, cloud resources, workload identities, secrets, releases, verification, and
day-two operations. Stable configuration lives in Make, Compose, Terraform, or YAML—not `.env`
files.

## Contributing

Issues, documentation improvements, new sources, and focused pull requests are welcome. Keep
changes within the owning component, add tests for behavior and contracts, run `make check`, and
never commit credentials or generated cloud state.

## License

Licensed under the [Apache License 2.0](LICENSE).
