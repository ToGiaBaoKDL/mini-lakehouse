# Orchestration

Airflow is self-hosted with LocalExecutor, PostgreSQL metadata, a dedicated DAG processor, and a
triggerer. EMR tasks use the official deferrable operator, cancel remote runs when killed, expose
application UI links, and use deterministic client tokens.

DAG files are grouped by domain and follow:

```text
[job_type]_[worker_type]_[verb]_[scope].py
```

The vocabulary is intentionally bounded:

| Segment | Values |
|---|---|
| `job_type` | `etl` extract/transform/load; `el` extract/load; `tl` transform/load or terminal processing; `rpt` reporting; `mon` monitoring; `bk` backup; `gov` governance and table lifecycle; `test` experiments |
| `worker_type` | `afw` Airflow worker; `docker` Docker container; `emr` EMR Serverless; `k8spod` Kubernetes pod; `sparkonk8s` SparkApplication; `glue` AWS Glue; `mix` multiple backends |
| `verb` | One present-tense action such as `ingest`, `build`, or `maintain` |
| `scope` | Stable business/data scope; it may contain underscores |

Current production DAG IDs are `etl_emr_ingest_github_archive`,
`etl_emr_ingest_arxiv_metadata`, `tl_docker_build_analytics`, and
`gov_emr_maintain_iceberg`. Runtime technologies belong in `worker_type`, not in
the business scope. Renames are direct cutovers without compatibility DAGs; old
run history remains attached to the retired DAG ID in Airflow metadata.

The current daily EMR jobs accept an optional `source_date`; scheduled runs default to T-1 in
`Asia/Ho_Chi_Minh`. GitHub Archive runs at 07:30 and ArXiv metadata at 11:00. DAG and task failures
use official Slack and SMTP notifiers backed by Airflow connections in AWS Secrets Manager.
