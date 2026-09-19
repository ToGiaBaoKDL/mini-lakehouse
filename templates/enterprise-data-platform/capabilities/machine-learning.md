# Machine Learning Data

Enable this capability when a trained or rules-based model consumes governed features or produces
decisions that must be reproduced. A feature store or model registry is not required until its
specific coordination or latency benefit is measured.

## Contract

- Offline feature snapshots include entity key, event time, availability time, feature version,
  source lineage, and configuration hash.
- Labels and training sets use point-in-time joins and never observe information unavailable at the
  decision instant.
- Training, evaluation, shadow, and serving use the same feature definitions or prove parity.
- Dataset, code, parameters, environment, model, evaluation, and approval are versioned.
- Promotion criteria include data quality, leakage checks, performance by relevant segments,
  operational limits, and rollback.
- Decisions preserve model/strategy version, inputs or feature snapshot, result, abstention/reason,
  and processing time.

## Required proof

- golden point-in-time fixtures around late-arriving data and label horizons;
- deterministic training/evaluation dataset reconstruction;
- offline/online or replay/live feature parity;
- baseline comparison, temporal validation, drift and calibration assessment where applicable;
- shadow result and rollback before automated business action;
- access and retention controls for sensitive training data.

Keep offline durable features in governed analytical storage. Add online storage only when the
serving SLO cannot be met by the current deterministic runtime.

