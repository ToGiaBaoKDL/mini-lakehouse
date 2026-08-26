# Mini Lakehouse

Mini Lakehouse is a production-shaped data platform with an AWS lakehouse data plane and a small
self-hosted service plane. It keeps infrastructure, catalog contracts, processing, analytics,
orchestration, observability, OCR, and applications independently owned inside one monorepo.

Read the canonical documentation at
[mini-lakehouse-docs.tgblab.io.vn](https://mini-lakehouse-docs.tgblab.io.vn).

## Repository map

| Path | Capability |
|---|---|
| `lakehouse/` | Contracts, Glue/Iceberg catalog control, and EMR jobs |
| `automation/airflow/` | Versioned Airflow orchestration |
| `analytics/` | dbt and Lightdash |
| `infra/` | AWS, OCI, Tailscale, Cloudflare, GitHub, and shared runtime foundations |
| `sysops/` | SigNoz and OpenTelemetry |
| `ocr-engine/` | Local OCR CLI and Modal GPU runtime |
| `arxiv-lens/` | Read-only ArXiv and OCR inspection |
| `docs/` | Fumadocs source and static documentation deployment |

```bash
make help
make check
```

Licensed under the [Apache License 2.0](LICENSE).
