# Operational checks

- If a DAG does not parse, check the scheduler and DAG processor logs separately.
- If an EMR task waits, inspect the linked job run; the Airflow task is deferrable and should not
  occupy a worker slot.
- If a Spark job reports a missing table, run contract apply/validate before republishing data.
- If contract validation reports structural drift, use an explicit migration; never add an
  automatic fallback that hides schema or partition differences.
- If Athena cannot read results, verify the workload profile/role, workgroup, KMS grant, and result
  prefix rather than introducing static credentials.
