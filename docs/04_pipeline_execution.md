# Orchestration

Airflow is self-hosted with LocalExecutor, PostgreSQL metadata, a dedicated DAG processor, and a
triggerer. EMR tasks use the official deferrable operator, cancel remote runs when killed, expose
application UI links, and use deterministic client tokens.

DAG files are grouped by domain and follow:

```text
[job_type]_[worker_type]_[description].py
```

The current daily EMR jobs accept an optional `source_date`; scheduled runs default to T-1 in
`Asia/Ho_Chi_Minh`. GitHub Archive runs at 08:00 and ArXiv metadata at 10:00. DAG and task failures
use official Slack and SMTP notifiers backed by Airflow connections in AWS Secrets Manager.
